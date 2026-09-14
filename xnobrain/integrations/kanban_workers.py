"""Accounted spawn adapter; native Kanban retains claims, recovery and transitions."""

import asyncio
import os
import subprocess
import sys
import threading
from pathlib import Path

from .accounting_context import accounting_binding, accounting_enabled
from .kanban_support import _module


class KanbanWorkerSpawner:
    def __init__(self, agents, analytics, loop):
        self.agents = agents
        self.analytics = analytics
        self.loop = loop
        self._workers = {}
        self._workers_lock = threading.Lock()
        self._install_root_alias()
        # Native waitpid(-1) steals exit statuses from asyncio/uvloop Teams
        # subprocesses in this shared host. Reap only our own spawned workers.
        _module().reap_worker_zombies = self.reap_workers

    def _install_root_alias(self):
        # The native dispatcher validates names before calling spawn_fn. The
        # public root agent has no named profile directory on clean installs.
        from hermes_cli import profiles

        if getattr(profiles.profile_exists, "_xnobrain_root_alias", False) is True:
            return
        native_exists = profiles.profile_exists

        def profile_exists(name):
            if str(name).strip().casefold() == "big-brother":
                from hermes_constants import get_hermes_home

                return get_hermes_home().is_dir()
            return native_exists(name)

        profile_exists._xnobrain_root_alias = True
        profiles.profile_exists = profile_exists

    def reap_workers(self):
        reaped = []
        with self._workers_lock:
            for pid, process in list(self._workers.items()):
                try:
                    exited, status = os.waitpid(pid, os.WNOHANG)
                except ChildProcessError:
                    self._workers.pop(pid, None)
                    continue
                if not exited:
                    continue
                process.returncode = os.waitstatus_to_exitcode(status)
                _module()._record_worker_exit(pid, status)
                self._workers.pop(pid, None)
                reaped.append(pid)
        return reaped

    def __call__(self, task, workspace, *, board=None):
        kb = _module()
        agent_id = self.agents._agent_name(task.assignee)
        # dispatch_once runs in a thread; wait on the host's existing async
        # accounting client before spawning any worker (including review runs).
        check = asyncio.run_coroutine_threadsafe(
            self.analytics.require_execution_budget(agent_id), self.loop
        )
        try:
            check.result(timeout=10)
        except Exception:
            check.cancel()
            raise RuntimeError("worker budget admission unavailable or exceeded") from None
        profile = self.agents.profile_path(agent_id)
        self.agents._ensure_router_profile(profile)
        env = self.agents._command_env(profile, "xnobrain")
        env.update(
            HERMES_KANBAN_TASK=task.id,
            HERMES_KANBAN_WORKSPACE=workspace,
            HERMES_KANBAN_DB=str(kb.kanban_db_path(board=board)),
            HERMES_KANBAN_BOARD=board or kb.get_current_board(),
            HERMES_KANBAN_WORKSPACES_ROOT=str(kb.workspaces_root(board=board)),
            HERMES_PROFILE="default" if agent_id == "big-brother" else agent_id,
            TERMINAL_CWD=workspace,
        )
        env.pop("HERMES_TUI", None)
        if task.current_run_id is not None:
            env["HERMES_KANBAN_RUN_ID"] = str(task.current_run_id)
        if task.claim_lock:
            env["HERMES_KANBAN_CLAIM_LOCK"] = task.claim_lock
        if task.tenant:
            env["HERMES_TENANT"] = task.tenant
        if task.branch_name:
            env["HERMES_KANBAN_BRANCH"] = task.branch_name
        if task.goal_mode:
            env["HERMES_KANBAN_GOAL_MODE"] = "1"
            if task.goal_max_turns is not None:
                env["HERMES_KANBAN_GOAL_MAX_TURNS"] = str(task.goal_max_turns)
        for name in ("TERMINAL_TIMEOUT", "TERMINAL_MAX_FOREGROUND_TIMEOUT"):
            value = kb._worker_terminal_timeout_env(task.max_runtime_seconds, env.get(name))
            if value is not None:
                env[name] = value
        if accounting_enabled():
            env["RUNTIME_LLM_API_KEY"] = accounting_binding(agent_id)["user_key"]
            env["RUNTIME_LLM_API_KEY_FILE"] = ""
            env["RUNTIME_EXECUTION_AGENT_ID"] = agent_id
            env["RUNTIME_EXECUTION_RUN_ID"] = (
                f"kanban/{board or 'default'}/{task.id}/{task.current_run_id}"
            )
            command = [sys.executable, "-m", "xnobrain.integrations.worker_cli"]
        else:
            command = kb._resolve_hermes_argv()
        command += ["--cli", "--accept-hooks"]
        if task.model_override:
            command += ["--model", task.model_override]
        for skill in task.skills or []:
            command += ["--skills", skill]
        tools = kb._resolve_worker_cli_toolsets(str(profile))
        if tools:
            command += ["--toolsets", ",".join(tools)]
        command += ["chat", "-q", f"work kanban task {task.id}"]
        if task.goal_mode:
            command.append("-Q")
        # Ensure the wrapper remains importable after chdir to the task workspace.
        root = str(Path(__file__).resolve().parents[2])
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (root, env.get("PYTHONPATH", ""))))
        log_path = kb.worker_logs_dir(board=board) / f"{task.id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        kb._rotate_worker_log(log_path, *kb.worker_log_rotation_config())
        with log_path.open("ab") as log:
            worker = subprocess.Popen(
                command,
                cwd=workspace,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        with self._workers_lock:
            self._workers[worker.pid] = worker
        return worker.pid
