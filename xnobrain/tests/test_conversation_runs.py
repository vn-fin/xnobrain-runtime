from __future__ import annotations

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from xnobrain.repositories import FileRepository
from xnobrain.services.conversation_runs import ConversationRunService


def sse(payload: dict) -> bytes:
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n".encode()


class FakeAgents:
    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.finished = asyncio.Event()
        self.received: dict = {}

    def get_conversation(self, agent_id: str, conversation_id: str) -> dict:
        return {"conversation": {"id": conversation_id, "agent_id": agent_id}, "messages": []}

    async def chat_stream(self, agent_id: str, body: dict):
        self.received = {"agent_id": agent_id, **dict(body)}
        run_id = body["run_id"]
        yield sse({"event": "run.started", "run_id": run_id, "timestamp": 100.0})
        yield sse({"event": "tool.started", "run_id": run_id, "timestamp": 101.0, "tool": "terminal"})
        await self.release.wait()
        yield sse({
            "event": "run.completed",
            "run_id": run_id,
            "timestamp": 102.0,
            "output": "report ready",
            "usage": {"total_tokens": 12},
        })
        yield b"data: [DONE]\n\n"
        self.finished.set()


class FakeAnalytics:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def require_chat_budget(self, agent_id: str) -> None:
        self.calls.append(agent_id)


class ConversationRunServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
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
        )
        self.assertEqual(record["mode"], "background")
        self.assertEqual(record["timeout_seconds"], 60)
        await self.wait_for_revision(record["id"], 2)

        observer = self.service.events("agent-one", "session-one", record["id"])
        first = await anext(observer)
        self.assertEqual(first["sequence"], 1)
        await observer.aclose()
        self.assertFalse(self.service.registry_entry(record["id"]).task.done())

        self.agents.release.set()
        await asyncio.wait_for(self.agents.finished.wait(), timeout=1)
        await self.wait_for_revision(record["id"], 3)
        replay = [
            event
            async for event in self.service.events(
                "agent-one", "session-one", record["id"], after=1,
            )
        ]
        self.assertEqual([event["sequence"] for event in replay], [2, 3])
        self.assertEqual(replay[-1]["data"]["output"], "report ready")
        completed = self.service.get_run("agent-one", "session-one", record["id"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["usage"], {"total_tokens": 12})
        self.assertEqual(self.agents.received["run_id"], record["id"])
        self.assertEqual(self.agents.received["conversation_id"], "session-one")

    async def test_budget_is_checked_once_when_the_run_is_accepted(self) -> None:
        record = await self.service.start_run(
            "agent-one", "session-one", {"input": "hello", "model": "test/model"},
        )
        self.assertEqual(self.analytics.calls, ["agent-one"])

        self.agents.release.set()
        await asyncio.wait_for(self.agents.finished.wait(), timeout=1)
        await self.wait_for_revision(record["id"], 3)
        self.assertEqual(self.analytics.calls, ["agent-one"])

    async def test_interactive_and_explicit_timeout_policy(self) -> None:
        first = await self.service.start_run(
            "agent-one", "session-one", {"input": "Say hello", "model": "test/model"},
        )
        self.assertEqual(first["mode"], "interactive")
        self.assertEqual(first["timeout_seconds"], 60)
        await self.service.cancel_run("agent-one", "session-one", first["id"])

        second = await self.service.start_run(
            "agent-one",
            "session-one",
            {"input": "Say hello", "model": "test/model", "run_mode": "background", "timeout_seconds": 7200},
        )
        self.assertEqual(second["mode"], "background")
        self.assertEqual(second["timeout_seconds"], 3600)
        await self.service.cancel_run("agent-one", "session-one", second["id"])


if __name__ == "__main__":
    unittest.main()
