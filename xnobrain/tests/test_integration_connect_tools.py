"""Focused tests for the Big Brother integration connect tool."""

from __future__ import annotations

import json
import unittest
import uuid
from types import SimpleNamespace

from xnobrain.integrations import integration_connect_tools as connect
from xnobrain.integrations.hermes_support import _integration_connect_event
from xnobrain.services.base import ServiceError
from xnobrain.trusted_context import TrustedRequestContext


def _catalog_entry(catalog_id="notion", *, installability="installable", auth="oauth"):
    return {
        "id": catalog_id,
        "name": catalog_id.title(),
        "installability": installability,
        "mcp": {"transport": "http", "auth": {"kind": auth}},
        "recipe_revision": 2,
    }


class FakeExtensionsService:
    def __init__(self):
        self.agent_missing = False
        self.catalog_data = {
            "entries": [
                _catalog_entry("notion"),
                _catalog_entry("linear"),
                _catalog_entry("desktop-only", installability="unsupported"),
            ],
            "engine_capabilities": {"mcp_profile_isolation": True},
        }
        self.inventory = {
            "agent_id": "big-brother",
            "plugins": [
                {
                    "id": "sample",
                    "name": "Sample Plugin",
                    "mcp_servers": [
                        {
                            "name": "docs",
                            "auth_kind": "oauth",
                            "plugin_revision": "c" * 40,
                        }
                    ],
                }
            ],
            "mcp_servers": [],
        }
        self.list_plugins_calls = []

    def connect_target(self, agent_id, trusted):
        if self.agent_missing:
            raise ServiceError("missing", status=404, code="not_found")
        return object()

    def catalog(self):
        return self.catalog_data

    def list_plugins(self, agent_id, trusted, *, recover_publication=True):
        self.list_plugins_calls.append((agent_id, recover_publication))
        return self.inventory


class IntegrationConnectToolTests(unittest.TestCase):
    def setUp(self):
        self.service = FakeExtensionsService()
        self.trusted = TrustedRequestContext(subject="person-1", tenant_id="tenant-1")
        self.session = uuid.uuid4().hex

    def _call(self, args):
        with connect.bind_run(self.service, self.session, self.trusted):
            return json.loads(connect.call(connect.TOOL_NAME, args, session_id=self.session))

    def test_arguments_reject_headers_and_api_key(self):
        for extra in ({"headers": {"Authorization": "secret"}}, {"api_key": "secret"}):
            result = self._call({"kind": "catalog_mcp", "catalog_id": "notion", **extra})
            self.assertFalse(result["success"])
            self.assertEqual(result["error"]["code"], "invalid_integration_connect_request")

    def test_unknown_agent_is_reported(self):
        self.service.agent_missing = True
        result = self._call({"kind": "catalog_mcp", "catalog_id": "notion"})
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "agent_not_found")

    def test_unknown_catalog_id_returns_candidates(self):
        result = self._call({"kind": "catalog_mcp", "catalog_id": "notoin"})
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "catalog_unknown")
        self.assertIn("notion", result["error"]["candidates"])

    def test_unsupported_catalog_entry_is_rejected(self):
        result = self._call({"kind": "catalog_mcp", "catalog_id": "desktop-only"})
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "catalog_unsupported")

    def test_catalog_entry_is_rejected_when_engine_lacks_isolation(self):
        self.service.catalog_data["engine_capabilities"]["mcp_profile_isolation"] = False
        result = self._call({"kind": "catalog_mcp", "catalog_id": "notion"})
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "catalog_unsupported")

    def test_unknown_plugin_server_is_reported(self):
        result = self._call({"kind": "plugin_mcp", "plugin_id": "sample", "server_name": "missing"})
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "plugin_mcp_unknown")
        self.assertEqual(self.service.list_plugins_calls[-1][1], False)

    def test_custom_url_must_be_public_https_without_query_or_userinfo(self):
        for url in (
            "http://mcp.example.com/mcp",
            "https://mcp.example.com/mcp?token=1",
            "https://user:pass@mcp.example.com/mcp",
            "https://localhost/mcp",
        ):
            result = self._call({"kind": "custom_mcp", "url": url})
            self.assertFalse(result["success"], url)
            self.assertEqual(result["error"]["code"], "invalid_url")

    def test_custom_url_longer_than_200_chars_is_rejected(self):
        url = "https://mcp.example.com/" + ("a" * 200)
        self.assertGreater(len(url), 200)
        result = self._call({"kind": "custom_mcp", "url": url})
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "invalid_url")

    def test_catalog_success_builds_request_from_catalog_data(self):
        result = self._call(
            {
                "kind": "catalog_mcp",
                "catalog_id": "notion",
                "reason": "You asked to connect Notion.",
            }
        )
        self.assertTrue(result["success"])
        request = result["connect_request"]
        self.assertEqual(len(request["id"]), 32)
        self.assertEqual(request["kind"], "catalog_mcp")
        self.assertEqual(request["agent_id"], "big-brother")
        self.assertEqual(request["display_name"], "Notion")
        self.assertEqual(request["auth_kind"], "oauth")
        self.assertEqual(request["catalog_id"], "notion")
        self.assertEqual(request["recipe_revision"], 2)
        self.assertEqual(request["reason"], "You asked to connect Notion.")
        self.assertIn("connect card", result["instruction"])
        self.assertNotIn("headers", request)

    def test_plugin_success_uses_inventory_data(self):
        result = self._call(
            {
                "kind": "plugin_mcp",
                "plugin_id": "sample",
                "server_name": "docs",
            }
        )
        self.assertTrue(result["success"])
        request = result["connect_request"]
        self.assertEqual(request["display_name"], "Sample Plugin")
        self.assertEqual(request["auth_kind"], "oauth")
        self.assertEqual(request["plugin_id"], "sample")
        self.assertEqual(request["server_name"], "docs")
        self.assertEqual(request["plugin_revision"], "c" * 40)

    def test_custom_success_uses_url_host_and_no_auth(self):
        result = self._call({"kind": "custom_mcp", "url": "https://mcp.example.com/mcp"})
        self.assertTrue(result["success"])
        request = result["connect_request"]
        self.assertEqual(request["display_name"], "mcp.example.com")
        self.assertEqual(request["auth_kind"], "none")
        self.assertEqual(request["server_name"], "mcp-example-com")
        self.assertEqual(request["url"], "https://mcp.example.com/mcp")

    def test_custom_server_name_is_derived_from_host(self):
        result = self._call({"kind": "custom_mcp", "url": "https://MCP.Example.com/mcp"})
        self.assertTrue(result["success"])
        self.assertEqual(result["connect_request"]["server_name"], "mcp-example-com")

    def test_custom_explicit_server_name_is_used(self):
        result = self._call(
            {
                "kind": "custom_mcp",
                "url": "https://mcp.example.com/mcp",
                "server_name": "my_server-1",
            }
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["connect_request"]["server_name"], "my_server-1")

    def test_custom_invalid_server_name_is_rejected(self):
        for server_name in ("MCP", "-leading", "with space", "a" * 65):
            result = self._call(
                {
                    "kind": "custom_mcp",
                    "url": "https://mcp.example.com/mcp",
                    "server_name": server_name,
                }
            )
            self.assertFalse(result["success"], server_name)
            self.assertEqual(result["error"]["code"], "invalid_server_name")

    def test_manage_mcp_payload_for_allowed_agent(self):
        result = self._call({"kind": "manage_mcp"})
        self.assertTrue(result["success"])
        request = result["connect_request"]
        self.assertEqual(len(request["id"]), 32)
        self.assertEqual(request["kind"], "manage_mcp")
        self.assertEqual(request["agent_id"], "big-brother")
        self.assertEqual(request["display_name"], "big-brother")
        self.assertNotIn("auth_kind", request)
        self.assertNotIn("url", request)

    def test_manage_mcp_unknown_agent_is_reported(self):
        self.service.agent_missing = True
        result = self._call({"kind": "manage_mcp"})
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "agent_not_found")

    def test_install_tools_requires_binding_and_skips_delegated_children(self):
        profile = "/tmp/connect-test-profile"
        unbound = SimpleNamespace(tools=[], valid_tool_names=set())
        connect.install_tools(unbound, "not-bound", profile)
        self.assertEqual(unbound.tools, [])

        bound = SimpleNamespace(tools=[], valid_tool_names=set(), enabled_toolsets=["web"])
        with connect.bind_run(self.service, self.session, self.trusted):
            connect.install_tools(bound, self.session, profile)
            self.assertEqual(len(bound.tools), 1)
            self.assertIn(connect.TOOL_NAME, bound.valid_tool_names)
            self.assertIn(connect.TOOLSET, bound.enabled_toolsets)

            from hermes_constants import hermes_home_key
            from tools.registry import registry

            scope = hermes_home_key(profile)
            self.assertIsNotNone(registry.get_entry(connect.TOOL_NAME, scope=scope))
            connect.install_tools(
                SimpleNamespace(tools=[], valid_tool_names=set()), self.session, profile
            )

            child = SimpleNamespace(tools=[], valid_tool_names=set(), _delegate_depth=1)
            connect.install_tools(child, self.session, profile)
            self.assertEqual(child.tools, [])


class IntegrationConnectEventTests(unittest.TestCase):
    def test_valid_result_keeps_only_whitelisted_keys(self):
        event = _integration_connect_event(
            json.dumps(
                {
                    "success": True,
                    "connect_request": {
                        "id": "a" * 32,
                        "kind": "catalog_mcp",
                        "agent_id": "big-brother",
                        "display_name": "Notion",
                        "auth_kind": "oauth",
                        "catalog_id": "notion",
                        "recipe_revision": 2,
                        "headers": {"Authorization": "secret"},
                        "api_key": "secret",
                    },
                    "instruction": "finish",
                }
            )
        )
        self.assertEqual(event["event"], "integration.connect_requested")
        self.assertEqual(
            set(event["request"]),
            {
                "id",
                "kind",
                "agent_id",
                "display_name",
                "auth_kind",
                "catalog_id",
                "recipe_revision",
            },
        )
        self.assertNotIn("headers", json.dumps(event))
        self.assertNotIn("secret", json.dumps(event))

    def test_manage_mcp_result_needs_no_auth_kind(self):
        event = _integration_connect_event(
            json.dumps(
                {
                    "success": True,
                    "connect_request": {
                        "id": "a" * 32,
                        "kind": "manage_mcp",
                        "agent_id": "big-brother",
                        "display_name": "Big Brother",
                    },
                }
            )
        )
        self.assertIsNotNone(event)
        self.assertEqual(event["request"]["kind"], "manage_mcp")
        self.assertNotIn("auth_kind", event["request"])

    def test_failed_or_malformed_results_emit_nothing(self):
        self.assertIsNone(_integration_connect_event(json.dumps({"success": False})))
        self.assertIsNone(_integration_connect_event("not json"))
        self.assertIsNone(
            _integration_connect_event(
                json.dumps({"success": True, "connect_request": {"id": "a" * 32}})
            )
        )


class ConnectGuidanceTests(unittest.TestCase):
    def test_guidance_and_description_steer_away_from_cli_and_chat_secrets(self):
        from xnobrain.integrations import integration_connect_tools as tools

        self.assertIn(tools.TOOL_NAME, tools.CONNECT_GUIDANCE)
        self.assertIn("agent mcp add", tools.CONNECT_GUIDANCE)
        self.assertIn("never ask", tools.CONNECT_GUIDANCE)

    def test_guidance_and_description_try_catalog_before_custom(self):
        from xnobrain.integrations import integration_connect_tools as tools

        description = tools._function_schema()["description"]
        for text in (tools.CONNECT_GUIDANCE, description):
            self.assertIn("kind='catalog_mcp'", text)
            self.assertIn("catalog_unknown", text)
            self.assertIn("Hermes plugin index", text)
            self.assertLess(text.index("catalog_mcp"), text.index("custom_mcp"))


if __name__ == "__main__":
    unittest.main()
