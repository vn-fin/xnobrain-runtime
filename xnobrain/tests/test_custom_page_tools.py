"""Real engine registry dispatch is scoped, inert without a host-owned run."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from xnobrain.integrations.custom_page_tools import bind_run, install_tools, register_tools
from xnobrain.repositories import FileRepository
from xnobrain.services.conversations import ConversationsServiceMixin
from xnobrain.services.custom_page import CustomPageService
from xnobrain.tests.test_custom_page import news_manifest
from xnobrain.trusted_context import TrustedRequestContext


class CustomPageToolTests(unittest.TestCase):
    def test_real_registry_scopes_tools_to_parent_session_and_cleans_up(self):
        from tools.registry import registry

        with tempfile.TemporaryDirectory() as temporary:
            files = FileRepository(temporary, Path(temporary) / "profiles")
            (files.profiles_root / "research").mkdir()
            platform = SimpleNamespace(repository=files)
            service = CustomPageService(platform)
            context = TrustedRequestContext("owner", "tenant")
            run = {
                "id": "run_" + "a" * 32,
                "agent_id": "research",
                "conversation_id": "session",
                "status": "running",
                "actor_user_id": "owner",
                "actor_tenant_id": "tenant",
                "ownership_context": ConversationsServiceMixin._personal_context(),
            }
            files.create_conversation_context(
                files.live_profile_path("research"),
                {
                    **run["ownership_context"],
                    "agent_id": "research",
                    "conversation_id": "session",
                    "actor_user_id": "owner",
                    "actor_tenant_id": "tenant",
                },
            )
            files.put_conversation_run(run)
            register_tools()
            with bind_run(service, "research", "session", run["id"], context):
                agent = SimpleNamespace(tools=[], valid_tool_names=set())
                install_tools(agent, "session")
                self.assertEqual(len(agent.tools), 4)
                child = SimpleNamespace(tools=[], valid_tool_names=set(), _delegate_depth=1)
                install_tools(child, "session")
                self.assertEqual(child.tools, [])
                for forbidden in (
                    "custom_page_activate",
                    "custom_page_migrate",
                    "custom_page_delete",
                ):
                    self.assertNotIn(forbidden, agent.valid_tool_names)
                result = json.loads(
                    registry.dispatch("custom_page_inspect", {}, session_id="session")
                )
                self.assertTrue(result["success"])
                self.assertIsNone(result["data"]["page"])
                draft = {
                    "expected_revision": 0,
                    "idempotency_key": "tool-draft-01",
                    "manifest": news_manifest(),
                }
                result = json.loads(
                    registry.dispatch("custom_page_prepare", draft, session_id="session")
                )
                self.assertTrue(result["success"])
                self.assertTrue(result["data"]["approval_required"])
                self.assertEqual(service.read("research", context)["active"], 0)
                foreign = json.loads(
                    registry.dispatch("custom_page_inspect", {}, session_id="other")
                )
                self.assertIn("error", foreign)
                files.put_conversation_run({**run, "cancellation": {"requested": True}})
                self.assertFalse(
                    json.loads(registry.dispatch("custom_page_inspect", {}, session_id="session"))[
                        "success"
                    ]
                )
                files.put_conversation_run({**run, "status": "completed"})
                self.assertFalse(
                    json.loads(registry.dispatch("custom_page_inspect", {}, session_id="session"))[
                        "success"
                    ]
                )
            self.assertIn(
                "error",
                json.loads(registry.dispatch("custom_page_inspect", {}, session_id="session")),
            )
