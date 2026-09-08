"""Durable, reconnectable background execution for conversation turns."""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Mapping

from pydantic import ValidationError

from xnobrain.models.conversations import ChatRequest
from xnobrain.runtime_limits import session_timeout_seconds

from .base import ServiceError

TERMINAL_STATUSES = frozenset({"completed", "failed", "timed_out", "cancelled"})
BACKGROUND_HINT = re.compile(
    r"\b(pdf|report|research|survey|workspace|paper|papers|kdd|neurips|nips|icml|iclr|acl|"
    r"delegate|subagent|multi[- ]?step|dataset|benchmark|implementation|build|compile)\b",
    re.IGNORECASE,
)


@dataclass
class _ActiveConversationRun:
    run_id: str
    agent_id: str
    conversation_id: str
    task: asyncio.Task | None = None
    changed: asyncio.Event = field(default_factory=asyncio.Event)


class ConversationRunService:
    """Own chat execution independently from any browser SSE connection."""

    def __init__(self, repository, agents, analytics=None):
        self.repository = repository
        self.agents = agents
        self.analytics = analytics
        self._active: dict[str, _ActiveConversationRun] = {}
        self._by_conversation: dict[tuple[str, str], str] = {}

    @staticmethod
    def _reject_conflicting_context(
        body: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> None:
        aliases = {
            "ownership_context": context,
            "work_context_id": context.get("id"),
            "owner_kind": context.get("owner_kind"),
            "organization_id": context.get("organization_id"),
            "payer_kind": context.get("payer_kind"),
            "sponsor_grant_id": context.get("sponsor_grant_id"),
        }
        for field, expected in aliases.items():
            if field not in body:
                continue
            supplied = body.get(field)
            if supplied != expected:
                raise ServiceError(
                    "run ownership or payer conflicts with the conversation binding",
                    status=409,
                    code="conversation_context_conflict",
                )
            raise ServiceError(
                "run ownership and payer are loaded from the conversation",
                code="conversation_context_not_accepted",
            )

    async def start_run(
        self,
        agent_id: str,
        conversation_id: str,
        body: Mapping[str, Any],
        *,
        ownership_context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.agents.get_conversation(agent_id, conversation_id)
        context = dict(ownership_context or {})
        self._reject_conflicting_context(body, context)
        try:
            selection = ChatRequest(
                input=str(body.get("input") or body.get("message") or " "),
                feature=body.get("feature"),
                capabilities=body.get("capabilities"),
            )
        except ValidationError as exc:
            raise ServiceError(
                "invalid composer capability selection",
                status=422,
                code="invalid_capabilities",
            ) from exc
        capabilities = selection.capabilities
        if capabilities is None:
            capabilities = [selection.feature] if selection.feature else []
        key = (str(agent_id), str(conversation_id))
        active_id = self._by_conversation.get(key)
        if active_id and active_id in self._active:
            raise ServiceError(
                "a response is already running for this conversation",
                status=409,
                code="conversation_running",
            )
        # Heal a record left non-terminal by a previous runtime process before
        # accepting a new writer for the same conversation.
        if self.active_run(agent_id, conversation_id) is not None:
            raise ServiceError(
                "a response is already running for this conversation",
                status=409,
                code="conversation_running",
            )
        # This is the only enforcement point. Once accepted, a run is allowed
        # to finish even when its cost pushes the weekly total over 100%.
        if self.analytics is not None:
            await self.analytics.require_execution_budget(agent_id)
        # Budget verification yields to the event loop. Another request may
        # have claimed this conversation while it was pending. Recheck before
        # the synchronous record/worker registration critical section below.
        active_id = self._by_conversation.get(key)
        if (active_id and active_id in self._active) or self.active_run(
            agent_id, conversation_id
        ) is not None:
            raise ServiceError(
                "a response is already running for this conversation",
                status=409,
                code="conversation_running",
            )
        mode = self._mode(body)
        timeout_seconds = session_timeout_seconds(body.get("timeout_seconds"))
        now = time.time()
        run_id = "run_" + uuid.uuid4().hex
        record = {
            "id": run_id,
            "agent_id": str(agent_id),
            "conversation_id": str(conversation_id),
            "status": "queued",
            "mode": mode,
            "timeout_seconds": timeout_seconds,
            "created_at": now,
            "started_at": None,
            "ended_at": None,
            "updated_at": now,
            "last_activity_at": now,
            "deadline_at": None,
            "revision": 0,
            "error": None,
            "output": "",
            "usage": {},
            "ownership_context": context,
            "composer_selection": {
                "schema_version": 1,
                "feature": selection.feature,
                "capabilities": list(capabilities),
            },
        }
        self.repository.put_conversation_run(record)
        entry = _ActiveConversationRun(run_id, str(agent_id), str(conversation_id))
        self._active[run_id] = entry
        self._by_conversation[key] = run_id
        payload = dict(body)
        if selection.capabilities is not None:
            payload["capabilities"] = list(selection.capabilities)
        payload["timeout_seconds"] = timeout_seconds
        payload["run_id"] = run_id
        payload["run_mode"] = mode
        payload["conversation_id"] = str(conversation_id)
        payload["ownership_context"] = context
        task = asyncio.create_task(self._drive(record, payload), name=f"conversation-run-{run_id}")
        entry.task = task
        task.add_done_callback(lambda _task, rid=run_id: self._deregister(rid))
        return dict(record)

    def get_run(self, agent_id: str, conversation_id: str, run_id: str) -> dict[str, Any]:
        return self._heal_if_stale(
            self.repository.get_conversation_run(agent_id, conversation_id, run_id)
        )

    def active_run(self, agent_id: str, conversation_id: str) -> dict[str, Any] | None:
        for record in self.repository.list_conversation_runs(agent_id, conversation_id, limit=20):
            current = self._heal_if_stale(record)
            if current.get("status") not in TERMINAL_STATUSES:
                return current
        return None

    async def cancel_run(self, agent_id: str, conversation_id: str, run_id: str) -> dict[str, Any]:
        record = self.get_run(agent_id, conversation_id, run_id)
        if record.get("status") in TERMINAL_STATUSES:
            raise ServiceError("run has already finished", status=409, code="run_already_finished")
        entry = self._active.get(run_id)
        if entry is None or entry.agent_id != agent_id or entry.conversation_id != conversation_id:
            return self._mark_terminal(record, "cancelled", "run is no longer attached to a worker")
        if entry.task and not entry.task.done():
            entry.task.cancel()
            with suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(entry.task), timeout=10)
        return self.get_run(agent_id, conversation_id, run_id)

    async def events(
        self,
        agent_id: str,
        conversation_id: str,
        run_id: str,
        after: int = 0,
    ) -> AsyncIterator[dict[str, Any]]:
        cursor = max(0, int(after or 0))
        while True:
            record = self.get_run(agent_id, conversation_id, run_id)
            pending = self.repository.list_conversation_run_events(
                agent_id,
                conversation_id,
                run_id,
                cursor,
            )
            for event in pending:
                cursor = max(cursor, int(event.get("sequence") or 0))
                yield event
            if record.get("status") in TERMINAL_STATUSES and cursor >= int(
                record.get("revision") or 0
            ):
                return
            entry = self._active.get(run_id)
            if entry is None:
                await asyncio.sleep(0.25)
                continue
            entry.changed.clear()
            try:
                await asyncio.wait_for(entry.changed.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass

    async def legacy_stream(
        self,
        agent_id: str,
        conversation_id: str,
        body: Mapping[str, Any],
        *,
        ownership_context: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[bytes]:
        record = await self.start_run(
            agent_id,
            conversation_id,
            body,
            ownership_context=ownership_context,
        )
        async for event in self.events(agent_id, conversation_id, str(record["id"])):
            yield self._sse(event)
        yield b"data: [DONE]\n\n"

    async def shutdown(self) -> None:
        tasks = [
            entry.task for entry in self._active.values() if entry.task and not entry.task.done()
        ]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError, Exception):
                await task

    def registry_entry(self, run_id: str) -> _ActiveConversationRun | None:
        return self._active.get(run_id)

    @staticmethod
    def _mode(body: Mapping[str, Any]) -> str:
        requested = str(body.get("run_mode") or "auto").strip().lower()
        if requested in {"interactive", "background"}:
            return requested
        message = str(body.get("input") or body.get("message") or "")
        return "background" if BACKGROUND_HINT.search(message) else "interactive"

    async def _drive(self, initial: Mapping[str, Any], body: Mapping[str, Any]) -> None:
        record = dict(initial)
        run_id = str(record["id"])
        try:
            buffer = ""
            async for chunk in self.agents.chat_stream(record["agent_id"], body):
                buffer += chunk.decode("utf-8", errors="replace")
                frames, buffer = self._frames(buffer)
                for frame in frames:
                    event = self._parse_frame(frame)
                    if event is None:
                        continue
                    record = self._append(record, event)
            if buffer.strip():
                event = self._parse_frame(buffer)
                if event is not None:
                    record = self._append(record, event)
            if record.get("status") not in TERMINAL_STATUSES:
                self._mark_terminal(record, "failed", "run ended without a completion event")
        except asyncio.CancelledError:
            if record.get("status") not in TERMINAL_STATUSES:
                self._mark_terminal(record, "cancelled", "run cancelled")
            raise
        except Exception as error:
            self._mark_terminal(record, "failed", str(error) or "conversation run failed")
        finally:
            entry = self._active.get(run_id)
            if entry is not None:
                entry.changed.set()

    def _append(self, record: Mapping[str, Any], event: Mapping[str, Any]) -> dict[str, Any]:
        now = time.time()
        payload = (
            dict(event.get("data") or {})
            if isinstance(event.get("data"), Mapping)
            else event.get("data")
        )
        event_name = str(
            payload.get("event")
            if isinstance(payload, Mapping)
            else event.get("event") or "message"
        )
        next_record = dict(record)
        if isinstance(payload, dict) and event_name == "run.started":
            started = float(payload.get("timestamp") or now)
            next_record.update(
                {
                    "status": "running",
                    "started_at": started,
                    "deadline_at": started + int(next_record["timeout_seconds"]),
                }
            )
            payload.update(
                {
                    "durable": True,
                    "run_mode": next_record["mode"],
                    "timeout_seconds": next_record["timeout_seconds"],
                    "deadline_at": next_record["deadline_at"],
                    "ownership_context": next_record.get("ownership_context") or {},
                }
            )
        elif isinstance(payload, Mapping) and event_name == "run.completed":
            next_record.update(
                {
                    "status": "completed",
                    "output": str(payload.get("output") or ""),
                    "usage": dict(payload.get("usage") or {}),
                    "ended_at": float(payload.get("timestamp") or now),
                }
            )
        elif isinstance(payload, Mapping) and event_name == "run.cancelled":
            next_record.update(
                {"status": "cancelled", "ended_at": float(payload.get("timestamp") or now)}
            )
        elif isinstance(payload, Mapping) and event_name == "run.failed":
            message = str(payload.get("message") or "conversation run failed")
            next_record.update(
                {
                    "status": "timed_out" if "timed out" in message.lower() else "failed",
                    "error": message,
                    "ended_at": float(payload.get("timestamp") or now),
                }
            )
        stored = self.repository.append_conversation_run_event(
            next_record["agent_id"],
            next_record["conversation_id"],
            next_record["id"],
            {"event": str(event.get("event") or "message"), "data": payload},
        )
        next_record.update(
            {
                "revision": int(stored["sequence"]),
                "updated_at": now,
                "last_activity_at": now,
            }
        )
        self.repository.put_conversation_run(next_record)
        entry = self._active.get(str(next_record["id"]))
        if entry is not None:
            entry.changed.set()
        return next_record

    def _mark_terminal(
        self, record: Mapping[str, Any], status: str, message: str
    ) -> dict[str, Any]:
        event_name = "run.cancelled" if status == "cancelled" else "run.failed"
        payload: dict[str, Any] = {
            "event": event_name,
            "run_id": record["id"],
            "timestamp": time.time(),
        }
        if event_name == "run.failed":
            payload["message"] = message
        updated = self._append(record, {"event": "message", "data": payload})
        if status == "timed_out":
            updated["status"] = "timed_out"
            self.repository.put_conversation_run(updated)
        return updated

    def _heal_if_stale(self, record: Mapping[str, Any]) -> dict[str, Any]:
        current = dict(record)
        if current.get("status") in TERMINAL_STATUSES or str(current.get("id")) in self._active:
            return current
        return self._mark_terminal(current, "failed", "run interrupted by runtime restart")

    def _deregister(self, run_id: str) -> None:
        entry = self._active.pop(run_id, None)
        if entry is not None:
            key = (entry.agent_id, entry.conversation_id)
            if self._by_conversation.get(key) == run_id:
                self._by_conversation.pop(key, None)

    @staticmethod
    def _frames(buffer: str) -> tuple[list[str], str]:
        frames: list[str] = []
        while True:
            match = re.search(r"\r?\n\r?\n", buffer)
            if match is None:
                return frames, buffer
            frames.append(buffer[: match.start()])
            buffer = buffer[match.end() :]

    @staticmethod
    def _parse_frame(frame: str) -> dict[str, Any] | None:
        event_name = "message"
        data_lines: list[str] = []
        for raw_line in frame.splitlines():
            if raw_line.startswith("event:"):
                event_name = raw_line[6:].strip() or "message"
            elif raw_line.startswith("data:"):
                data_lines.append(raw_line[5:].lstrip())
        if not data_lines:
            return None
        raw_data = "\n".join(data_lines)
        if raw_data == "[DONE]":
            return None
        try:
            data: Any = json.loads(raw_data)
        except json.JSONDecodeError:
            data = raw_data
        return {"event": event_name, "data": data}

    @staticmethod
    def _sse(event: Mapping[str, Any]) -> bytes:
        sequence = int(event.get("sequence") or 0)
        name = str(event.get("event") or "message")
        data = json.dumps(event.get("data"), ensure_ascii=False, separators=(",", ":"))
        return f"id: {sequence}\nevent: {name}\ndata: {data}\n\n".encode()
