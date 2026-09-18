from __future__ import annotations

import asyncio
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from xnobrain.repositories import FileRepository
from xnobrain.services.conversation_runs import ConversationRunService


def sse(payload: dict) -> bytes:
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n".encode()


class FakeAgents:
    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.finished = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.received: dict = {}

    def get_conversation(self, agent_id: str, conversation_id: str) -> dict:
        return {"conversation": {"id": conversation_id, "agent_id": agent_id}, "messages": []}

    async def stop_child_run(self, run_id: str, subagent_id: str) -> bool:
        self.stopped_child = (run_id, subagent_id)
        return True

    async def chat_stream(self, agent_id: str, body: dict):
        self.received = {"agent_id": agent_id, **dict(body)}
        run_id = body["run_id"]
        try:
            yield sse({"event": "run.started", "run_id": run_id, "timestamp": 100.0})
            yield sse(
                {"event": "tool.started", "run_id": run_id, "timestamp": 101.0, "tool": "terminal"}
            )
            await self.release.wait()
            yield sse(
                {
                    "event": "run.completed",
                    "run_id": run_id,
                    "timestamp": 102.0,
                    "output": "report ready",
                    "usage": {"total_tokens": 12},
                }
            )
            yield b"data: [DONE]\n\n"
            self.finished.set()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


class FakeAnalytics:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def require_execution_budget(self, agent_id: str, **_: object) -> None:
        self.calls.append(agent_id)



class ConversationRunServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.timeout_env = patch.dict(
            os.environ,
            {"RUNTIME_SESSION_TIMEOUT_SECONDS": "60"},
        )
        self.timeout_env.start()
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        profiles = root / "profiles"
        (profiles / "agent-one").mkdir(parents=True)
        self.repository = FileRepository(root / "data", profiles)
        self.agents = FakeAgents()
        self.analytics = FakeAnalytics()
        self.service = ConversationRunService(self.repository, self.agents, self.analytics)

    async def asyncTearDown(self) -> None:
        await self.service.shutdown()
        self.temp.cleanup()
        self.timeout_env.stop()

    async def test_combined_selection_persists_with_one_budget_check_and_parent(self):
        selected = ["goal", "todo", "delegate"]
        record = await self.service.start_run(
            "agent-one", "session-one", {"input": "Synthetic task", "capabilities": selected}
        )
        selected.clear()
        await self.wait_for_revision(record["id"], 2)
        expected = {
            "schema_version": 1,
            "feature": None,
            "capabilities": ["todo", "delegate", "goal"],
        }
        self.assertEqual(record["composer_selection"], expected)
        self.assertEqual(self.agents.received["capabilities"], expected["capabilities"])
        self.assertEqual(self.analytics.calls, ["agent-one"])
        reloaded = FileRepository(Path(self.temp.name) / "data", Path(self.temp.name) / "profiles")
        stored = reloaded.get_conversation_run("agent-one", "session-one", record["id"])
        self.assertEqual(stored["composer_selection"], expected)
        self.assertEqual(len(reloaded.list_conversation_runs("agent-one", "session-one")), 1)

    async def test_invalid_selection_cannot_dispatch_or_consume_budget(self):
        from xnobrain.services.base import ServiceError

        with self.assertRaises(ServiceError) as error:
            await self.service.start_run(
                "agent-one",
                "session-one",
                {"input": "Synthetic task", "feature": "todo", "capabilities": ["goal"]},
            )
        self.assertEqual(error.exception.code, "invalid_capabilities")
        self.assertEqual(self.analytics.calls, [])
        self.assertEqual(self.repository.list_conversation_runs("agent-one", "session-one"), [])

    async def test_concurrent_budget_checks_cannot_dispatch_two_parents(self):
        from xnobrain.services.base import ServiceError

        entered = 0
        both_entered = asyncio.Event()

        async def delayed_budget(_agent_id):
            nonlocal entered
            entered += 1
            if entered == 2:
                both_entered.set()
            await both_entered.wait()

        self.analytics.require_execution_budget = delayed_budget
        results = await asyncio.wait_for(
            asyncio.gather(
                self.service.start_run("agent-one", "session-one", {"input": "Synthetic one"}),
                self.service.start_run("agent-one", "session-one", {"input": "Synthetic two"}),
                return_exceptions=True,
            ),
            timeout=2,
        )
        successes = [result for result in results if isinstance(result, dict)]
        failures = [result for result in results if isinstance(result, ServiceError)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].code, "conversation_running")
        self.assertEqual(len(self.repository.list_conversation_runs("agent-one", "session-one")), 1)

    async def wait_for_revision(self, run_id: str, revision: int) -> dict:
        for _ in range(100):
            record = self.repository.get_conversation_run("agent-one", "session-one", run_id)
            if int(record.get("revision") or 0) >= revision:
                return record
            await asyncio.sleep(0.01)
        self.fail(f"run did not reach revision {revision}")

    async def test_observer_disconnect_does_not_cancel_and_reconnect_replays(self) -> None:
        record = await self.service.start_run(
            "agent-one",
            "session-one",
            {"input": "Research papers and write a PDF report", "model": "test/model"},
            ownership_context={
                "id": "personal",
                "owner_kind": "personal",
                "payer_kind": "personal",
            },
        )
        self.assertEqual(record["mode"], "background")
        self.assertEqual(record["timeout_seconds"], 60)
        self.assertEqual(record["ownership_context"]["id"], "personal")
        await self.wait_for_revision(record["id"], 2)

        observer = self.service.events("agent-one", "session-one", record["id"])
        first = await anext(observer)
        self.assertEqual(first["sequence"], 1)
        self.assertEqual(first["data"]["ownership_context"]["id"], "personal")
        await observer.aclose()
        self.assertFalse(self.service.registry_entry(record["id"]).task.done())

        self.agents.release.set()
        await asyncio.wait_for(self.agents.finished.wait(), timeout=1)
        await self.wait_for_revision(record["id"], 3)
        replay = [
            event
            async for event in self.service.events(
                "agent-one",
                "session-one",
                record["id"],
                after=1,
            )
        ]
        self.assertEqual([event["sequence"] for event in replay], [2, 3])
        self.assertEqual(replay[-1]["data"]["output"], "report ready")
        completed = self.service.get_run("agent-one", "session-one", record["id"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["usage"], {"total_tokens": 12})
        self.assertEqual(self.agents.received["run_id"], record["id"])
        self.assertEqual(self.agents.received["conversation_id"], "session-one")
        self.assertEqual(self.agents.received["ownership_context"]["id"], "personal")

    async def test_conflicting_context_input_is_rejected_before_budget_or_dispatch(self) -> None:
        with self.assertRaisesRegex(Exception, "conflicts") as caught:
            await self.service.start_run(
                "agent-one",
                "session-one",
                {"input": "hello", "payer_kind": "organization_sponsor"},
                ownership_context={"id": "personal", "payer_kind": "personal"},
            )
        self.assertEqual(caught.exception.code, "conversation_context_conflict")
        self.assertEqual(self.analytics.calls, [])
        self.assertEqual(self.agents.received, {})

    async def test_inactive_context_denied_before_budget_or_dispatch(self) -> None:
        from xnobrain.services.base import ServiceError

        with self.assertRaises(ServiceError) as denied:
            await self.service.start_run(
                "agent-one",
                "session-one",
                {"input": "hello"},
                ownership_context={"id": "org-one", "state": "revoked_read_only"},
            )
        self.assertEqual(denied.exception.code, "conversation_context_inactive")
        self.assertEqual(self.analytics.calls, [])
        self.assertEqual(self.agents.received, {})

    async def test_budget_is_checked_once_when_the_run_is_accepted(self) -> None:
        record = await self.service.start_run(
            "agent-one",
            "session-one",
            {"input": "hello", "model": "test/model"},
        )
        self.assertEqual(self.analytics.calls, ["agent-one"])

        self.agents.release.set()
        await asyncio.wait_for(self.agents.finished.wait(), timeout=1)
        await self.wait_for_revision(record["id"], 3)
        self.assertEqual(self.analytics.calls, ["agent-one"])

    async def test_cancel_closes_the_running_agent_stream(self) -> None:
        record = await self.service.start_run(
            "agent-one",
            "session-one",
            {"input": "run a long command", "model": "test/model"},
        )
        await self.wait_for_revision(record["id"], 2)

        cancelled = await self.service.cancel_run(
            "agent-one",
            "session-one",
            record["id"],
        )

        self.assertEqual(cancelled["status"], "cancelled")
        self.assertTrue(self.agents.cancelled.is_set())

    async def test_interactive_and_explicit_timeout_policy(self) -> None:
        first = await self.service.start_run(
            "agent-one",
            "session-one",
            {"input": "Say hello", "model": "test/model"},
        )
        self.assertEqual(first["mode"], "interactive")
        self.assertEqual(first["timeout_seconds"], 60)
        await self.service.cancel_run("agent-one", "session-one", first["id"])

        second = await self.service.start_run(
            "agent-one",
            "session-one",
            {
                "input": "Say hello",
                "model": "test/model",
                "run_mode": "background",
                "timeout_seconds": 172800,
            },
        )
        self.assertEqual(second["mode"], "background")
        self.assertEqual(second["timeout_seconds"], 86400)
        await self.service.cancel_run("agent-one", "session-one", second["id"])

    async def test_custom_page_scope_is_in_run_receipt_not_browser_payload(self):
        from xnobrain.services.base import ServiceError
        from xnobrain.trusted_context import TrustedRequestContext

        body = {"input": "Approved analysis", "idempotency_key": "page-action-01"}
        from xnobrain.services.conversations import ConversationsServiceMixin

        context = ConversationsServiceMixin._personal_context()
        self.repository.create_conversation_context(
            self.repository.live_profile_path("agent-one"),
            {
                **context,
                "agent_id": "agent-one",
                "conversation_id": "session-one",
                "actor_user_id": "owner",
                "actor_tenant_id": "tenant",
            },
        )
        scope = {
            "ownership_context": context,
            "trusted_context": TrustedRequestContext("owner", "tenant"),
            "custom_page_datasets": ["articles"],
            "custom_page_revision": 1,
        }
        first = await self.service.start_run("agent-one", "session-one", body, **scope)
        replay = await self.service.start_run("agent-one", "session-one", body, **scope)
        self.assertEqual(first["id"], replay["id"])
        self.assertEqual(self.analytics.calls, ["agent-one"])
        for change in ({"custom_page_datasets": ["private"]}, {"custom_page_revision": 2}):
            with self.assertRaises(ServiceError) as caught:
                await self.service.start_run("agent-one", "session-one", body, **(scope | change))
            self.assertEqual(caught.exception.code, "run_idempotency_conflict")
        stored = self.repository.get_conversation_run("agent-one", "session-one", first["id"])
        self.assertEqual(stored["custom_page_datasets"], ["articles"])
        self.assertEqual(stored["custom_page_revision"], 1)
        self.assertEqual(stored["ownership_context"]["payer_kind"], "personal")

    async def test_all_four_capabilities_share_one_verified_parent_and_budget(self):
        from xnobrain.services.conversations import ConversationsServiceMixin
        from xnobrain.trusted_context import TrustedRequestContext

        context = ConversationsServiceMixin._personal_context()
        self.repository.create_conversation_context(
            self.repository.live_profile_path("agent-one"),
            {
                **context,
                "agent_id": "agent-one",
                "conversation_id": "session-one",
                "actor_user_id": "owner",
                "actor_tenant_id": "tenant",
            },
        )
        first = await self.service.start_run(
            "agent-one",
            "session-one",
            {
                "input": "Create a news page",
                "capabilities": ["custom_page", "goal", "todo", "delegate"],
                "idempotency_key": "four-capabilities",
            },
            ownership_context=context,
            trusted_context=TrustedRequestContext("owner", "tenant"),
        )
        await self.wait_for_revision(first["id"], 2)
        self.assertEqual(
            first["composer_selection"]["capabilities"], ["todo", "delegate", "goal", "custom_page"]
        )
        self.assertEqual(self.analytics.calls, ["agent-one"])
        self.assertEqual(len(self.repository.list_conversation_runs("agent-one", "session-one")), 1)

    async def test_idempotent_start_replays_one_parent_and_conflicts_on_change(self):
        from xnobrain.services.base import ServiceError

        body = {
            "input": "Synthetic task",
            "capabilities": ["todo", "delegate", "goal"],
            "idempotency_key": "send-once",
        }
        first = await self.service.start_run("agent-one", "session-one", body)
        second = await self.service.start_run("agent-one", "session-one", body)
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(self.analytics.calls, ["agent-one"])
        self.assertEqual(first["links"]["goal_id"], "goal:session-one")
        self.assertEqual(first["links"]["todo_revision"], 0)
        self.assertEqual(first["execution_budget"]["turn_limit"], 90)
        self.assertEqual(first["execution_budget"]["concurrency_limit"], 3)
        self.assertEqual(first["execution_budget"]["depth_limit"], 1)
        self.assertEqual(len(self.repository.list_conversation_runs("agent-one", "session-one")), 1)
        with self.assertRaises(ServiceError) as conflict:
            await self.service.start_run(
                "agent-one",
                "session-one",
                {**body, "input": "Different task"},
            )
        self.assertEqual(conflict.exception.code, "run_idempotency_conflict")

    async def test_linked_goal_todo_child_and_revision_conflict_survive_reload(self):
        record = await self.service.start_run(
            "agent-one",
            "session-one",
            {
                "input": "Synthetic task",
                "capabilities": ["todo", "delegate", "goal"],
            },
        )
        await self.wait_for_revision(record["id"], 2)
        current = self.repository.get_conversation_run("agent-one", "session-one", record["id"])
        current = self.service._append(
            current,
            {
                "event": "message",
                "data": {
                    "event": "todo.updated",
                    "todos": [{"id": "todo-1", "content": "Inspect", "status": "pending"}],
                },
            },
        )
        current = self.service._append(
            current,
            {
                "event": "message",
                "data": {
                    "event": "goal.updated",
                    "goal": {"objective": "Ship safely", "status": "active"},
                },
            },
        )
        current = self.service._append(
            current,
            {
                "event": "message",
                "data": {
                    "event": "delegation.worker.completed",
                    "child_run_id": f"{record['id']}:child:sa-one",
                    "subagent_id": "sa-one",
                    "task_index": 0,
                    "todo_id": "todo-1",
                    "expected_todo_revision": 1,
                    "status": "completed",
                    "summary": "Evidence collected",
                },
            },
        )
        updated = self.service.update_todo(
            "agent-one",
            "session-one",
            record["id"],
            "todo-1",
            {
                "expected_revision": 1,
                "idempotency_key": "todo-once",
                "status": "completed",
                "evidence": "Focused check passed",
            },
        )
        replay = self.service.update_todo(
            "agent-one",
            "session-one",
            record["id"],
            "todo-1",
            {
                "expected_revision": 1,
                "idempotency_key": "todo-once",
                "status": "completed",
                "evidence": "Focused check passed",
            },
        )
        self.assertEqual(replay["links"], updated["links"])
        from xnobrain.services.base import ServiceError

        with self.assertRaises(ServiceError) as conflict:
            self.service.update_todo(
                "agent-one",
                "session-one",
                record["id"],
                "todo-1",
                {
                    "expected_revision": 1,
                    "idempotency_key": "todo-stale",
                    "status": "failed",
                },
            )
        self.assertEqual(conflict.exception.code, "todo_revision_conflict")
        reopened = FileRepository(Path(self.temp.name) / "data", Path(self.temp.name) / "profiles")
        stored = reopened.get_conversation_run("agent-one", "session-one", record["id"])
        child = next(iter(stored["links"]["children"].values()))
        self.assertEqual(stored["links"]["goal"]["status"], "active")
        self.assertEqual(stored["links"]["todos"][0]["evidence"], "Focused check passed")
        self.assertEqual(child["todo_id"], "todo-1")
        self.assertEqual(child["summary"], "Evidence collected")

    async def test_child_cancel_is_revisioned_idempotent_and_keeps_parent_running(self):
        record = await self.service.start_run(
            "agent-one", "session-one", {"input": "Synthetic task", "capabilities": ["delegate"]}
        )
        await self.wait_for_revision(record["id"], 2)
        current = self.repository.get_conversation_run("agent-one", "session-one", record["id"])
        child_id = f"{record['id']}:child:sa-one"
        current = self.service._append(
            current,
            {
                "event": "message",
                "data": {
                    "event": "delegation.worker.started",
                    "child_run_id": child_id,
                    "subagent_id": "sa-one",
                    "task_index": 0,
                    "status": "running",
                },
            },
        )
        revision = current["links"]["children"][child_id]["revision"]
        body = {"expected_revision": revision, "idempotency_key": "stop-child-once"}
        first = await self.service.cancel_child_run(
            "agent-one", "session-one", record["id"], child_id, body
        )
        second = await self.service.cancel_child_run(
            "agent-one", "session-one", record["id"], child_id, body
        )
        self.assertEqual(first["links"], second["links"])
        self.assertEqual(self.agents.stopped_child, (record["id"], "sa-one"))
        self.assertFalse(self.service.registry_entry(record["id"]).task.done())
        self.assertEqual(
            first["links"]["children"][child_id]["status"],
            "cancellation_pending",
        )

    async def test_diagram_attachment_merges_into_message_and_is_stripped(self) -> None:
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            "<flowchart>\n"
            '  <node id="n1" x="0" y="0">Start</node>\n'
            "</flowchart>\n"
        )
        record = await self.service.start_run(
            "agent-one",
            "session-one",
            {
                "input": "Describe this flow",
                "attachment": {
                    "kind": "diagram",
                    "filename": "sketch.xml",
                    "mime_type": "application/xml",
                    "content": xml,
                },
            },
        )
        self.agents.release.set()
        await self.wait_for_revision(record["id"], 2)
        self.assertIn("<flowchart>", self.agents.received["message"])
        self.assertIn("Describe this flow", self.agents.received["message"])
        self.assertNotIn("attachment", self.agents.received)

    async def test_text_only_run_ignores_missing_attachment(self) -> None:
        record = await self.service.start_run(
            "agent-one", "session-one", {"input": "hello only"}
        )
        self.agents.release.set()
        await self.wait_for_revision(record["id"], 2)
        self.assertEqual(self.agents.received["input"], "hello only")
        self.assertNotIn("attachment", self.agents.received)


if __name__ == "__main__":
    unittest.main()
