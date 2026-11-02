"""Cron blend routing preserves native execution and persisted virtual pins."""

import asyncio
import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, patch

from xnobrain.integrations import cron_blends
from xnobrain.integrations.llm_router import LLMRouterClient


class CronBlendTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.router = LLMRouterClient(
            base_url="https://router.invalid/v1", data_dir=Path(self.temp.name)
        )
        self.guard = patch.object(
            cron_blends.ConversationRunnerMixin, "_install_provider_runtime_request_guard"
        )
        self.guard.start()
        self.addCleanup(self.guard.stop)
        self.env = patch.dict(
            "os.environ", {"RUNTIME_LLM_API_KEY": "test-key", "RUNTIME_LLM_API_KEY_FILE": ""}
        )
        self.env.start()
        self.addCleanup(self.env.stop)

        class Agent:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

            def _build_api_kwargs(self, messages, tools_for_api=None):
                return {"model": self.model, "messages": messages, "tools": tools_for_api}

        self.agent_class = Agent

        def run(job, **kwargs):
            try:
                return Agent(
                    model=job.get("model", "duo"),
                    platform="cron",
                    reasoning_config=job.get("reasoning"),
                    base_url=self.router.base_url,
                    api_key="test-key",
                )
            except Exception as exc:
                return False, "", "", str(exc)

        self.scheduler = SimpleNamespace(run_job=run)
        self.install = cron_blends.install_blend_executor
        self.install(self.scheduler, Agent)
        self.installer_patch = patch.object(cron_blends, "install_blend_executor")
        self.installer_patch.start()
        self.addCleanup(self.installer_patch.stop)

    def fire(self, job, router=None, **kwargs):
        with cron_blends.blend_execution_scope(router or self.router):
            return self.scheduler.run_job(job, **kwargs)

    def create(self, strategy=None):
        asyncio.run(self.router.create_combo("duo", ["openai/fast", "anthropic/deep"]))
        if strategy:
            asyncio.run(self.router.set_combo_strategy("duo", strategy=strategy))

    def test_priority_keeps_job_pin_and_native_fallback_chain(self):
        self.create()
        job = {"model": "duo", "prompt": "Report", "xnobrain_pinned_connector": "blend"}
        before = copy.deepcopy(job)
        agent = self.fire(job)
        self.assertEqual(job, before)
        self.assertEqual(agent.model, "openai/fast")
        self.assertEqual([m["model"] for m in agent._fallback_chain], ["anthropic/deep"])
        self.assertEqual(agent._fallback_index, 0)
        self.assertEqual(agent._fallback_model["base_url"], self.router.base_url)
        self.assertEqual(agent._fallback_model["api_key"], "test-key")

    def test_round_robin_resolves_each_fire_including_inherited_agent_blend(self):
        self.create("round-robin")
        job = {"prompt": "Report"}  # The native executor inherits the agent model.
        self.assertEqual(self.fire(job).model, "openai/fast")
        self.assertEqual(self.fire(job).model, "anthropic/deep")
        self.assertNotIn("model", job)

    def test_concrete_auto_and_unknown_native_models_are_unchanged(self):
        self.router.resolve_blend_route = AsyncMock(return_value=None)
        for model in ("auto", "openai/fast", "native-model"):
            self.assertEqual(self.fire({"model": model}).model, model)
        self.router.resolve_blend_route.assert_awaited_once()

    def test_deleted_explicit_blend_becomes_native_failure_without_inference(self):
        result = self.fire({"model": "deleted", "xnobrain_pinned_connector": "blend"})
        self.assertEqual(result, (False, "", "", "cron_model_blend_unavailable"))

    def test_resolver_error_is_not_silently_routed_as_a_model(self):
        self.router.resolve_blend_route = AsyncMock(side_effect=ValueError("unavailable"))
        self.assertEqual(self.fire({"model": "duo"}), (False, "", "", "unavailable"))

    def test_smart_route_reuses_chat_step_routing_and_preserves_tools(self):
        self.router.resolve_blend_route = AsyncMock(
            return_value={"model": "openai/fast", "tier": "quick", "reasoning": "low"}
        )
        self.router.resolve_smart_route = AsyncMock(
            return_value={"model": "anthropic/deep", "tier": "deep", "reasoning": "high"}
        )
        agent = self.fire({"model": "duo", "prompt": "Report"}, extra_prompt="Extra context")
        self.assertEqual(self.router.resolve_blend_route.call_args.args[1], "Report\nExtra context")
        self.assertEqual(agent.reasoning_config, {"enabled": True, "effort": "low"})
        self.assertEqual(
            agent._build_api_kwargs([{"content": "first"}], [])["model"], "openai/fast"
        )
        result = agent._build_api_kwargs([{"content": "next step"}], [])
        self.assertEqual(result["model"], "anthropic/deep")
        self.assertEqual(result["tools"], [])
        self.assertEqual(agent.reasoning_config, {"enabled": True, "effort": "high"})
        self.router.resolve_smart_route.assert_awaited_once()

    def test_explicit_reasoning_is_preserved(self):
        self.router.resolve_blend_route = AsyncMock(
            return_value={"model": "openai/fast", "tier": "quick", "reasoning": "low"}
        )
        agent = self.fire({"model": "duo", "reasoning": {"effort": "medium"}})
        self.assertEqual(agent.reasoning_config, {"effort": "medium"})

    def test_concurrent_jobs_use_their_own_router_and_reset_scope(self):
        barrier = threading.Barrier(2)

        def worker(model):
            async def resolve(*args, **kwargs):
                barrier.wait(timeout=5)
                return {"model": model}

            router = SimpleNamespace(base_url=self.router.base_url, resolve_blend_route=resolve)
            agent = self.fire({"model": "duo"}, router=router)
            self.assertIsNone(cron_blends._ROUTER.get())
            self.assertIsNone(cron_blends._JOB.get())
            return agent.model

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(
                list(pool.map(worker, ["openai/a", "openai/b"])), ["openai/a", "openai/b"]
            )
        self.assertEqual(self.scheduler.run_job({"model": "duo"}).model, "duo")

    def test_hooks_install_once_and_chat_is_unchanged(self):
        run, init = self.scheduler.run_job, self.agent_class.__init__
        self.install(self.scheduler, self.agent_class)
        self.assertIs(self.scheduler.run_job, run)
        self.assertIs(self.agent_class.__init__, init)
        self.create()
        token = cron_blends._JOB.set({"model": "duo"})
        try:
            with patch.object(cron_blends, "install_blend_executor"):
                with cron_blends.blend_execution_scope(self.router):
                    agent = self.agent_class(model="duo", platform="api")
                    self.assertEqual(agent.model, "duo")
        finally:
            cron_blends._JOB.reset(token)

    def test_pinned_native_executor_resolves_blend_before_agent_inference(self):
        from cron import jobs, scheduler
        from hermes_constants import reset_hermes_home_override, set_hermes_home_override

        self.installer_patch.stop()
        self.create()
        home = Path(self.temp.name) / "profile"
        home.mkdir()
        (home / "config.yaml").write_text(
            "cron:\n  preflight: false\ncustom_providers:\n"
            "  - name: xnobrain\n    base_url: https://router.invalid/v1\n"
            "    api_key: test-key\n"
        )
        observed = []

        def run_conversation(agent, prompt):
            observed.append((agent.model, agent._fallback_model["model"]))
            return {"completed": True, "final_response": "Synthetic cron result"}

        self.agent_class.run_conversation = run_conversation
        token = set_hermes_home_override(str(home))
        try:
            with (
                jobs.use_cron_store(home),
                patch.object(scheduler, "run_job", scheduler.run_job),
                patch("run_agent.AIAgent", self.agent_class),
                patch("tools.mcp_tool.discover_mcp_tools", return_value=[]),
                patch(
                    "hermes_cli.runtime_provider.resolve_runtime_provider",
                    return_value={
                        "provider": "custom:xnobrain",
                        "api_key": "test-key",
                        "base_url": self.router.base_url,
                    },
                ),
                cron_blends.blend_execution_scope(self.router),
            ):
                result = scheduler.run_job(
                    {
                        "id": "blend-test",
                        "name": "Blend test",
                        "prompt": "Report",
                        "model": "duo",
                        "provider": "custom:xnobrain",
                        "base_url": self.router.base_url,
                        "deliver": "local",
                    }
                )
            self.assertTrue(result[0], result[3])
            self.assertEqual(result[2], "Synthetic cron result")
            self.assertEqual(observed, [("openai/fast", "anthropic/deep")])
        finally:
            reset_hermes_home_override(token)
