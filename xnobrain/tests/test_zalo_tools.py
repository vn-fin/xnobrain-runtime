"""Run-scoped personal Zalo tools: schemas, scope, approvals, and bridge calls."""

import base64
import json
import tempfile
import unittest
from pathlib import Path
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

    def test_approved_image_send_is_delivered_after_approval(self):
        self._bind()
        self._write_image()
        self.service.bridge_result = {
            "ok": True,
            "status": 200,
            "data": {"success": True, "messageId": "9"},
        }

        with mock.patch.object(
            channel_tools,
            "_request_approval",
            return_value={"resolved": True, "choice": "once"},
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
        self.assertEqual(approval.call_args.args[2]["pattern_key"], "zalo.send_image")
        self.assertFalse(approval.call_args.args[2]["allow_permanent"])
        self.assertFalse(approval.call_args.args[2]["allow_session"])

    def test_group_image_send_uses_the_group_thread_type(self):
        self._bind()
        self._write_image()

        with mock.patch.object(
            channel_tools,
            "_request_approval",
            return_value={"resolved": True, "choice": "always"},
        ):
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

    def test_denied_image_send_never_calls_the_bridge(self):
        self._bind()
        self._write_image()

        with mock.patch.object(
            channel_tools,
            "_request_approval",
            return_value={"resolved": True, "choice": "deny"},
        ):
            result = self._call(
                "zalo_send_image",
                {"recipient": "Nam", "path": "pic.png"},
            )

        self.assertEqual(result["error"]["code"], "channel_send_denied")
        self.assertEqual(self.service.bridge_calls, [])

    def test_unapproved_group_image_is_refused_before_approval(self):
        self._bind()
        self._write_image()
        self.service.group = {"status": "group_not_approved", "thread_id": "123"}

        with mock.patch.object(channel_tools, "_request_approval") as approval:
            result = self._call(
                "zalo_send_image",
                {
                    "thread_type": "group",
                    "recipient": "123",
                    "path": "pic.png",
                },
            )

        self.assertEqual(result["error"]["code"], "channel_group_not_approved")
        approval.assert_not_called()
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
            with self.subTest(path=path), mock.patch.object(
                channel_tools, "_request_approval"
            ) as approval:
                result = self._call(
                    "zalo_send_image", {"recipient": "Nam", "path": path}
                )
                self.assertEqual(result["error"]["code"], expected)
                approval.assert_not_called()
        self.assertEqual(self.service.bridge_calls, [])

    def test_non_image_and_oversized_files_are_refused(self):
        self._bind()
        self._write_image("notes.png", b"not an image")

        with mock.patch.object(channel_tools, "_request_approval") as approval:
            invalid = self._call(
                "zalo_send_image", {"recipient": "Nam", "path": "notes.png"}
            )
        self.assertEqual(invalid["error"]["code"], "channel_image_invalid")
        approval.assert_not_called()

        self._write_image("big.png")
        self.service.media_max_mb = 0.0
        with mock.patch.object(channel_tools, "_request_approval") as approval:
            oversized = self._call(
                "zalo_send_image", {"recipient": "Nam", "path": "big.png"}
            )
        self.assertEqual(oversized["error"]["code"], "channel_image_too_large")
        approval.assert_not_called()
        self.assertEqual(self.service.bridge_calls, [])

    def test_link_send_is_approved_and_delivered(self):
        self._bind()
        self.service.bridge_result = {
            "ok": True,
            "status": 200,
            "data": {"success": True, "messageId": "10"},
        }

        with mock.patch.object(
            channel_tools,
            "_request_approval",
            return_value={"resolved": True, "choice": "once"},
        ) as approval:
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
        self.assertEqual(approval.call_args.args[2]["pattern_key"], "zalo.send_link")

    def test_invalid_link_is_rejected_before_approval(self):
        self._bind()

        with mock.patch.object(channel_tools, "_request_approval") as approval:
            result = self._call(
                "zalo_send_link",
                {"recipient": "Nam", "url": "file:///etc/passwd"},
            )

        self.assertEqual(result["error"]["code"], "channel_invalid_request")
        approval.assert_not_called()

    def test_react_requires_approval_and_sends_the_message_id(self):
        self._bind()

        with mock.patch.object(
            channel_tools,
            "_request_approval",
            return_value={"resolved": True, "choice": "once"},
        ) as approval:
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
        self.assertEqual(approval.call_args.args[2]["pattern_key"], "zalo.react")

    def test_group_react_uses_the_resolved_group(self):
        self._bind()

        with mock.patch.object(
            channel_tools,
            "_request_approval",
            return_value={"resolved": True, "choice": "once"},
        ):
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

        with mock.patch.object(
            channel_tools,
            "_request_approval",
            return_value={"resolved": True, "choice": "once"},
        ):
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

    def test_denied_react_never_calls_the_bridge(self):
        self._bind()

        with mock.patch.object(
            channel_tools,
            "_request_approval",
            return_value={"resolved": True, "choice": "deny"},
        ):
            result = self._call(
                "zalo_react",
                {"thread_id": "111", "message_id": "555", "reaction": "LIKE"},
            )

        self.assertEqual(result["error"]["code"], "channel_send_denied")
        self.assertEqual(self.service.bridge_calls, [])


if __name__ == "__main__":
    unittest.main()
