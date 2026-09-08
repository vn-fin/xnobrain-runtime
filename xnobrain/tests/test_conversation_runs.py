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

    async def require_execution_budget(self, agent_id: str) -> None:
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
        expected = {"schema_version": 1, "feature": None, "capabilities": ["todo", "delegate", "goal"]}
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
                "agent-one", "session-one",
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


if __name__ == "__main__":
    unittest.main()
