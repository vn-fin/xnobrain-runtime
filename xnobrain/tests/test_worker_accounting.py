"""Worker credential, attribution and admission regressions without provider calls."""

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from xnobrain.integrations.accounting_context import current_accounting, inference_accounting
from xnobrain.integrations.hermes_commands import HermesCommandsMixin
from xnobrain.integrations.kanban_workers import KanbanWorkerSpawner
from xnobrain.integrations.worker_cli import install_worker_agent


class WorkerAccountingTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_worker_and_child_headers_keep_same_user_and_agent(self):
        class NativeAgent:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.session_id = kwargs.get("session_id", "session")
                self.client = SimpleNamespace(_custom_headers={})
                self._build_api_kwargs = lambda messages: {"messages": messages}

            def run_conversation(self, **kwargs):
                return {"failed": kwargs.get("failed", False)}

        with (
            patch.dict(sys.modules, {"run_agent": SimpleNamespace(AIAgent=NativeAgent)}),
            patch.dict(os.environ, {"RUNTIME_LLM_ROUTER_URL": "http://router/v1"}),
        ):
            install_worker_agent({"user_key": "synthetic", "agent_id": "worker"}, "run", "team")
            agent = NativeAgent(session_id="session")
            child = NativeAgent(session_id="child")
            for instance in (agent, child):
                request = await asyncio.to_thread(instance._build_api_kwargs, [])
                self.assertEqual(request["extra_headers"]["X-GoRouter-Agent-Id"], "worker")
                self.assertEqual(request["extra_headers"]["X-GoRouter-Parent-Run-Id"], "team")
                self.assertEqual(
                    request["extra_headers"]["X-GoRouter-Conversation-Id"], instance.session_id
                )
                self.assertNotIn("X-GoRouter-Request-Id", request["extra_headers"])
                self.assertEqual(instance.kwargs["api_key"], "synthetic")
            with self.assertRaises(RuntimeError):
                agent.run_conversation(failed=True)
        self.assertIsNone(current_accounting())

    async def test_spawn_budget_failure_never_starts_process(self):
        agents = SimpleNamespace(_agent_name=lambda name: "big-brother")
        analytics = SimpleNamespace(require_execution_budget=AsyncMock(side_effect=RuntimeError()))
        spawner = KanbanWorkerSpawner(agents, analytics, asyncio.get_running_loop())
        with patch("xnobrain.integrations.kanban_workers.subprocess.Popen") as spawn:
            with self.assertRaisesRegex(RuntimeError, "budget"):
                await asyncio.to_thread(
                    spawner, SimpleNamespace(assignee="default"), "/tmp", board="test"
                )
        spawn.assert_not_called()
        analytics.require_execution_budget.assert_awaited_once_with("big-brother")

    async def test_spawn_freezes_key_and_keeps_native_claim_goal_and_board(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            agents = SimpleNamespace(
                _agent_name=lambda name: "big-brother",
                profile_path=lambda name: profile,
                _ensure_router_profile=Mock(),
                _command_env=lambda *args: {},
            )
            analytics = SimpleNamespace(require_execution_budget=AsyncMock())
            task = SimpleNamespace(
                assignee="default",
                id="t_test",
                current_run_id=7,
                claim_lock="claim",
                tenant=None,
                branch_name=None,
                goal_mode=True,
                goal_max_turns=2,
                max_runtime_seconds=30,
                model_override="provider/model",
                skills=["verify"],
            )
            kb = SimpleNamespace(
                kanban_db_path=lambda **kw: profile / "kanban.db",
                workspaces_root=lambda **kw: profile,
                worker_logs_dir=lambda **kw: profile,
                _worker_terminal_timeout_env=lambda *args: "30",
                _resolve_worker_cli_toolsets=lambda *args: ["file"],
                _rotate_worker_log=Mock(),
                worker_log_rotation_config=lambda: (10000, 1),
            )
            with (
                patch.dict(os.environ, {"RUNTIME_ACCOUNTING_MODE": "router"}),
                patch("xnobrain.integrations.kanban_workers._module", return_value=kb),
                patch(
                    "xnobrain.integrations.kanban_workers.accounting_binding",
                    return_value={"user_key": "synthetic"},
                ),
                patch(
                    "xnobrain.integrations.kanban_workers.subprocess.Popen",
                    return_value=SimpleNamespace(pid=123),
                ) as spawn,
            ):
                result = await asyncio.to_thread(
                    KanbanWorkerSpawner(agents, analytics, asyncio.get_running_loop()),
                    task,
                    directory,
                    board="board",
                )
            self.assertEqual(result, 123)
            argv = spawn.call_args.args[0]
            env = spawn.call_args.kwargs["env"]
            self.assertNotIn("synthetic", str(argv))
            self.assertIn("xnobrain.integrations.worker_cli", argv)
            self.assertIn("-Q", argv)
            self.assertEqual(env["HERMES_KANBAN_CLAIM_LOCK"], "claim")
            self.assertEqual(env["HERMES_PROFILE"], "default")
            self.assertEqual(env["RUNTIME_EXECUTION_AGENT_ID"], "big-brother")
            self.assertEqual(env["RUNTIME_LLM_API_KEY_FILE"], "")
            self.assertEqual(env["RUNTIME_EXECUTION_RUN_ID"], "kanban/board/t_test/7")

    async def test_child_credentials_cannot_be_overridden_by_legacy_file(self):
        class Commands(HermesCommandsMixin):
            def _load_agent_credentials(self, env):
                env["RUNTIME_LLM_API_KEY"] = "stale"

        with (
            patch.dict(os.environ, {"RUNTIME_LLM_API_KEY": "old", "RUNTIME_LLM_API_KEY_FILE": ""}),
            inference_accounting(
                {"user_key": "accepted", "agent_id": "a"}, run_id="run", parent_run_id="team"
            ),
        ):
            env = Commands()._command_env(Path("/tmp"), "xnobrain")
            self.assertEqual(env["RUNTIME_LLM_API_KEY"], "accepted")
            self.assertEqual(env["RUNTIME_EXECUTION_PARENT_RUN_ID"], "team")
            self.assertEqual(os.environ["RUNTIME_LLM_API_KEY"], "old")

    async def test_large_oneshot_input_uses_pipe_not_argv(self):
        from xnobrain.integrations.hermes import AgentManager

        prepared = {
            "model": "provider/model",
            "name": "a",
            "profile_dir": Path("/tmp"),
            "command": ["hermes", "-z", "sensitive"],
            "message": "large" * 40000,
            "timeout_seconds": 30,
        }
        manager = object.__new__(AgentManager)
        manager._prepare_chat_command = Mock(return_value=prepared)
        contexts = []
        manager._resolve_prepared_smart_route = AsyncMock(
            side_effect=lambda prepared: contexts.append(current_accounting())
        )
        manager._latest_session_ids = Mock(return_value=[])
        manager._detect_changed_session = Mock(return_value=None)
        manager._mark_agent_active = Mock()
        manager._mark_agent_idle = Mock()
        manager._workspace_dir = Mock(return_value=Path("/tmp"))

        def execute(*args, **kwargs):
            contexts.append(current_accounting())
            return {"exit_code": 0, "stdout": "verified", "stderr": ""}

        manager._run_profile_command = AsyncMock(side_effect=execute)
        with patch.dict(
            os.environ,
            {
                "RUNTIME_ACCOUNTING_MODE": "router",
                "RUNTIME_LLM_API_KEY": "synthetic",
                "RUNTIME_LLM_API_KEY_FILE": "",
            },
        ):
            await manager.chat("a", {"parent_run_id": "team"})
        call = manager._run_profile_command.call_args
        self.assertNotIn("sensitive", str(call.args))
        self.assertIn("--xnobrain-input-stdin", call.args[1])
        self.assertEqual(call.kwargs["input_bytes"], prepared["message"].encode())
        self.assertEqual(len(contexts), 2)
        self.assertEqual(contexts[0], contexts[1])
        self.assertEqual(contexts[0]["headers"]["X-GoRouter-Agent-Id"], "a")
        self.assertEqual(contexts[0]["headers"]["X-GoRouter-Parent-Run-Id"], "team")
        self.assertIsNone(current_accounting())

    async def test_root_alias_dispatches_without_legacy_named_directory(self):
        from hermes_cli import profiles

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(profiles, "profile_exists", return_value=False) as native,
        ):
            with patch.dict(os.environ, {"HERMES_HOME": directory}):
                KanbanWorkerSpawner(None, None, asyncio.get_running_loop())
                self.assertTrue(profiles.profile_exists("big-brother"))
                self.assertFalse(profiles.profile_exists("missing"))
                native.assert_called_once_with("missing")

    async def test_kanban_reaper_never_waits_on_team_or_other_children(self):
        spawner = KanbanWorkerSpawner(None, None, asyncio.get_running_loop())
        process = SimpleNamespace(returncode=None)
        spawner._workers[123] = process
        with (
            patch("xnobrain.integrations.kanban_workers.os.waitpid", return_value=(123, 0)) as wait,
            patch("xnobrain.integrations.kanban_workers._module") as module,
        ):
            self.assertEqual(spawner.reap_workers(), [123])
            wait.assert_called_once_with(123, os.WNOHANG)
            module.return_value._record_worker_exit.assert_called_once_with(123, 0)
        self.assertEqual(process.returncode, 0)
        self.assertEqual(spawner._workers, {})
