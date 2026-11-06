"""Gateway launcher installs the same correlation and credentials as Runtime turns."""

import os
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from xnobrain.integrations import gateway_cli
from xnobrain.integrations.conversation_credentials import (
    install_workspace_router_key_scope,
)
from xnobrain.integrations.llm_router_support import LLM_ROUTER_KEY_ENV


class FakeAIAgent:
    def __init__(self, *args, **kwargs):
        self.init_kwargs = kwargs
        self.session_id = kwargs.get("session_id", "")
        self.client = types.SimpleNamespace(_custom_headers={})
        self.async_client = types.SimpleNamespace(_custom_headers={})
        self._build_api_kwargs = lambda messages: messages

    def run_conversation(self, *args, **kwargs):
        from xnobrain.integrations.accounting_context import current_accounting

        context = current_accounting()
        return {
            "accounting": dict(context["headers"]) if context else {},
            "client": dict(self.client._custom_headers),
            "request": self._build_api_kwargs([]),
        }


class GatewayCliTests(unittest.TestCase):
    def test_turns_get_profile_headers_router_credentials_and_fresh_runs(self):
        fake_module = types.ModuleType("run_agent")
        fake_module.AIAgent = FakeAIAgent
        profile = Path("/opt/data/agent/worker-a")
        environment = {
            "RUNTIME_ACCOUNTING_MODE": "router",
            "RUNTIME_LLM_API_KEY": "synthetic-workload",
            "RUNTIME_LLM_API_KEY_FILE": "",
            "RUNTIME_LLM_ROUTER_URL": "https://router.invalid/v1",
        }
        with (
            mock.patch.dict(sys.modules, {"run_agent": fake_module}),
            mock.patch.dict(os.environ, environment),
            mock.patch.object(gateway_cli, "_current_profile", return_value=profile),
            mock.patch("xnobrain.integrations.image_tools.install_image_tools"),
            mock.patch(
                "xnobrain.integrations.conversation_runner.ConversationRunnerMixin."
                "_install_provider_runtime_request_guard"
            ),
        ):
            gateway_cli.install_gateway_turns()
            agent = fake_module.AIAgent(model="synthetic/model", session_id="conversation-1")

            first = agent.run_conversation("first")
            second = agent.run_conversation("second")

        self.assertEqual(agent.init_kwargs["api_key"], "synthetic-workload")
        self.assertEqual(agent.init_kwargs["base_url"], "https://router.invalid/v1")
        self.assertEqual(agent.init_kwargs["provider"], "custom:xnobrain")
        self.assertEqual(agent._xnobrain_gateway_binding["agent_id"], "worker-a")
        self.assertEqual(first["accounting"]["X-GoRouter-Agent-Id"], "worker-a")
        self.assertEqual(
            first["accounting"]["X-GoRouter-Conversation-Id"], "conversation-1"
        )
        self.assertEqual(
            first["client"]["X-GoRouter-Conversation-Id"], "conversation-1"
        )
        self.assertNotEqual(
            first["accounting"]["X-GoRouter-Run-Id"],
            second["accounting"]["X-GoRouter-Run-Id"],
        )
        self.assertEqual(
            second["client"]["X-GoRouter-Run-Id"],
            second["accounting"]["X-GoRouter-Run-Id"],
        )

    def test_workspace_key_is_visible_in_every_profile_scope(self):
        from agent import secret_scope

        with TemporaryDirectory() as directory:
            profile = Path(directory) / "worker-a"
            profile.mkdir()
            (profile / ".env").write_text("PROFILE_LOCAL_KEY=own-profile\n", encoding="utf-8")
            original = install_workspace_router_key_scope()
            try:
                with mock.patch.dict(
                    os.environ,
                    {
                        "RUNTIME_LLM_API_KEY": "synthetic-workload",
                        "RUNTIME_LLM_API_KEY_FILE": "",
                        "FOREIGN_PROVIDER_KEY": "must-not-leak",
                    },
                ):
                    secrets = secret_scope.build_profile_secret_scope(profile)
                self.assertEqual(secrets[LLM_ROUTER_KEY_ENV], "synthetic-workload")
                self.assertEqual(secrets["PROFILE_LOCAL_KEY"], "own-profile")
                self.assertNotIn("FOREIGN_PROVIDER_KEY", secrets)
            finally:
                secret_scope.build_profile_secret_scope = original


if __name__ == "__main__":
    unittest.main()
