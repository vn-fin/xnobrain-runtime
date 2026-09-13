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
from xnobrain.runtime_limits import (
    concurrent_work_max_depth,
    concurrent_work_max_turns,
    max_parallel_agents,
    session_timeout_seconds,
)

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
        runtime_updates = getattr(self, "runtime_updates", None)
        if runtime_updates is not None:
            runtime_updates.require_dispatch()
        self.agents.get_conversation(agent_id, conversation_id)
        context = dict(ownership_context or {})
        self._reject_conflicting_context(body, context)
        context_state = str(context.get("state") or "active")
        if context_state != "active":
            raise ServiceError(
                "conversation context does not permit new execution",
                status=403,
                code="conversation_context_inactive",
            )
        try:
            selection = ChatRequest.model_validate(
                {
                    key: value
                    for key, value in body.items()
                    if key
                    in {
                        "input",
                        "model",
                        "skills",
                        "toolsets",
                        "timeout_seconds",
                        "run_mode",
                        "idempotency_key",
                        "feature",
                        "capabilities",
                    }
                }
                | {"input": str(body.get("input") or body.get("message") or " ")}
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
        idempotency_key = str(body.get("idempotency_key") or "").strip()
        fingerprint = self.repository.conversation_run_fingerprint(body)

        def replay_idempotent() -> dict[str, Any] | None:
            if not idempotency_key:
                return None
            existing = self.repository.find_conversation_run_by_idempotency(
                agent_id,
                conversation_id,
                idempotency_key,
            )
            if existing is None:
                return None
            if existing.get("request_fingerprint") != fingerprint:
                raise ServiceError(
                    "run idempotency key was already used for different input",
                    status=409,
                    code="run_idempotency_conflict",
                )
            return self._heal_if_stale(existing)

        if idempotency_key:
            replay = replay_idempotent()
            if replay is not None:
                return replay
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
            from ..integrations.accounting_context import accounting_enabled

            if accounting_enabled():
                await self.analytics.require_execution_budget(
                    agent_id,
                    context_id=str(
                        context.get("organization_id")
                        or context.get("payer_organization_id")
                        or "personal"
                    ),
                )
            else:
                await self.analytics.require_execution_budget(agent_id)
        # Budget verification yields to the event loop. Another request may
        # have claimed this conversation while it was pending. Recheck before
        # the synchronous record/worker registration critical section below.
        active_id = self._by_conversation.get(key)
        replay = replay_idempotent()
        if replay is not None:
            return replay
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
            "idempotency_key": idempotency_key or None,
            "request_fingerprint": fingerprint,
            "links": {
                "goal_id": (f"goal:{conversation_id}" if "goal" in capabilities else None),
                "todo_revision": 0 if "todo" in capabilities else None,
                "children": {},
            },
            "execution_budget": {
                "turn_limit": concurrent_work_max_turns(),
                "turns_used": 0,
                "concurrency_limit": max_parallel_agents(),
                "active_children": 0,
                "peak_children": 0,
                "depth_limit": concurrent_work_max_depth(),
            },
            "cancellation": {
                "requested": False,
                "children_pending": [],
                "unknown_in_flight": False,
            },
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

        def request_cancellation(current: dict[str, Any]) -> dict[str, Any]:
            links = dict(current.get("links") or {})
            children = dict(links.get("children") or {})
            pending = [
                child_id
                for child_id, child in children.items()
                if isinstance(child, Mapping)
                and str(child.get("status") or "") in {"pending", "running"}
            ]
            current["cancellation"] = {
                "requested": True,
                "requested_at": time.time(),
                "children_pending": pending,
                "unknown_in_flight": bool(pending),
            }
            return current

        self.repository.mutate_conversation_run(
            agent_id,
            conversation_id,
            run_id,
            request_cancellation,
        )
        if entry.task and not entry.task.done():
            entry.task.cancel()
            with suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(entry.task), timeout=10)
        return self.get_run(agent_id, conversation_id, run_id)

    async def cancel_child_run(
        self,
        agent_id: str,
        conversation_id: str,
        run_id: str,
        child_run_id: str,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        expected_revision = int(body.get("expected_revision") or 0)
        idempotency_key = str(body.get("idempotency_key") or "")
        subagent_id = ""
        replayed = False

        def mutate(current: dict[str, Any]) -> dict[str, Any]:
            nonlocal subagent_id, replayed
            receipts = dict(current.get("child_cancel_receipts") or {})
            fingerprint = f"{child_run_id}:{expected_revision}"
            receipt = receipts.get(idempotency_key)
            if isinstance(receipt, Mapping):
                if receipt.get("fingerprint") != fingerprint:
                    raise ServiceError(
                        "child cancellation idempotency key was reused",
                        status=409,
                        code="child_cancel_idempotency_conflict",
                    )
                replayed = True
                return current
            links = dict(current.get("links") or {})
            children = dict(links.get("children") or {})
            child = dict(children.get(child_run_id) or {})
            if not child:
                raise ServiceError("child run not found", status=404, code="child_run_not_found")
            revision = int(child.get("revision") or 0)
            if revision != expected_revision:
                raise ServiceError(
                    "child run revision changed; reconcile before cancelling",
                    status=409,
                    code="child_run_revision_conflict",
                )
            if str(child.get("status") or "") in {
                "completed",
                "failed",
                "cancelled",
                "error",
                "timeout",
                "interrupted",
            }:
                raise ServiceError(
                    "child run has already finished",
                    status=409,
                    code="child_run_already_finished",
                )
            subagent_id = str(child.get("subagent_id") or "")
            child["status"] = "cancellation_pending"
            child["revision"] = revision + 1
            child["cancellation_requested_at"] = time.time()
            children[child_run_id] = child
            links["children"] = children
            current["links"] = links
            receipts[idempotency_key] = {
                "fingerprint": fingerprint,
                "child_revision": revision + 1,
            }
            current["child_cancel_receipts"] = receipts
            current["updated_at"] = time.time()
            return current

        updated = self.repository.mutate_conversation_run(
            agent_id,
            conversation_id,
            run_id,
            mutate,
        )
        if not replayed and subagent_id:
            stopped = await self.agents.stop_child_run(run_id, subagent_id)
            if not stopped:

                def mark_unknown(current: dict[str, Any]) -> dict[str, Any]:
                    links = dict(current.get("links") or {})
                    children = dict(links.get("children") or {})
                    child = dict(children.get(child_run_id) or {})
                    child["status"] = "unknown"
                    child["unknown_in_flight"] = True
                    children[child_run_id] = child
                    links["children"] = children
                    current["links"] = links
                    return current

                updated = self.repository.mutate_conversation_run(
                    agent_id,
                    conversation_id,
                    run_id,
                    mark_unknown,
                )
        return updated

    def update_todo(
        self,
        agent_id: str,
        conversation_id: str,
        run_id: str,
        todo_id: str,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        expected_revision = int(body.get("expected_revision") or 0)
        idempotency_key = str(body.get("idempotency_key") or "")
        status = str(body.get("status") or "")
        evidence = body.get("evidence")

        def mutate(current: dict[str, Any]) -> dict[str, Any]:
            receipts = dict(current.get("todo_update_receipts") or {})
            fingerprint = json.dumps(
                {
                    "todo_id": todo_id,
                    "expected_revision": expected_revision,
                    "status": status,
                    "evidence": evidence,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            receipt = receipts.get(idempotency_key)
            if isinstance(receipt, Mapping):
                if receipt.get("fingerprint") != fingerprint:
                    raise ServiceError(
                        "todo update idempotency key was reused",
                        status=409,
                        code="todo_idempotency_conflict",
                    )
                return current
            links = dict(current.get("links") or {})
            revision = int(links.get("todo_revision") or 0)
            if revision != expected_revision:
                raise ServiceError(
                    "todo revision changed; reconcile before updating",
                    status=409,
                    code="todo_revision_conflict",
                )
            todos = [dict(item) for item in list(links.get("todos") or [])]
            selected = next((item for item in todos if str(item.get("id")) == todo_id), None)
            if selected is None:
                raise ServiceError("todo not found", status=404, code="todo_not_found")
            next_revision = revision + 1
            selected["status"] = status
            selected["revision"] = next_revision
            if evidence is not None:
                selected["evidence"] = str(evidence)
            links["todo_revision"] = next_revision
            links["todos"] = todos
            current["links"] = links
            receipts[idempotency_key] = {
                "fingerprint": fingerprint,
                "revision": next_revision,
            }
            current["todo_update_receipts"] = receipts
            current["updated_at"] = time.time()
            return current

        return self.repository.mutate_conversation_run(
            agent_id,
            conversation_id,
            run_id,
            mutate,
        )

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
        latest = self.repository.get_conversation_run(
            record["agent_id"], record["conversation_id"], record["id"]
        )
        record = {**dict(record), **latest}
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
            cancellation = dict(next_record.get("cancellation") or {})
            cancellation["requested"] = True
            cancellation["unknown_in_flight"] = bool(cancellation.get("children_pending"))
            next_record.update(
                {
                    "status": "cancelled",
                    "ended_at": float(payload.get("timestamp") or now),
                    "cancellation": cancellation,
                }
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
        if isinstance(payload, Mapping):
            links = dict(next_record.get("links") or {})
            if event_name == "todo.updated":
                todo_revision = int(links.get("todo_revision") or 0) + 1
                links["todo_revision"] = todo_revision
                links["todos"] = []
                for item in list(payload.get("todos") or []):
                    if not isinstance(item, Mapping):
                        continue
                    todo = {**dict(item), "revision": todo_revision}
                    if todo.get("status") == "in_progress":
                        todo["status"] = "running"
                    links["todos"].append(todo)
                payload["todo_revision"] = todo_revision
            elif event_name == "goal.updated":
                links["goal"] = dict(payload.get("goal") or {})
            elif event_name.startswith("delegation.worker."):
                children = dict(links.get("children") or {})
                child_id = str(payload.get("child_run_id") or "")
                if child_id:
                    previous = dict(children.get(child_id) or {})
                    state = {
                        "delegation.worker.queued": "pending",
                        "delegation.worker.started": "running",
                        "delegation.worker.completed": str(payload.get("status") or "completed"),
                    }.get(event_name, previous.get("status") or "running")
                    child = {
                        **previous,
                        "id": child_id,
                        "revision": int(previous.get("revision") or 0) + 1,
                        "parent_run_id": next_record["id"],
                        "status": state,
                        "task_index": payload.get("task_index"),
                        "subagent_id": payload.get("subagent_id"),
                        "child_session_id": payload.get("child_session_id"),
                        "depth": payload.get("depth"),
                        "goal": payload.get("goal"),
                        "updated_at": float(payload.get("timestamp") or now),
                    }
                    for field in (
                        "todo_id",
                        "expected_todo_revision",
                        "summary",
                        "api_calls",
                        "input_tokens",
                        "output_tokens",
                        "reasoning_tokens",
                        "files_read",
                        "files_written",
                    ):
                        if field in payload:
                            child[field] = payload.get(field)
                    children[child_id] = child
                    links["children"] = children
            elif event_name == "execution.budget.updated":
                budget = dict(next_record.get("execution_budget") or {})
                budget["turns_used"] = int(payload.get("turns_used") or 0)
                budget["turn_limit"] = int(payload.get("turn_limit") or 0)
                next_record["execution_budget"] = budget
            elif event_name == "execution.concurrency.updated":
                budget = dict(next_record.get("execution_budget") or {})
                budget["active_children"] = int(payload.get("active_children") or 0)
                budget["peak_children"] = max(
                    int(budget.get("peak_children") or 0),
                    int(payload.get("peak_children") or 0),
                )
                next_record["execution_budget"] = budget
            next_record["links"] = links
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
