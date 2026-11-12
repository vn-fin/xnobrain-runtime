"""Run-scoped personal Zalo tools: schemas, scope, sends, and bridge calls."""

import base64
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from xnobrain.integrations import channel_tools, zalo_tools

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
_TOOL_NAMES = (
    "zalo_status",
    "zalo_me",
    "zalo_list_friends",
    "zalo_list_groups",
    "zalo_send_image",
    "zalo_send_link",
    "zalo_react",
)


class _Repository:
    def __init__(self, threads=None):
        self._threads = threads or {}

    def threads(self, profile):
        return self._threads


class _FakeService:
    def __init__(self, profile, threads=None):
        self.repository = _Repository(threads)
        self.profile = profile
        self.bridge_calls = []
        self.bridge_result = {"ok": True, "status": 200, "data": {}}
        self.health = None
        self.recipient = {"status": "ok", "thread_id": "111", "name": "Nam"}
        self.group = {"status": "ok", "thread_id": "123", "name": "Team One"}
        self.approved = {"123"}
        self.media_max_mb = 10.0

    def profile_path(self, agent_id):
        return self.profile

    def resolve_recipient(self, agent_id, recipient):
        return self.recipient

    def resolve_zalo_group(self, agent_id, recipient):
        return self.group

    def approved_zalo_groups(self, agent_id):
        return set(self.approved)

    def zalo_media_max_mb(self, agent_id):
        return self.media_max_mb

    def zalo_bridge_health(self, agent_id):
        return self.health

    def call_zalo_bridge(
        self,
        agent_id,
        path,
        *,
        method="GET",
        body=None,
        timeout=2.0,
        max_bytes=None,
    ):
        self.bridge_calls.append((path, method, body))
        return self.bridge_result


class ZaloToolSchemaTests(unittest.TestCase):
    def test_every_zalo_schema_is_strict(self):
        self.assertEqual(set(zalo_tools._SCHEMAS), set(_TOOL_NAMES))
        for name in _TOOL_NAMES:
            function = zalo_tools._SCHEMAS[name]["function"]
            self.assertEqual(function["name"], name)
            parameters = function["parameters"]
            self.assertEqual(parameters["type"], "object")
            self.assertIs(parameters["additionalProperties"], False)

    def test_react_and_send_required_fields(self):
        react = zalo_tools._SCHEMAS["zalo_react"]["function"]["parameters"]
        self.assertEqual(
            set(react["required"]), {"thread_id", "message_id", "reaction"}
        )
        image = zalo_tools._SCHEMAS["zalo_send_image"]["function"]["parameters"]
        self.assertEqual(set(image["required"]), {"recipient", "path"})
        link = zalo_tools._SCHEMAS["zalo_send_link"]["function"]["parameters"]
        self.assertEqual(set(link["required"]), {"recipient", "url"})


class ZaloToolCallTests(unittest.TestCase):
    def setUp(self):
        channel_tools._BINDINGS.clear()
        channel_tools._PLATFORMS.clear()
        self.addCleanup(channel_tools._BINDINGS.clear)
        self.addCleanup(channel_tools._PLATFORMS.clear)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.profile = Path(self.temporary.name) / "profiles" / "agent-1"
        self.workspace = self.profile / "workspace"
        self.workspace.mkdir(parents=True)
        self.service = _FakeService(self.profile)

    def _bind(self, platforms=("zalo",), conversation_id="conv-1", threads=None):
        if threads is not None:
            self.service.repository = _Repository(threads)
        channel_tools._BINDINGS[conversation_id] = (
            self.service,
            "agent-1",
            conversation_id,
            "run-1",
            lambda approval: None,
            None,
        )
        channel_tools._PLATFORMS[conversation_id] = frozenset(platforms)

    def _call(self, name, args=None, conversation_id="conv-1"):
        return json.loads(
            zalo_tools.call(name, args or {}, session_id=conversation_id)
        )

    def _write_image(self, name="pic.png", content=_PNG):
        path = self.workspace / name
        path.write_bytes(content)
        return path

    def test_unbound_run_requires_authority(self):
        result = self._call("zalo_me", conversation_id="missing")

        self.assertEqual(result["error"]["code"], "channel_authority_required")
        self.assertEqual(self.service.bridge_calls, [])

    def test_platform_must_be_enabled_for_the_run(self):
        self._bind(platforms=("zalo_bot",))

        result = self._call("zalo_me")

        self.assertEqual(result["error"]["code"], "channel_platform_not_configured")
        self.assertEqual(self.service.bridge_calls, [])

    def test_status_reports_the_bridge_health(self):
        self._bind()
        self.service.health = {"status": "connected", "ownId": "42"}

        result = self._call("zalo_status")

        self.assertTrue(result["success"])
        self.assertEqual(
            result["data"],
            {"status": "connected", "connected": True, "own_id": "42"},
        )

    def test_me_returns_the_bridge_profile(self):
        self._bind()
        self.service.bridge_result = {
            "ok": True,
            "status": 200,
            "data": {"userId": "7", "displayName": "Me", "avatar": "https://a"},
        }

        result = self._call("zalo_me")

        self.assertTrue(result["success"])
        self.assertEqual(
            result["data"],
            {"user_id": "7", "display_name": "Me", "avatar": "https://a"},
        )
        self.assertEqual(self.service.bridge_calls, [("/me", "GET", None)])

    def test_friends_are_filtered_and_wrapped_as_untrusted(self):
        self._bind()
        self.service.bridge_result = {
            "ok": True,
            "status": 200,
            "data": {
                "friends": [
                    {"userId": "1", "displayName": "Nam", "zaloName": "nam"},
                    {"userId": "2", "displayName": "Lan", "zaloName": "lan"},
                    {"userId": "3", "displayName": "Nam Two", "zaloName": "nam2"},
                ]
            },
        }

        result = self._call("zalo_list_friends", {"query": "nam", "limit": 1})

        self.assertTrue(result["success"])
        self.assertEqual(
            result["data"]["friends"],
            [
                {
                    "trust": "untrusted_external",
                    "user_id": "1",
                    "display_name": "Nam",
                }
            ],
        )

    def test_groups_mark_owner_approved_groups(self):
        self._bind()
        self.service.bridge_result = {
            "ok": True,
            "status": 200,
            "data": {
                "groups": [
                    {"groupId": "123", "name": "Team One", "memberCount": 9},
                    {"groupId": "456", "name": "Other", "memberCount": 4},
                ]
            },
        }

        result = self._call("zalo_list_groups")

        self.assertTrue(result["success"])
        self.assertEqual(
            result["data"]["groups"],
            [
                {
                    "trust": "untrusted_external",
                    "group_id": "123",
                    "name": "Team One",
                    "member_count": 9,
                    "approved": True,
                },
                {
                    "trust": "untrusted_external",
                    "group_id": "456",
                    "name": "Other",
                    "member_count": 4,
                    "approved": False,
                },
            ],
        )

    def test_bridge_rate_limit_maps_to_the_channel_code(self):
        self._bind()
        self.service.bridge_result = {
            "ok": False,
            "status": 429,
            "data": None,
            "error": "rate_limited",
        }

        result = self._call("zalo_me")

        self.assertEqual(result["error"]["code"], "channel_rate_limited")

    def test_channel_run_lists_only_its_own_thread(self):
        self._bind(
            threads={"zalo": {"123": {"conversation_id": "conv-1"}}}
        )
        self.service.health = {"status": "connected", "ownId": "42"}

        friends = self._call("zalo_list_friends")
        groups = self._call("zalo_list_groups")
        status = self._call("zalo_status")
        me = self._call("zalo_me")

        self.assertEqual(friends["error"]["code"], "channel_thread_out_of_scope")
        self.assertEqual(groups["error"]["code"], "channel_thread_out_of_scope")
        self.assertTrue(status["success"])
        self.assertTrue(me["success"])

    def test_image_send_is_delivered_without_approval(self):
        self._bind()
        self._write_image()
        self.service.bridge_result = {
            "ok": True,
            "status": 200,
            "data": {"success": True, "messageId": "9"},
        }

        with mock.patch(
            "tools.approval._await_gateway_decision"
        ) as approval:
            result = self._call(
                "zalo_send_image",
                {"recipient": "Nam", "path": "pic.png", "caption": "look"},
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["message_id"], "9")
        path, method, body = self.service.bridge_calls[0]
        self.assertEqual((path, method), ("/send-media", "POST"))
        self.assertEqual(body["threadType"], "user")
        self.assertEqual(body["fileName"], "pic.png")
        self.assertEqual(body["caption"], "look")
        self.assertEqual(base64.b64decode(body["contentBase64"]), _PNG)
        approval.assert_not_called()

    def test_group_image_send_uses_the_group_thread_type(self):
        self._bind()
        self._write_image()

        result = self._call(
            "zalo_send_image",
            {
                "thread_type": "group",
                "recipient": "Team One",
                "path": "pic.png",
            },
        )

        self.assertTrue(result["success"])
        self.assertEqual(self.service.bridge_calls[0][2]["threadType"], "group")

    def test_channel_run_send_to_another_thread_is_out_of_scope(self):
        self._bind(threads={"zalo": {"999": {"conversation_id": "conv-1"}}})
        self._write_image()

        result = self._call(
            "zalo_send_image",
            {"recipient": "Nam", "path": "pic.png"},
        )

        self.assertEqual(result["error"]["code"], "channel_thread_out_of_scope")
        self.assertEqual(self.service.bridge_calls, [])

    def test_channel_run_send_to_its_own_thread_is_delivered(self):
        self._bind(threads={"zalo": {"111": {"conversation_id": "conv-1"}}})
        self._write_image()

        result = self._call(
            "zalo_send_image",
            {"recipient": "Nam", "path": "pic.png"},
        )

        self.assertTrue(result["success"])
        self.assertEqual(self.service.bridge_calls[0][0], "/send-media")

    def test_unapproved_group_image_is_refused_before_send(self):
        self._bind()
        self._write_image()
        self.service.group = {"status": "group_not_approved", "thread_id": "123"}

        result = self._call(
            "zalo_send_image",
            {
                "thread_type": "group",
                "recipient": "123",
                "path": "pic.png",
            },
        )

        self.assertEqual(result["error"]["code"], "channel_group_not_approved")
        self.assertEqual(self.service.bridge_calls, [])

    def test_unsafe_image_paths_are_refused(self):
        self._bind()
        outside = Path(self.temporary.name) / "outside.png"
        outside.write_bytes(_PNG)
        (self.workspace / "link.png").symlink_to(outside)
        self._write_image()

        cases = [
            (str((self.workspace / "pic.png").resolve()), "channel_path_unsafe"),
            ("../outside.png", "channel_path_unsafe"),
            ("link.png", "channel_path_unsafe"),
        ]
        for path, expected in cases:
            with self.subTest(path=path):
                result = self._call(
                    "zalo_send_image", {"recipient": "Nam", "path": path}
                )
                self.assertEqual(result["error"]["code"], expected)
        self.assertEqual(self.service.bridge_calls, [])

    def test_non_image_and_oversized_files_are_refused(self):
        self._bind()
        self._write_image("notes.png", b"not an image")

        invalid = self._call(
            "zalo_send_image", {"recipient": "Nam", "path": "notes.png"}
        )
        self.assertEqual(invalid["error"]["code"], "channel_image_invalid")

        self._write_image("big.png")
        self.service.media_max_mb = 0.0
        oversized = self._call(
            "zalo_send_image", {"recipient": "Nam", "path": "big.png"}
        )
        self.assertEqual(oversized["error"]["code"], "channel_image_too_large")
        self.assertEqual(self.service.bridge_calls, [])

    def test_link_send_is_delivered(self):
        self._bind()
        self.service.bridge_result = {
            "ok": True,
            "status": 200,
            "data": {"success": True, "messageId": "10"},
        }

        result = self._call(
            "zalo_send_link",
            {
                "recipient": "Nam",
                "url": "https://example.com/report",
                "message": "Report",
            },
        )

        self.assertTrue(result["success"])
        _, method, body = self.service.bridge_calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(body["url"], "https://example.com/report")
        self.assertEqual(body["message"], "Report")

    def test_invalid_link_is_rejected_before_send(self):
        self._bind()

        result = self._call(
            "zalo_send_link",
            {"recipient": "Nam", "url": "file:///etc/passwd"},
        )

        self.assertEqual(result["error"]["code"], "channel_invalid_request")
        self.assertEqual(self.service.bridge_calls, [])

    def test_react_sends_the_message_id(self):
        self._bind()

        result = self._call(
            "zalo_react",
            {
                "thread_id": "111",
                "message_id": "555",
                "reaction": "HEART",
            },
        )

        self.assertTrue(result["success"])
        _, method, body = self.service.bridge_calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(body["messageId"], "555")
        self.assertEqual(body["reaction"], "HEART")

    def test_group_react_uses_the_resolved_group(self):
        self._bind()

        result = self._call(
            "zalo_react",
            {
                "thread_type": "group",
                "thread_id": "Team One",
                "message_id": "555",
                "reaction": "LIKE",
            },
        )

        self.assertTrue(result["success"])
        self.assertEqual(self.service.bridge_calls[0][2]["threadType"], "group")

    def test_react_unknown_message_maps_to_not_found(self):
        self._bind()
        self.service.bridge_result = {
            "ok": False,
            "status": 404,
            "data": None,
            "error": "not_found",
        }

        result = self._call(
            "zalo_react",
            {"thread_id": "111", "message_id": "555", "reaction": "LIKE"},
        )

        self.assertEqual(result["error"]["code"], "channel_message_not_found")

    def test_unknown_arguments_are_rejected(self):
        self._bind()

        result = self._call("zalo_me", {"unexpected": True})

        self.assertEqual(result["error"]["code"], "channel_invalid_request")
        self.assertEqual(self.service.bridge_calls, [])

    def test_channel_run_react_to_another_thread_is_out_of_scope(self):
        self._bind(threads={"zalo": {"999": {"conversation_id": "conv-1"}}})

        result = self._call(
            "zalo_react",
            {"thread_id": "111", "message_id": "555", "reaction": "LIKE"},
        )

        self.assertEqual(result["error"]["code"], "channel_thread_out_of_scope")
        self.assertEqual(self.service.bridge_calls, [])


class ZaloToolRegistryTests(unittest.TestCase):
    """The engine rebuilds agent.tools from the registry between turns."""

    def setUp(self):
        channel_tools._BINDINGS.clear()
        channel_tools._PLATFORMS.clear()
        self.addCleanup(channel_tools._BINDINGS.clear)
        self.addCleanup(channel_tools._PLATFORMS.clear)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.profile = Path(self.temporary.name) / "profiles" / "agent-1"
        self.other_profile = Path(self.temporary.name) / "profiles" / "agent-2"
        self.profile.mkdir(parents=True)
        self.other_profile.mkdir(parents=True)
        self.service = _FakeService(self.profile)
        channel_tools._BINDINGS["conv-1"] = (
            self.service,
            "agent-1",
            "conv-1",
            "run-1",
            lambda approval: None,
            None,
        )
        channel_tools._PLATFORMS["conv-1"] = frozenset({"zalo"})

    def _agent(self, **kwargs):
        return SimpleNamespace(
            tools=[], valid_tool_names=set(), enabled_toolsets=["web"], **kwargs
        )

    def test_install_registers_tools_only_in_the_profile_scope(self):
        from hermes_constants import hermes_home_key
        from tools.registry import registry

        zalo_tools.install_tools(self._agent(), "conv-1")

        scope = hermes_home_key(self.profile)
        other_scope = hermes_home_key(self.other_profile)
        for name in _TOOL_NAMES:
            self.assertIsNotNone(registry.get_entry(name, scope=scope))
            self.assertIsNone(registry.get_entry(name, scope=other_scope))

    def test_install_enables_the_toolset_and_keeps_other_entries(self):
        agent = self._agent()
        agent.tools = [{"type": "function", "function": {"name": "read"}}]
        agent.valid_tool_names = {"read"}

        zalo_tools.install_tools(agent, "conv-1")

        self.assertEqual(agent.enabled_toolsets, ["web", "xnobrain_channels"])
        self.assertEqual(agent.valid_tool_names - set(_TOOL_NAMES), {"read"})
        names = [tool["function"]["name"] for tool in agent.tools]
        self.assertEqual(names[0], "read")
        self.assertEqual(set(names[1:]), set(_TOOL_NAMES))

    def test_install_skips_an_unbound_run(self):
        agent = self._agent()

        zalo_tools.install_tools(agent, "missing")

        self.assertEqual(agent.tools, [])
        self.assertEqual(agent.enabled_toolsets, ["web"])

    def test_unbound_call_still_returns_the_error_envelope(self):
        result = json.loads(zalo_tools.call("zalo_me", {}, session_id="missing"))

        self.assertEqual(result["error"]["code"], "channel_authority_required")


if __name__ == "__main__":
    unittest.main()
