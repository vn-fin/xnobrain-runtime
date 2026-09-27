"""Conversation effort persistence and contract regressions."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from xnobrain.models.conversations import ConversationReasoningUpdate
from xnobrain.repositories import FileRepository, StoreError
from xnobrain.repositories.conversation_reasoning import ConversationReasoningRepository
from xnobrain.services.base import ServiceError
from xnobrain.services.conversation_reasoning import validate


class PreferenceTests(unittest.TestCase):
    def test_revision_reload_isolation_clear_and_delete(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "profiles" / "agent"
            profile.mkdir(parents=True)
            files = FileRepository(root / "data", root / "profiles")
            first = ConversationReasoningRepository(files, profile, "one")
            second = ConversationReasoningRepository(files, profile, "two")
            self.assertIsNone(first.read()["reasoning_effort"])
            first.update("none", 0)
            self.assertEqual(
                ConversationReasoningRepository(files, profile, "one").read(),
                {"reasoning_effort": "none", "reasoning_revision": 1},
            )
            self.assertIsNone(second.read()["reasoning_effort"])
            with self.assertRaises(StoreError) as conflict:
                first.update("high", 0)
            self.assertEqual(conflict.exception.code, "reasoning_revision_conflict")
            first.update(None, 1)
            self.assertIsNone(first.read()["reasoning_effort"])
            first.delete()
            self.assertFalse(first.path.exists())

    def test_contract_rejects_missing_unknown_and_authority_fields(self):
        for body in (
            {"expected_revision": 0},
            {"reasoning_effort": "invalid", "expected_revision": 0},
            {"reasoning_effort": None, "expected_revision": -1},
            {"reasoning_effort": "high", "expected_revision": 0, "tenant": "other"},
        ):
            with self.subTest(body=body), self.assertRaises(ValidationError):
                ConversationReasoningUpdate.model_validate(body)

    def test_symlink_rejected(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "profiles" / "agent"
            profile.mkdir(parents=True)
            files = FileRepository(root / "data", root / "profiles")
            store = ConversationReasoningRepository(files, profile, "one")
            store.path.symlink_to(root / "elsewhere")
            with self.assertRaises(StoreError):
                store.update("high", 0)


class MetadataTests(unittest.IsolatedAsyncioTestCase):
    async def test_support_and_outage(self):
        router = AsyncMock()
        router.reasoning_for_model.return_value = {"reasoning": ["none", "high"]}
        self.assertEqual(await validate(router, "model", "none"), "available")
        with self.assertRaises(ServiceError) as error:
            await validate(router, "model", "ultra")
        self.assertEqual(error.exception.code, "unsupported_reasoning_effort")
        router.reasoning_for_model.side_effect = RuntimeError("unavailable")
        with self.assertRaises(ServiceError) as error:
            await validate(router, "model", "high")
        self.assertEqual(error.exception.status, 503)
        self.assertEqual(await validate(router, "model", None), "available")

    async def test_disabled_routes_absent(self):
        from xnobrain.routes.setup import route_groups

        with patch.dict("os.environ", {"FT_ENABLE_CONVERSATION_REASONING_EFFORT": "invalid"}):
            self.assertFalse(
                any(
                    route.operation.startswith("conversation_reasoning_")
                    for group in route_groups()
                    for route in group
                )
            )


class ExecutionGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_none_and_model_boundary_validation(self):
        import asyncio
        from types import SimpleNamespace

        from xnobrain.integrations.conversation_reasoning import install_reasoning_guard

        router = AsyncMock()
        router.reasoning_for_model.return_value = {"reasoning": ["none", "high"]}
        agent = SimpleNamespace(model="model", reasoning_config={"effort": "high"})
        agent._build_api_kwargs = lambda: {"reasoning": agent.reasoning_config}
        events = []
        install_reasoning_guard(agent, router, asyncio.get_running_loop(), "none", events.append)
        result = await asyncio.to_thread(agent._build_api_kwargs)
        self.assertEqual(agent.reasoning_config, {"enabled": False})
        self.assertEqual(result["reasoning"], {"enabled": False})
        self.assertEqual(events, ["none"])
        router.reasoning_for_model.return_value = {"reasoning": ["high"]}
        with self.assertRaisesRegex(ValueError, "unsupported_reasoning_effort"):
            await asyncio.to_thread(agent._build_api_kwargs)

    async def test_auto_non_reasoning_model_omits_config(self):
        import asyncio
        from types import SimpleNamespace

        from xnobrain.integrations.conversation_reasoning import install_reasoning_guard

        router = AsyncMock()
        router.reasoning_for_model.return_value = {"reasoning": [], "default_reasoning": "auto"}
        agent = SimpleNamespace(model="model", reasoning_config={"effort": "high"})
        agent._build_api_kwargs = lambda: agent.reasoning_config
        install_reasoning_guard(agent, router, asyncio.get_running_loop(), "auto", lambda _: None)
        self.assertIsNone(await asyncio.to_thread(agent._build_api_kwargs))


class PersistedHermesRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_reopened_preference_reaches_real_hermes_serializer(self):
        import asyncio
        from types import SimpleNamespace

        from agent.transports.chat_completions import ChatCompletionsTransport

        from xnobrain.integrations.conversation_reasoning import install_reasoning_guard
        from xnobrain.services.conversation_reasoning import snapshot

        with TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "profiles" / "agent"
            profile.mkdir(parents=True)
            files = FileRepository(root / "data", root / "profiles")
            service = SimpleNamespace(
                repository=files,
                agents=SimpleNamespace(
                    describe_agent=lambda _: {"config": {"reasoning_effort": "medium"}}
                ),
            )
            router = AsyncMock()
            router.reasoning_for_model.return_value = {
                "reasoning": ["none", "low", "medium", "high", "xhigh", "max", "ultra"],
                "default_reasoning": "medium",
            }
            revision = 0
            for effort in ["high", "low", "none", "xhigh", "max", "ultra", "auto"]:
                with self.subTest(effort=effort):
                    store = ConversationReasoningRepository(files, profile, "one")
                    store.update(effort, revision)
                    revision += 1
                    admitted = snapshot(service, "agent", "one")
                    agent = SimpleNamespace(model="cx/gpt-6-sol", reasoning_config=None)
                    transport = ChatCompletionsTransport()
                    agent._get_transport = lambda: transport
                    agent._build_api_kwargs = lambda: agent._get_transport().build_kwargs(
                        model=agent.model,
                        messages=[],
                        reasoning_config=agent.reasoning_config,
                        supports_reasoning=False,
                        is_custom_provider=True,
                        request_overrides={
                            "extra_body": {
                                "reasoning": {"effort": "low"},
                                "other": True,
                            }
                        },
                    )
                    install_reasoning_guard(
                        agent,
                        router,
                        asyncio.get_running_loop(),
                        admitted["effective_preference"],
                        lambda _: None,
                    )
                    # A later edit must not mutate the already admitted run.
                    store.update("medium", revision)
                    revision += 1
                    request = await asyncio.to_thread(agent._build_api_kwargs)
                    expected = "medium" if effort == "auto" else effort
                    self.assertEqual(request["extra_body"]["reasoning"]["effort"], expected)
                    self.assertNotIn("reasoning_effort", request)
                    self.assertTrue(request["extra_body"]["other"])
                    self.assertEqual(snapshot(service, "agent", "two")["source"], "agent")
            store.update(None, revision)
            inherited = snapshot(service, "agent", "one")
            self.assertEqual(inherited["effective_preference"], "medium")
            self.assertEqual(inherited["source"], "agent")


class NativeMappingTests(unittest.TestCase):
    def test_native_mapping_is_not_overwritten_and_other_transports_are_untouched(self):
        from types import SimpleNamespace

        from agent.transports.chat_completions import ChatCompletionsTransport
        from hermes_constants import parse_reasoning_effort

        from xnobrain.integrations.conversation_reasoning import install_router_reasoning_transport

        native = ChatCompletionsTransport()
        agent = SimpleNamespace(_get_transport=lambda: native)
        install_router_reasoning_transport(agent)
        request = agent._get_transport().build_kwargs(
            model="cx/gpt-5.6",
            messages=[],
            reasoning_config=parse_reasoning_effort("ultra"),
        )
        self.assertEqual(request["extra_body"]["reasoning"]["effort"], "max")
        self.assertNotIn(
            "reasoning_effort",
            agent._get_transport().build_kwargs(
                model="plain",
                messages=[],
                reasoning_config=None,
            ),
        )
        other = object()
        other_agent = SimpleNamespace(_get_transport=lambda: other)
        install_router_reasoning_transport(other_agent)
        self.assertIs(other_agent._get_transport(), other)


class RouterWireFormatTests(unittest.TestCase):
    def test_sdk_sends_nested_codex_reasoning_and_preserves_other_routes(self):
        import json
        from types import SimpleNamespace

        import httpx
        from agent.transports.chat_completions import ChatCompletionsTransport
        from hermes_constants import parse_reasoning_effort
        from openai import OpenAI

        from xnobrain.integrations.conversation_reasoning import install_router_reasoning_transport

        captured = []

        def receive(request):
            captured.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "id": "test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "test",
                    "choices": [],
                },
            )

        native = ChatCompletionsTransport()
        agent = SimpleNamespace(_get_transport=lambda: native)
        install_router_reasoning_transport(agent)
        with OpenAI(
            api_key="test-only", http_client=httpx.Client(transport=httpx.MockTransport(receive))
        ) as client:
            for model in ("cx/gpt-6-luna", "org/cx/gpt-6-luna", "other/model"):
                for effort in ("low", "max"):
                    kwargs = agent._get_transport().build_kwargs(
                        model=model,
                        messages=[{"role": "user", "content": "test"}],
                        reasoning_config=parse_reasoning_effort(effort),
                        request_overrides={"reasoning_effort": "medium"},
                    )
                    kwargs["stream"] = False
                    client.chat.completions.create(**kwargs)
                    body = captured[-1]
                    if "/cx/" in f"/{model}":
                        self.assertEqual(body["reasoning"]["effort"], effort)
                        self.assertNotIn("reasoning_effort", body)
                    else:
                        self.assertEqual(body["reasoning_effort"], effort)
                        self.assertNotIn("reasoning", body)
