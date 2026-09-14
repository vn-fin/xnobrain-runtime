"""OpenAI-compatible agent endpoint tests."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from xnobrain.services.agent_api import AgentAPIService


class FakeRuns:
    async def events(self, agent_id, conversation_id, run_id):
        yield {"event": "message", "data": {"event": "message.delta", "delta": "Hello "}}
        yield {"event": "message", "data": {"event": "message.delta", "delta": "world"}}
        yield {
            "event": "message",
            "data": {
                "event": "run.completed",
                "output": "Hello world",
                "usage": {"input_tokens": 5, "output_tokens": 2},
            },
        }


class FakePlatform:
    def __init__(self):
        self.conversation_runs = FakeRuns()
        self.created = None
        self.started = None

    def get_agent(self, agent_id):
        if agent_id != "research-agent":
            raise AssertionError(agent_id)
        return {"id": agent_id}

    def create_conversation(self, agent_id, body, trusted):
        self.created = (agent_id, body, trusted.subject)
        return {"id": "session-api"}

    async def start_conversation_run(self, agent_id, conversation_id, body, trusted):
        self.started = (agent_id, conversation_id, body, trusted.subject)
        return {"id": "run-api"}


class AgentAPIServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_completion_maps_openai_messages_to_owned_agent_run(self):
        platform = FakePlatform()
        service = AgentAPIService(platform)
        result = await service.completion(
            "research-agent",
            {
                "model": "research-agent",
                "messages": [
                    {"role": "system", "content": "Be concise."},
                    {"role": "user", "content": "Hello"},
                ],
            },
            SimpleNamespace(subject="user-1"),
        )
        self.assertEqual(result["object"], "chat.completion")
        self.assertEqual(result["choices"][0]["message"]["content"], "Hello world")
        self.assertEqual(result["usage"]["total_tokens"], 7)
        self.assertEqual(platform.created[0], "research-agent")
        self.assertIn("SYSTEM:\nBe concise.", platform.started[2]["input"])
        self.assertIn("USER:\nHello", platform.started[2]["input"])

    async def test_stream_returns_openai_chunks_and_done(self):
        platform = FakePlatform()
        service = AgentAPIService(platform)
        chunks = [
            item.decode()
            async for item in service.stream(
                "research-agent",
                {"model": "research-agent", "messages": [{"role": "user", "content": "Hi"}]},
                SimpleNamespace(subject="user-1"),
            )
        ]
        payload = json.loads(chunks[0].removeprefix("data: "))
        self.assertEqual(payload["object"], "chat.completion.chunk")
        self.assertEqual(payload["choices"][0]["delta"]["role"], "assistant")
        self.assertEqual(chunks[-1], "data: [DONE]\n\n")
