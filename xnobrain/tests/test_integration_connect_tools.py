"""Focused tests for the Big Brother integration connect tool."""

from __future__ import annotations

import json
import unittest
import uuid
from types import SimpleNamespace
from unittest import mock

from xnobrain.integrations import integration_connect_tools as connect
from xnobrain.integrations.hermes_support import _integration_connect_event
from xnobrain.services.base import ServiceError
from xnobrain.services.conversation_authority import HostAuthority
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


class FakeChannelsService:
    def __init__(self):
        self.calls = []
        self.error = None

    def connect_target(self, agent_id, *, trusted=None, host_authority=None):
        self.calls.append((agent_id, trusted, host_authority))
        if self.error is not None:
            raise self.error
        return object()


class IntegrationConnectToolTests(unittest.TestCase):
    def setUp(self):
        self.service = FakeExtensionsService()
        self.channels = FakeChannelsService()
        self.trusted = TrustedRequestContext(subject="person-1", tenant_id="tenant-1")
        self.session = uuid.uuid4().hex

    def _call(self, args, *, host_authority=None):
        with connect.bind_run(
            self.service,
            self.session,
            self.trusted,
            channels_service=self.channels,
            host_authority=host_authority,
        ):
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
        self.assertEqual(request["url"], "https://mcp.example.com/mcp")

    def test_channel_platform_is_server_derived_and_secret_free(self):
        for platform, display_name, auth_kind in (
            ("zalo", "Zalo", "qr"),
            ("zalo_bot", "Zalo Bot", "api_key"),
        ):
            with self.subTest(platform=platform):
                self.channels.calls.clear()
                result = self._call(
                    {
                        "kind": "channel",
                        "platform": platform,
                        "reason": "Connect this channel.",
                    }
                )
                self.assertTrue(result["success"])
                request = result["connect_request"]
                self.assertEqual(
                    set(request),
                    {
                        "id",
                        "kind",
                        "agent_id",
                        "display_name",
                        "auth_kind",
                        "platform",
                        "reason",
                    },
                )
                self.assertEqual(request["kind"], "channel")
                self.assertEqual(request["agent_id"], "big-brother")
                self.assertEqual(request["display_name"], display_name)
                self.assertEqual(request["auth_kind"], auth_kind)
                self.assertEqual(request["platform"], platform)
                self.assertEqual(self.channels.calls[0][0], "big-brother")
                self.assertEqual(self.service.list_plugins_calls, [])

    def test_channel_rejects_mcp_fields_extra_secrets_and_bad_platform(self):
        for args in (
            {"kind": "channel", "platform": "zalo", "url": "https://mcp.example.com"},
            {
                "kind": "channel",
                "platform": "zalo",
                "catalog_id": "notion",
            },
            {"kind": "channel", "platform": "zalo", "api_key": "secret"},
            {"kind": "channel", "platform": "signal"},
            {"kind": "channel"},
        ):
            with self.subTest(args=args):
                result = self._call(args)
                self.assertFalse(result["success"])
                self.assertEqual(result["error"]["code"], "invalid_integration_connect_request")

    def test_channel_path_does_not_require_extension_feature(self):
        with mock.patch.object(
            self.service,
            "connect_target",
            side_effect=ServiceError("Agent extensions are disabled", status=404, code="not_found"),
        ):
            result = self._call({"kind": "channel", "platform": "zalo"})
        self.assertTrue(result["success"])
        self.assertEqual(result["connect_request"]["kind"], "channel")

    def test_host_authority_mapping_or_duck_object_is_rejected(self):
        for authority in (
            {
                "subject": "person-1",
                "tenant_id": "tenant-1",
                "agent_id": "big-brother",
                "platform": "zalo",
            },
            SimpleNamespace(
                subject="person-1",
                tenant_id="tenant-1",
                organization_id="",
                agent_id="big-brother",
                platform="zalo",
            ),
        ):
            with self.subTest(authority=authority):
                with connect.bind_run(
                    self.service,
                    self.session,
                    None,
                    channels_service=self.channels,
                    host_authority=authority,
                ) as bound:
                    self.assertFalse(bound)
                result = json.loads(
                    connect.call(
                        connect.TOOL_NAME,
                        {"kind": "channel", "platform": "zalo"},
                        session_id=self.session,
                    )
                )
                self.assertFalse(result["success"])
                self.assertEqual(result["error"]["code"], "integration_connect_authority_required")

    def test_channel_target_rejects_mismatched_agent(self):
        self.channels.error = ServiceError(
            "owner mismatch", status=403, code="channel_owner_mismatch"
        )
        result = self._call({"kind": "channel", "platform": "zalo", "agent_id": "other-agent"})
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "channel_owner_mismatch")

    def test_host_authority_allows_channel_only_and_not_mcp(self):
        authority = HostAuthority(
            subject="person-1",
            tenant_id="tenant-1",
            organization_id="",
            agent_id="big-brother",
            platform="zalo",
        )
        with connect.bind_run(
            self.service,
            self.session,
            None,
            channels_service=self.channels,
            host_authority=authority,
        ):
            channel = json.loads(
                connect.call(
                    connect.TOOL_NAME,
                    {"kind": "channel", "platform": "zalo_bot"},
                    session_id=self.session,
                )
            )
            mcp = json.loads(
                connect.call(
                    connect.TOOL_NAME,
                    {"kind": "catalog_mcp", "catalog_id": "notion"},
                    session_id=self.session,
                )
            )
        self.assertTrue(channel["success"])
        self.assertFalse(mcp["success"])
        self.assertEqual(mcp["error"]["code"], "trusted_context_required")

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

    def test_channel_event_accepts_exact_contract(self):
        event = _integration_connect_event(
            json.dumps(
                {
                    "success": True,
                    "connect_request": {
                        "id": "b" * 32,
                        "kind": "channel",
                        "agent_id": "big-brother",
                        "display_name": "Zalo Bot",
                        "auth_kind": "api_key",
                        "platform": "zalo_bot",
                        "reason": "Connect it.",
                    },
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
                "platform",
                "reason",
            },
        )
        self.assertNotIn("secret", json.dumps(event))

    def test_channel_event_rejects_extra_secret_or_mcp_fields(self):
        for extra in (
            {"url": "https://mcp.example.com"},
            {"catalog_id": "notion"},
            {"api_key": "secret"},
            {"token": "secret"},
            {"unknown": "value"},
        ):
            with self.subTest(extra=extra):
                self.assertIsNone(
                    _integration_connect_event(
                        json.dumps(
                            {
                                "success": True,
                                "connect_request": {
                                    "id": "b" * 32,
                                    "kind": "channel",
                                    "agent_id": "big-brother",
                                    "display_name": "Zalo Bot",
                                    "auth_kind": "api_key",
                                    "platform": "zalo_bot",
                                    **extra,
                                },
                            }
                        )
                    )
                )

    def test_channel_event_rejects_bad_platform_auth_or_display(self):
        for platform, auth_kind, display_name in (
            ("telegram", "qr", "Zalo"),
            ("zalo", "oauth", "Zalo"),
            ("zalo_bot", "none", "Zalo Bot"),
            ("zalo", "api_key", "Zalo"),
            ("zalo_bot", "qr", "Zalo Bot"),
            ("zalo", "qr", "Untrusted Label"),
        ):
            with self.subTest(platform=platform, auth_kind=auth_kind):
                self.assertIsNone(
                    _integration_connect_event(
                        json.dumps(
                            {
                                "success": True,
                                "connect_request": {
                                    "id": "c" * 32,
                                    "kind": "channel",
                                    "agent_id": "big-brother",
                                    "display_name": display_name,
                                    "auth_kind": auth_kind,
                                    "platform": platform,
                                },
                            }
                        )
                    )
                )

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
        self.assertIn("kind='channel'", tools.CONNECT_GUIDANCE)
        self.assertIn("platform='zalo'", tools.CONNECT_GUIDANCE)
        self.assertIn("platform='zalo_bot'", tools.CONNECT_GUIDANCE)

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
