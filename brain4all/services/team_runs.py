"""Persistent, observable, cancellable team runs over the existing DAG engine.

The DAG execution engine itself is unchanged Brain4All logic moved verbatim out
of ``PlatformService.run_team``; this service wraps it in a run lifecycle:
an on-disk run record per transition, background execution, live progress via a
"changed" pulse, cancellation that reaches the child ``hermes`` subprocesses, and
a lazy staleness rule for runs interrupted by a process restart.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
import logging
from typing import Any, Mapping
import uuid

from .platform import EXPECTED_ERRORS, ServiceError, iso


MAX_ACTIVE_TEAM_RUNS = 4
SUMMARY_CAP = 100_000
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})

logger = logging.getLogger(__name__)


@dataclass
class _ActiveRun:
    run_id: str
    team_id: str
    task: asyncio.Task | None = None
    changed: asyncio.Event = field(default_factory=asyncio.Event)


class TeamRunService:
    """Run registry + background engine around ``PlatformService``'s DAG workflow."""

    def __init__(self, repository, agents, platform):
        self.repository = repository
        self.agents = agents
        self.platform = platform
        self._active: dict[str, _ActiveRun] = {}
        self._by_team: dict[str, str] = {}

    # ---- public API -----------------------------------------------------

    async def start_run(self, team_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        team = self.platform.get_team(team_id)
        if not team.get("enabled", True):
            raise ServiceError("team is disabled", status=409, code="team_disabled")
        workflow = self.platform._build_team_workflow(team, body)
        self._guard_capacity(str(team["id"]))
        record = self._new_record(team, body, workflow, mode="async")
        self.repository.put_team_run(record)
        entry = self._register(record, task=None)
        task = asyncio.ensure_future(self._drive(record, team, workflow))
        entry.task = task
        task.add_done_callback(lambda _t, run_id=record["id"]: self._deregister(run_id))
        return record

    async def run_sync(self, team_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        team = self.platform.get_team(team_id)
        if not team.get("enabled", True):
            raise ServiceError("team is disabled", status=409, code="team_disabled")
        workflow = self.platform._build_team_workflow(team, body)
        self._guard_capacity(str(team["id"]))
        record = self._new_record(team, body, workflow, mode="sync")
        self.repository.put_team_run(record)
        self._register(record, task=asyncio.current_task())
        try:
            await self._drive(record, team, workflow)
        finally:
            self._deregister(record["id"])
        return record

    def get_run(self, team_id: str, run_id: str) -> dict[str, Any]:
        record = self.repository.get_team_run(team_id, run_id)
        return self._heal_if_stale(record)

    def list_runs(self, team_id: str, limit: Any = None) -> list[dict[str, Any]]:
        self.platform.get_team(team_id)
        rows = self.repository.list_team_runs(team_id, limit=self._clamp_limit(limit))
        return [self._summarize_for_list(self._heal_if_stale(record)) for record in rows]

    async def cancel_run(self, team_id: str, run_id: str) -> dict[str, Any]:
        self.platform.get_team(team_id)
        entry = self._active.get(run_id)
        if entry and entry.team_id == team_id and entry.task and not entry.task.done():
            entry.task.cancel()
            with suppress(asyncio.TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(entry.task), timeout=30)
            return self.get_run(team_id, run_id)
        record = self._heal_if_stale(self.repository.get_team_run(team_id, run_id))
        if record["status"] in TERMINAL_STATUSES:
            raise ServiceError("run has already finished", status=409, code="run_already_finished")
        return record

    def delete_run(self, team_id: str, run_id: str) -> dict[str, Any]:
        self.platform.get_team(team_id)
        record = self._heal_if_stale(self.repository.get_team_run(team_id, run_id))
        if record["status"] not in TERMINAL_STATUSES:
            raise ServiceError(
                "cancel the active run before deleting it",
                status=409,
                code="team_run_active",
            )
        if not self.repository.delete_team_run(team_id, run_id):
            raise ServiceError("team run not found", status=404, code="run_not_found")
        return {"id": run_id, "team_id": team_id, "deleted": True}

    async def shutdown(self) -> None:
        tasks = [entry.task for entry in list(self._active.values()) if entry.task and not entry.task.done()]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError, Exception):
                await task

    def registry_entry(self, run_id: str) -> _ActiveRun | None:
        return self._active.get(run_id)

    def sanitized(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """Full record with only whitelisted keys (records are built clean, so
        this is a defensive copy — never composed prompts, stderr, or reasoning)."""
        return dict(record)

    # ---- registry -------------------------------------------------------

    def _guard_capacity(self, team_id: str) -> None:
        if team_id in self._by_team:
            raise ServiceError("a run is already active for this team", status=409, code="team_run_active")
        if len(self._active) >= MAX_ACTIVE_TEAM_RUNS:
            raise ServiceError("too many active team runs", status=409, code="too_many_team_runs")

    def _register(self, record: Mapping[str, Any], task: asyncio.Task | None) -> _ActiveRun:
        entry = _ActiveRun(run_id=str(record["id"]), team_id=str(record["team_id"]), task=task)
        self._active[entry.run_id] = entry
        self._by_team[entry.team_id] = entry.run_id
        return entry

    def _deregister(self, run_id: str) -> None:
        entry = self._active.pop(run_id, None)
        if entry and self._by_team.get(entry.team_id) == run_id:
            self._by_team.pop(entry.team_id, None)

    # ---- record shaping -------------------------------------------------

    def _new_record(self, team: Mapping[str, Any], body: Mapping[str, Any], workflow: list[Mapping[str, Any]], *, mode: str) -> dict[str, Any]:
        now = iso()
        task = str(body.get("task") or "").strip()
        synthesis = str(body.get("synthesis") or "Synthesize these workflow results into one final answer.").strip()
        steps = [
            {
                "id": str(step["id"]),
                "agent_id": str(step["agent_id"]),
                "role": str(step["role"]),
                "task": str(step["task"]),
                "needs": [str(need) for need in step["needs"]],
                "allowed_tools": [str(tool) for tool in step["allowed_tools"]],
                "status": "pending",
                "summary": "",
                "summary_chars": 0,
                "error": None,
                "conversation_id": None,
                "started_at": None,
                "ended_at": None,
            }
            for step in workflow
        ]
        return {
            "id": "tr_" + uuid.uuid4().hex,
            "team_id": str(team["id"]),
            "status": "pending",
            "error": None,
            "mode": mode,
            "task": task,
            "synthesis_instruction": synthesis,
            "orchestrator_id": str(team["orchestrator_id"]),
            "orchestrator_summary": "",
            "created_at": now,
            "started_at": None,
            "ended_at": None,
            "updated_at": now,
            "revision": 0,
            "steps": steps,
        }

    def _summarize_for_list(self, record: Mapping[str, Any]) -> dict[str, Any]:
        trimmed = dict(record)
        trimmed["orchestrator_summary"] = ""
        steps = []
        for step in record["steps"]:
            item = dict(step)
            item["summary_chars"] = int(step.get("summary_chars") or len(step.get("summary") or ""))
            item["summary"] = ""
            steps.append(item)
        trimmed["steps"] = steps
        return trimmed

    @staticmethod
    def _clamp_limit(limit: Any) -> int:
        try:
            return max(1, min(100, int(limit)))
        except (TypeError, ValueError):
            return 20

    @staticmethod
    def _cap(text: str) -> str:
        return text[:SUMMARY_CAP] + "\n…[truncated]" if len(text) > SUMMARY_CAP else text

    # ---- persistence + staleness ---------------------------------------

    async def _persist(self, record: dict[str, Any]) -> None:
        record["revision"] = int(record.get("revision", 0)) + 1
        record["updated_at"] = iso()
        self.repository.put_team_run(record)
        entry = self._active.get(record["id"])
        if entry is not None:
            entry.changed.set()
            entry.changed.clear()

    def _heal_if_stale(self, record: dict[str, Any]) -> dict[str, Any]:
        if record.get("status") in {"pending", "running"} and record.get("id") not in self._active:
            record["status"] = "failed"
            record["error"] = "interrupted_by_restart"
            record["ended_at"] = iso()
            self._cancel_open_steps(record)
            record["revision"] = int(record.get("revision", 0)) + 1
            record["updated_at"] = iso()
            self.repository.put_team_run(record)
        return record

    @staticmethod
    def _cancel_open_steps(record: dict[str, Any]) -> None:
        for step in record["steps"]:
            if step["status"] not in {"completed", "failed"}:
                step["status"] = "cancelled"
                if step.get("ended_at") is None:
                    step["ended_at"] = iso()

    async def _finalize(self, record: dict[str, Any], status: str, *, error: str | None = None) -> None:
        record["status"] = status
        if error is not None:
            record["error"] = error
        record["ended_at"] = iso()
        if status in {"cancelled", "failed"}:
            self._cancel_open_steps(record)
        await self._persist(record)

    # ---- background driver + engine ------------------------------------

    async def _drive(self, record: dict[str, Any], team: Mapping[str, Any], workflow: list[Mapping[str, Any]]) -> None:
        try:
            record["status"] = "running"
            record["started_at"] = iso()
            await self._persist(record)
            summary = await self._execute_workflow(record, team, workflow)
            record["orchestrator_summary"] = self._cap(summary)
            await self._finalize(record, "completed")
        except asyncio.CancelledError:
            await self._finalize(record, "cancelled", error="cancelled")
            raise
        except EXPECTED_ERRORS as error:
            await self._finalize(record, "failed", error=str(getattr(error, "code", "worker_failed")))
        except Exception:  # noqa: BLE001 - never leak provider output; log the type only
            logger.exception("team run %s failed with an unexpected error", record["id"])
            await self._finalize(record, "failed", error="internal_error")

    async def _execute_workflow(self, record: dict[str, Any], team: Mapping[str, Any], workflow: list[Mapping[str, Any]]) -> str:
        semaphore = asyncio.Semaphore(max(1, int(team.get("max_parallel") or 1)))
        agent_locks: dict[str, asyncio.Lock] = {}
        steps_by_id = {str(step["id"]): step for step in record["steps"]}

        async def run_step(step: Mapping[str, Any], completed: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
            step_record = steps_by_id[str(step["id"])]
            agent_lock = agent_locks.setdefault(str(step["agent_id"]), asyncio.Lock())
            async with agent_lock, semaphore:
                step_record["status"] = "running"
                step_record["started_at"] = iso()
                await self._persist(record)
                upstream = []
                for dependency in step["needs"]:
                    result = completed[dependency]
                    value = result.get("summary") or f"Failed with {result.get('error', 'worker_failed')}"
                    upstream.append(f"[{dependency}] {value}")
                prompt = (
                    f"Role: {step['role']}\n"
                    "Do not ask for clarification or write memory. Return only a final summary.\n\n"
                    f"Task: {step['task']}"
                )
                if upstream:
                    prompt += "\n\nUpstream results:\n" + "\n\n".join(upstream)
                try:
                    result = await self.agents.chat(
                        str(step["agent_id"]),
                        {"message": prompt, "toolsets": list(step["allowed_tools"])},
                    )
                    summary = str(result.get("response") or "")
                    step_record["summary"] = self._cap(summary)
                    step_record["summary_chars"] = len(summary)
                    if result.get("conversation_id"):
                        step_record["conversation_id"] = str(result["conversation_id"])
                    step_record["status"] = "completed"
                    step_record["ended_at"] = iso()
                    await self._persist(record)
                    return {
                        "id": step["id"], "agent_id": step["agent_id"], "role": step["role"],
                        "needs": list(step["needs"]), "status": "completed", "summary": summary,
                    }
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    code = str(getattr(error, "code", "worker_failed"))
                    step_record["status"] = "failed"
                    step_record["error"] = code
                    step_record["ended_at"] = iso()
                    await self._persist(record)
                    return {
                        "id": step["id"], "agent_id": step["agent_id"], "role": step["role"],
                        "needs": list(step["needs"]), "status": "failed", "error": code,
                    }

        pending = {str(step["id"]): step for step in workflow}
        completed: dict[str, dict[str, Any]] = {}
        while pending:
            ready = [step for step in pending.values() if all(need in completed for need in step["needs"])]
            if not ready:
                raise ServiceError("workflow contains a dependency cycle", code="workflow_cycle")
            batch = await asyncio.gather(*(run_step(step, completed) for step in ready))
            for result in batch:
                completed[result["id"]] = result
                pending.pop(result["id"], None)

        results = [completed[str(step["id"])] for step in workflow]
        synthesis = record["synthesis_instruction"] + "\n\n" + "\n\n".join(
            f"[{item['id']}] {item['role']}: {item.get('summary') or item.get('error', 'worker_failed')}"
            for item in results
        )
        try:
            final = await self.agents.chat(team["orchestrator_id"], {"message": synthesis, "toolsets": ["todo"]})
        except asyncio.CancelledError:
            raise
        except EXPECTED_ERRORS as error:
            raise ServiceError("team synthesis failed", status=502, code="synthesis_failed") from error
        return str(final.get("response") or "")
