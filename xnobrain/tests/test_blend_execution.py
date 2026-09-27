"""Blend decisions must reach the native runner without changing its contract."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from xnobrain.integrations.conversation_runner import ConversationRunnerMixin
from xnobrain.integrations.llm_router import LLMRouterAPIError, LLMRouterClient


class BlendExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.router = LLMRouterClient(data_dir=Path(self.temp.name))
        self.runner = ConversationRunnerMixin()
        self.runner.llm_router = self.router

    async def test_fallback_preparation_installs_ordered_native_failure_chain(self):
        await self.router.create_combo("duo", ["openai/fast", "anthropic/deep"])
        prepared = {"model": "duo", "message": "hello", "command": ["hermes"]}

        await self.runner._resolve_prepared_smart_route(prepared)
        agent = SimpleNamespace(base_url="https://router.invalid/v1", api_key="synthetic")
        self.runner._install_model_fallbacks(agent, prepared)

        self.assertEqual(prepared["model"], "openai/fast")
        self.assertEqual(prepared["command"], ["hermes", "--model", "openai/fast"])
        self.assertEqual(prepared.get("blend_route"), "duo")
        self.assertNotIn("smart_route", prepared)
        self.assertNotIn("route_reasoning", prepared)
        self.assertEqual([row["model"] for row in agent._fallback_chain], ["anthropic/deep"])
        self.assertEqual(agent._fallback_index, 0)
        self.assertTrue(all(row["api_key"] == "synthetic" for row in agent._fallback_chain))
        self.assertTrue(all(row["base_url"] == agent.base_url for row in agent._fallback_chain))

    async def test_round_robin_does_not_install_smart_classifier_or_fallback(self):
        await self.router.create_combo("rotate", ["openai/fast", "anthropic/deep"])
        await self.router.set_combo_strategy("rotate", strategy="round-robin")
        for expected in ("openai/fast", "anthropic/deep"):
            prepared = {
                "model": "rotate",
                "message": "hello",
                "command": ["hermes", "--model", "rotate"],
            }
            await self.runner._resolve_prepared_smart_route(prepared)
            self.assertEqual(prepared["model"], expected)
            self.assertEqual(prepared["command"], ["hermes", "--model", expected])
            self.assertNotIn("smart_route", prepared)
            self.assertNotIn("model_fallbacks", prepared)

    async def test_smart_route_keeps_its_tier_reasoning_and_step_routing(self):
        await self.router.create_combo("smart", ["openai/fast"])
        await self.router.set_combo_strategy(
            "smart",
            strategy="smart-route",
            smart_route={"quick": [{"model": "openai/fast", "reasoning": "low"}]},
        )
        prepared = {"model": "smart", "message": "hello", "command": ["hermes"]}
        await self.runner._resolve_prepared_smart_route(prepared)
        self.assertEqual(prepared["smart_route"], "smart")
        self.assertEqual(prepared["smart_route_tier"], "quick")
        self.assertEqual(prepared["route_reasoning"], "low")
        self.assertNotIn("model_fallbacks", prepared)

    async def test_physical_model_and_auto_skip_personal_blend_resolution(self):
        self.router.resolve_blend_route = AsyncMock()
        for model in ("auto", "openai/fast"):
            prepared = {"model": model, "command": ["hermes"]}
            await self.runner._resolve_prepared_smart_route(prepared)
            self.assertEqual(prepared, {"model": model, "command": ["hermes"]})
        self.router.resolve_blend_route.assert_not_awaited()

    def test_smart_wrapper_preserves_native_tool_argument_and_retry_behavior(self):
        for positional in (False, True):
            with self.subTest(positional=positional):
                build = Mock(
                    side_effect=lambda messages, tools_for_api=None: {
                        "messages": messages,
                        "tools": tools_for_api,
                    }
                )
                agent = SimpleNamespace(_build_api_kwargs=build)
                self.runner._resolve_smart_route_from_worker = Mock(return_value=None)
                self.runner._install_smart_route_step_routing(agent, None, 0, "smart")
                messages = [{"role": "user", "content": "hello"}]
                tools = [{"type": "function", "function": {"name": "read_file"}}]
                for _ in range(2):
                    result = (
                        agent._build_api_kwargs(messages, tools)
                        if positional
                        else agent._build_api_kwargs(messages, tools_for_api=tools)
                    )
                    self.assertIs(result["tools"], tools)
                self.runner._resolve_smart_route_from_worker.assert_not_called()
                next_messages = [*messages, {"role": "tool", "content": "result"}]
                result = agent._build_api_kwargs(next_messages, tools_for_api=[])
                self.assertEqual(result["tools"], [])
                self.runner._resolve_smart_route_from_worker.assert_called_once()

    async def test_missing_blend_does_not_rewrite_the_selection(self):
        prepared = {"model": "missing", "command": ["hermes"]}
        await self.runner._resolve_prepared_smart_route(prepared)
        self.assertEqual(prepared, {"model": "missing", "command": ["hermes"]})

    async def test_empty_blend_fails_before_starting_an_agent(self):
        await self.router.create_combo("empty", [])
        with self.assertRaises(LLMRouterAPIError) as caught:
            await self.runner._resolve_prepared_smart_route({"model": "empty"})
        self.assertEqual(caught.exception.code, "blend_model_unavailable")

    async def test_single_model_blend_does_not_retry_its_primary_as_a_fallback(self):
        await self.router.create_combo("single", ["openai/fast"])
        prepared = {"model": "single"}
        await self.runner._resolve_prepared_smart_route(prepared)
        self.assertEqual(prepared["model_fallbacks"], [])
        agent = SimpleNamespace()
        self.runner._install_model_fallbacks(agent, prepared)
        self.assertFalse(hasattr(agent, "_fallback_chain"))

    def test_smart_wrapper_does_not_swallow_builder_errors(self):
        build = Mock(side_effect=TypeError("invalid tool schema"))
        agent = SimpleNamespace(_build_api_kwargs=build)
        self.runner._install_smart_route_step_routing(agent, None, 0, "smart")
        with self.assertRaisesRegex(TypeError, "invalid tool schema"):
            agent._build_api_kwargs([], tools_for_api=[])
        build.assert_called_once_with([], tools_for_api=[])

    def test_smart_wrapper_preserves_legacy_one_argument_builder(self):
        agent = SimpleNamespace(_build_api_kwargs=lambda messages: {"messages": messages})
        self.runner._install_smart_route_step_routing(agent, None, 0, "smart")
        messages = [{"role": "user", "content": "hello"}]
        self.assertEqual(agent._build_api_kwargs(messages), {"messages": messages})
