"""Run-scoped personal Zalo tools: schemas, scope, sends, and bridge calls."""

import base64
import json
import tempfile
import time
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
    "zalo_list_conversations",
    "zalo_read_history",
    "zalo_read_group_history",
    "zalo_search_messages",
    "zalo_group_info",
    "zalo_user_info",
    "zalo_unread",
    "zalo_list_reminders",
    "zalo_list_notes",
    "zalo_poll_detail",
    "zalo_create_poll",
    "zalo_create_note",
    "zalo_create_reminder",
    "zalo_pin_conversation",
    "zalo_mute",
    "zalo_labels",
    "zalo_mark_unread",
    "zalo_group_members",
    "zalo_group_settings",
    "zalo_group_roles",
    "zalo_group_pending",
    "zalo_group_link",
    "zalo_create_group",
    "zalo_friend_requests",
    "zalo_friend_alias",
    "zalo_quick_reply",
    "zalo_send_image",
    "zalo_send_file",
    "zalo_send_video",
    "zalo_send_voice",
    "zalo_send_sticker",
    "zalo_send_contact_card",
    "zalo_forward",
    "zalo_undo_message",
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
        self.dm_allowed = True
        self.readable_threads = ["111", "123"]
        self.threads = {
            "111": {
                "status": "ok",
                "thread_id": "111",
                "name": "Nam",
                "type": "dm",
            },
            "123": {
                "status": "ok",
                "thread_id": "123",
                "name": "Team One",
                "type": "group",
            },
        }

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

    def zalo_readable_threads(self, agent_id):
        return list(self.readable_threads)

    def resolve_zalo_thread(self, agent_id, thread_id):
        entry = self.threads.get(str(thread_id))
        if entry is None:
            return {
                "status": "not_found",
                "thread_id": str(thread_id),
                "name": "",
                "type": "",
            }
        if entry["type"] == "dm" and not self.dm_allowed:
            return {
                "status": "dm_policy_blocked",
                "thread_id": str(thread_id),
                "name": "",
                "type": "dm",
            }
        return entry

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
        self.assertEqual(set(zalo_tools._MODELS), set(_TOOL_NAMES))
        self.assertEqual(set(zalo_tools._TOOL_HANDLERS), set(_TOOL_NAMES))
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

    def test_send_extra_schemas_require_their_scope_fields(self):
        expected = {
            "zalo_send_file": {"recipient", "path"},
            "zalo_send_video": {"recipient", "path"},
            "zalo_send_voice": {"recipient", "path"},
            "zalo_send_sticker": {"recipient", "keyword"},
            "zalo_send_contact_card": {"recipient", "user_id"},
            "zalo_forward": {"source_recipient", "message_id", "recipient"},
            "zalo_undo_message": {"recipient", "message_id"},
        }
        for name, required in expected.items():
            with self.subTest(name=name):
                parameters = zalo_tools._SCHEMAS[name]["function"]["parameters"]
                self.assertEqual(set(parameters.get("required") or []), required)
                self.assertIs(parameters["additionalProperties"], False)

    def test_send_extra_descriptions_ask_for_in_chat_confirmation(self):
        for name in (
            "zalo_send_file",
            "zalo_send_video",
            "zalo_send_voice",
            "zalo_send_sticker",
            "zalo_send_contact_card",
            "zalo_forward",
            "zalo_undo_message",
        ):
            with self.subTest(name=name):
                description = zalo_tools._SCHEMAS[name]["function"]["description"]
                self.assertIn("explicit confirmation in chat", description)

    def test_read_schemas_require_their_scope_fields(self):
        expected = {
            "zalo_read_history": {"recipient"},
            "zalo_read_group_history": {"recipient"},
            "zalo_search_messages": {"query"},
            "zalo_group_info": {"recipient"},
            "zalo_list_reminders": {"recipient"},
            "zalo_list_notes": {"recipient"},
            "zalo_poll_detail": {"recipient", "poll_id"},
        }
        for name, required in expected.items():
            with self.subTest(name=name):
                parameters = zalo_tools._SCHEMAS[name]["function"]["parameters"]
                self.assertEqual(set(parameters.get("required") or []), required)
                self.assertIs(parameters["additionalProperties"], False)
        empty = zalo_tools._SCHEMAS["zalo_unread"]["function"]["parameters"]
        self.assertEqual(empty["properties"], {})
        self.assertIs(empty["additionalProperties"], False)
        user = zalo_tools._SCHEMAS["zalo_user_info"]["function"]["parameters"]
        self.assertEqual(
            set(user["properties"]), {"user_id", "phone", "username"}
        )
        self.assertNotIn("required", user)


class _ZaloToolTestBase(unittest.TestCase):
    """Shared binding, workspace, and bridge fake for the Zalo tool tests."""

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

    def _write_file(self, name, content):
        path = self.workspace / name
        path.write_bytes(content)
        return path

    def _write_image(self, name="pic.png", content=_PNG):
        return self._write_file(name, content)


class ZaloToolCallTests(_ZaloToolTestBase):
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


class ZaloReadToolTests(unittest.TestCase):
    """P-B read tools: stored history, group reads, and channel-run scope."""

    def setUp(self):
        channel_tools._BINDINGS.clear()
        channel_tools._PLATFORMS.clear()
        self.addCleanup(channel_tools._BINDINGS.clear)
        self.addCleanup(channel_tools._PLATFORMS.clear)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.profile = Path(self.temporary.name) / "profiles" / "agent-1"
        self.profile.mkdir(parents=True)
        self.service = _FakeService(self.profile)

    def _bind(self, threads=None):
        if threads is not None:
            self.service.repository = _Repository(threads)
        channel_tools._BINDINGS["conv-1"] = (
            self.service,
            "agent-1",
            "conv-1",
            "run-1",
            lambda approval: None,
            None,
        )
        channel_tools._PLATFORMS["conv-1"] = frozenset({"zalo"})

    def _call(self, name, args=None, conversation_id="conv-1"):
        return json.loads(zalo_tools.call(name, args or {}, session_id=conversation_id))

    def _ok(self, data):
        self.service.bridge_result = {"ok": True, "status": 200, "data": data}

    def test_conversations_include_only_readable_threads(self):
        self._bind()
        self._ok(
            {
                "conversations": [
                    {
                        "kind": "group",
                        "id": "123",
                        "name": "Team One",
                        "member_count": 5,
                        "pinned": True,
                        "archived": False,
                    },
                    {
                        "kind": "dm",
                        "id": "111",
                        "name": "Nam",
                        "member_count": 0,
                        "pinned": False,
                        "archived": True,
                    },
                    {
                        "kind": "group",
                        "id": "999",
                        "name": "Other",
                        "member_count": 2,
                        "pinned": False,
                        "archived": False,
                    },
                ]
            }
        )

        result = self._call("zalo_list_conversations")

        self.assertTrue(result["success"])
        self.assertEqual(
            result["data"]["conversations"],
            [
                {
                    "trust": "untrusted_external",
                    "thread_type": "group",
                    "thread_id": "123",
                    "name": "Team One",
                    "member_count": 5,
                    "pinned": True,
                    "archived": False,
                },
                {
                    "trust": "untrusted_external",
                    "thread_type": "dm",
                    "thread_id": "111",
                    "name": "Nam",
                    "member_count": 0,
                    "pinned": False,
                    "archived": True,
                },
            ],
        )
        self.assertEqual(self.service.bridge_calls, [("/conversations", "GET", None)])

    def test_conversations_filter_by_name(self):
        self._bind()
        self._ok(
            {
                "conversations": [
                    {"kind": "group", "id": "123", "name": "Team One"},
                    {"kind": "dm", "id": "111", "name": "Nam"},
                ]
            }
        )

        result = self._call("zalo_list_conversations", {"query": "nam"})

        self.assertEqual(
            [entry["thread_id"] for entry in result["data"]["conversations"]], ["111"]
        )

    def test_global_read_tools_are_denied_in_channel_runs(self):
        self._bind(threads={"zalo": {"111": {"conversation_id": "conv-1"}}})

        cases = [
            ("zalo_list_conversations", {}),
            ("zalo_user_info", {"user_id": "111"}),
            ("zalo_unread", {}),
        ]
        for name, args in cases:
            with self.subTest(name=name):
                result = self._call(name, args)
                self.assertEqual(
                    result["error"]["code"], "channel_thread_out_of_scope"
                )
        self.assertEqual(self.service.bridge_calls, [])

    def test_read_history_wraps_both_directions(self):
        self._bind()
        self._ok(
            {
                "messages": [
                    {
                        "messageId": "1",
                        "senderId": "111",
                        "senderName": "Nam",
                        "text": "hello",
                        "ts": 100,
                        "type": "text",
                        "direction": "in",
                    },
                    {
                        "messageId": "2",
                        "senderId": "",
                        "senderName": "me",
                        "text": "reply",
                        "ts": 200,
                        "type": "text",
                        "direction": "out",
                    },
                ],
                "truncated": False,
                "backfilled": True,
            }
        )

        result = self._call(
            "zalo_read_history",
            {"recipient": "Nam", "since_hours": 24, "limit": 10},
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["thread_id"], "111")
        self.assertEqual(result["data"]["name"], "Nam")
        self.assertTrue(result["data"]["backfilled"])
        self.assertEqual(
            [message["direction"] for message in result["data"]["messages"]],
            ["in", "out"],
        )
        self.assertTrue(
            all(
                message["trust"] == "untrusted_external"
                for message in result["data"]["messages"]
            )
        )
        self.assertEqual(
            self.service.bridge_calls,
            [("/history", "POST", {"chatId": "111", "limit": 10, "sinceHours": 24})],
        )

    def test_read_history_requires_the_dm_policy_to_allow_the_contact(self):
        self._bind()
        self.service.dm_allowed = False

        result = self._call("zalo_read_history", {"recipient": "Nam"})

        self.assertEqual(result["error"]["code"], "channel_dm_policy_blocked")
        self.assertEqual(self.service.bridge_calls, [])

    def test_read_history_refuses_a_group_id(self):
        self._bind()
        self.service.recipient = {"status": "ok", "thread_id": "123", "name": "Team One"}

        result = self._call("zalo_read_history", {"recipient": "123"})

        self.assertEqual(result["error"]["code"], "channel_recipient_invalid")
        self.assertEqual(self.service.bridge_calls, [])

    def test_channel_run_reads_only_its_own_direct_thread(self):
        self._bind(threads={"zalo": {"999": {"conversation_id": "conv-1"}}})

        other = self._call("zalo_read_history", {"recipient": "Nam"})
        self.assertEqual(other["error"]["code"], "channel_thread_out_of_scope")
        self.assertEqual(self.service.bridge_calls, [])

        self._bind(threads={"zalo": {"111": {"conversation_id": "conv-1"}}})
        own = self._call("zalo_read_history", {"recipient": "Nam"})

        self.assertTrue(own["success"])
        self.assertEqual(self.service.bridge_calls[0][0], "/history")

    def test_group_history_filters_by_since_and_reports_more(self):
        self._bind()
        now = time.time()
        self._ok(
            {
                "messages": [
                    {
                        "message_id": "1",
                        "sender_id": "77",
                        "sender_name": "Lan",
                        "text": "old",
                        "ts": now - 7200,
                        "type": "text",
                        "direction": "in",
                    },
                    {
                        "message_id": "2",
                        "sender_id": "78",
                        "sender_name": "Minh",
                        "text": "new",
                        "ts": now - 60,
                        "type": "text",
                        "direction": "in",
                    },
                ],
                "more": 12,
                "last_action_id": "77",
            }
        )

        result = self._call(
            "zalo_read_group_history",
            {"recipient": "Team One", "since_hours": 1, "limit": 50},
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["thread_id"], "123")
        self.assertEqual(
            [message["message_id"] for message in result["data"]["messages"]], ["2"]
        )
        self.assertEqual(result["data"]["more"], 12)
        self.assertEqual(result["data"]["last_action_id"], "77")
        self.assertEqual(
            self.service.bridge_calls[0],
            ("/group-history", "POST", {"groupId": "123", "limit": 50}),
        )

    def test_group_reads_require_an_approved_group(self):
        self._bind()
        self.service.group = {"status": "group_not_approved", "thread_id": "123"}

        cases = [
            ("zalo_read_group_history", {"recipient": "123"}),
            ("zalo_group_info", {"recipient": "123"}),
            ("zalo_list_reminders", {"recipient": "123"}),
            ("zalo_list_notes", {"recipient": "123"}),
            ("zalo_poll_detail", {"recipient": "123", "poll_id": 5}),
        ]
        for name, args in cases:
            with self.subTest(name=name):
                result = self._call(name, args)
                self.assertEqual(
                    result["error"]["code"], "channel_group_not_approved"
                )
        self.assertEqual(self.service.bridge_calls, [])

    def test_channel_run_group_reads_are_limited_to_its_own_group(self):
        self._bind(threads={"zalo": {"999": {"conversation_id": "conv-1"}}})

        other = self._call("zalo_group_info", {"recipient": "Team One"})
        self.assertEqual(other["error"]["code"], "channel_thread_out_of_scope")
        self.assertEqual(self.service.bridge_calls, [])

        self._bind(threads={"zalo": {"123": {"conversation_id": "conv-1"}}})
        own = self._call("zalo_group_info", {"recipient": "Team One"})

        self.assertTrue(own["success"])
        self.assertEqual(self.service.bridge_calls[0][0], "/group-info")

    def test_group_info_wraps_members_as_untrusted(self):
        self._bind()
        self._ok(
            {
                "group_id": "123",
                "name": "Team One",
                "description": "desc",
                "member_count": 2,
                "created_time": 100,
                "creator_id": "77",
                "members": [
                    {"user_id": "77", "display_name": "Owner", "role": "creator"},
                    {"user_id": "78", "display_name": "Member", "role": "member"},
                ],
            }
        )

        result = self._call("zalo_group_info", {"recipient": "Team One"})

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["member_count"], 2)
        self.assertEqual(
            result["data"]["members"][0],
            {
                "trust": "untrusted_external",
                "user_id": "77",
                "display_name": "Owner",
                "role": "creator",
            },
        )

    def test_reminders_and_notes_are_group_scoped(self):
        self._bind()
        self._ok(
            {
                "reminders": [
                    {
                        "reminder_id": "5",
                        "title": "Standup",
                        "creator_id": "77",
                        "start_time": 100,
                        "repeat": 1,
                    }
                ]
            }
        )

        reminders = self._call(
            "zalo_list_reminders", {"recipient": "Team One", "page": 2, "count": 5}
        )

        self.assertTrue(reminders["success"])
        self.assertEqual(reminders["data"]["group_id"], "123")
        self.assertEqual(
            reminders["data"]["reminders"][0],
            {
                "trust": "untrusted_external",
                "reminder_id": "5",
                "title": "Standup",
                "creator_id": "77",
                "start_time": 100,
                "repeat": 1,
            },
        )
        self.assertEqual(
            self.service.bridge_calls[0],
            ("/reminders", "POST", {"groupId": "123", "page": 2, "count": 5}),
        )

        self._ok(
            {
                "notes": [
                    {
                        "note_id": "7",
                        "title": "Plan",
                        "creator_id": "77",
                        "created_time": 10,
                        "updated_time": 20,
                    }
                ]
            }
        )
        notes = self._call("zalo_list_notes", {"recipient": "Team One"})

        self.assertTrue(notes["success"])
        self.assertEqual(notes["data"]["notes"][0]["note_id"], "7")
        self.assertEqual(self.service.bridge_calls[1][0], "/notes")

    def test_poll_detail_wraps_the_poll(self):
        self._bind()
        self._ok(
            {
                "poll_id": "12",
                "question": "Lunch?",
                "options": [
                    {
                        "option_id": 1,
                        "content": "Pizza",
                        "votes": 2,
                        "voted": True,
                    }
                ],
                "closed": False,
                "anonymous": True,
                "multi_choice": False,
                "vote_count": 3,
                "created_time": 100,
            }
        )

        result = self._call(
            "zalo_poll_detail", {"recipient": "Team One", "poll_id": 12}
        )

        self.assertTrue(result["success"])
        poll = result["data"]["poll"]
        self.assertEqual(poll["question"], "Lunch?")
        self.assertEqual(poll["vote_count"], 3)
        self.assertEqual(
            poll["options"][0],
            {
                "trust": "untrusted_external",
                "option_id": 1,
                "content": "Pizza",
                "votes": 2,
                "voted": True,
            },
        )
        self.assertEqual(
            self.service.bridge_calls[0],
            ("/poll-detail", "POST", {"groupId": "123", "pollId": 12}),
        )

    def test_search_uses_every_readable_thread_in_a_web_run(self):
        self._bind()
        self._ok(
            {
                "messages": [
                    {
                        "messageId": "1",
                        "threadId": "123",
                        "senderId": "77",
                        "senderName": "Lan",
                        "text": "report ready",
                        "ts": 100,
                        "type": "text",
                        "direction": "in",
                    }
                ],
                "truncated": True,
            }
        )

        result = self._call("zalo_search_messages", {"query": "report"})

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["messages"][0]["text"], "report ready")
        self.assertTrue(result["data"]["truncated"])
        self.assertEqual(
            self.service.bridge_calls[0],
            (
                "/search",
                "POST",
                {"query": "report", "chatIds": ["111", "123"], "limit": 20},
            ),
        )

    def test_search_in_a_channel_run_uses_only_the_origin_thread(self):
        self._bind(threads={"zalo": {"111": {"conversation_id": "conv-1"}}})

        result = self._call("zalo_search_messages", {"query": "report"})

        self.assertTrue(result["success"])
        self.assertEqual(self.service.bridge_calls[0][2]["chatIds"], ["111"])

        self.service.bridge_calls.clear()
        other = self._call(
            "zalo_search_messages",
            {"query": "report", "thread_type": "group", "recipient": "Team One"},
        )
        self.assertEqual(other["error"]["code"], "channel_thread_out_of_scope")
        self.assertEqual(self.service.bridge_calls, [])

    def test_user_info_sends_exactly_one_lookup_key(self):
        self._bind()
        self._ok(
            {
                "user_id": "42",
                "display_name": "Nam",
                "zalo_name": "nam",
                "avatar": "https://a",
                "is_friend": True,
                "gender": 0,
                "birthday": "",
            }
        )

        result = self._call("zalo_user_info", {"username": "nam"})

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["user_id"], "42")
        self.assertEqual(self.service.bridge_calls[0], ("/user-info", "POST", {"username": "nam"}))

        for args in ({}, {"user_id": "1", "phone": "+84912345678"}, {"phone": "abc"}):
            with self.subTest(args=args):
                invalid = self._call("zalo_user_info", args)
                self.assertEqual(
                    invalid["error"]["code"], "channel_invalid_request"
                )

    def test_unread_filters_to_readable_threads(self):
        self._bind()
        self._ok(
            {
                "direct": [
                    {"thread_id": "111", "last_message_id": "9", "ts": 100},
                    {"thread_id": "777", "last_message_id": "8", "ts": 90},
                ],
                "groups": [{"thread_id": "123", "last_message_id": "7", "ts": 80}],
            }
        )

        result = self._call("zalo_unread")

        self.assertTrue(result["success"])
        self.assertEqual(
            [entry["thread_id"] for entry in result["data"]["direct"]], ["111"]
        )
        self.assertEqual(
            [entry["thread_id"] for entry in result["data"]["groups"]], ["123"]
        )
        self.assertEqual(result["data"]["direct"][0]["trust"], "untrusted_external")


class ZaloSendExtraTests(_ZaloToolTestBase):
    """P-B send extras: media kinds, stickers, cards, forward, and undo."""

    _MP4 = b"\x00\x00\x00\x20ftypisom" + b"\x01" * 16
    _M4A = b"\x00\x00\x00\x20ftypM4A " + b"\x01" * 16
    _OGG = b"OggS" + b"\x01" * 16

    def _ok(self, data):
        self.service.bridge_result = {"ok": True, "status": 200, "data": data}

    def test_send_file_posts_the_kind_and_bytes(self):
        self._bind()
        self._write_file("report.pdf", b"%PDF-1.4 report")
        self._ok({"success": True, "messageId": "9"})

        with mock.patch("tools.approval._await_gateway_decision") as approval:
            result = self._call(
                "zalo_send_file",
                {"recipient": "Nam", "path": "report.pdf", "caption": "report"},
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["message_id"], "9")
        path, method, body = self.service.bridge_calls[0]
        self.assertEqual((path, method), ("/send-media", "POST"))
        self.assertEqual(body["kind"], "file")
        self.assertEqual(body["fileName"], "report.pdf")
        self.assertEqual(body["caption"], "report")
        self.assertEqual(body["threadType"], "user")
        self.assertEqual(base64.b64decode(body["contentBase64"]), b"%PDF-1.4 report")
        approval.assert_not_called()

    def test_send_video_and_voice_use_their_kinds(self):
        self._bind()
        self._write_file("clip.mp4", self._MP4)
        self._write_file("note.m4a", self._M4A)

        video = self._call(
            "zalo_send_video",
            {"thread_type": "group", "recipient": "Team One", "path": "clip.mp4"},
        )
        self.assertTrue(video["success"])
        self.assertEqual(self.service.bridge_calls[0][2]["kind"], "video")
        self.assertEqual(self.service.bridge_calls[0][2]["threadType"], "group")

        voice = self._call("zalo_send_voice", {"recipient": "Nam", "path": "note.m4a"})
        self.assertTrue(voice["success"])
        self.assertEqual(self.service.bridge_calls[1][2]["kind"], "voice")

    def test_media_type_must_match_the_tool(self):
        self._bind()
        self._write_image("pic.png")
        self._write_file("notes.txt", b"plain text")
        self._write_file("clip.mp4", self._MP4)
        self._write_file("note.ogg", self._OGG)

        cases = [
            ("zalo_send_video", "pic.png", "channel_video_invalid"),
            ("zalo_send_video", "clip.mp4", None),
            ("zalo_send_voice", "clip.mp4", "channel_voice_invalid"),
            ("zalo_send_voice", "note.ogg", None),
            ("zalo_send_file", "pic.png", "channel_file_invalid"),
            ("zalo_send_file", "clip.mp4", "channel_file_invalid"),
            ("zalo_send_file", "note.ogg", "channel_file_invalid"),
            ("zalo_send_file", "notes.txt", None),
        ]
        for name, path, expected in cases:
            with self.subTest(name=name, path=path):
                self.service.bridge_calls.clear()
                result = self._call(name, {"recipient": "Nam", "path": path})
                if expected is None:
                    self.assertTrue(result["success"])
                else:
                    self.assertEqual(result["error"]["code"], expected)
                    self.assertEqual(self.service.bridge_calls, [])

    def test_media_paths_and_sizes_are_checked_before_the_bridge(self):
        self._bind()
        outside = Path(self.temporary.name) / "outside.pdf"
        outside.write_bytes(b"%PDF-1.4")
        (self.workspace / "link.pdf").symlink_to(outside)
        self._write_file("big.pdf", b"%PDF-1.4")

        unsafe = self._call(
            "zalo_send_file",
            {"recipient": "Nam", "path": str(outside.resolve())},
        )
        self.assertEqual(unsafe["error"]["code"], "channel_path_unsafe")

        symlink = self._call(
            "zalo_send_file", {"recipient": "Nam", "path": "link.pdf"}
        )
        self.assertEqual(symlink["error"]["code"], "channel_path_unsafe")

        self.service.media_max_mb = 0.0
        oversized = self._call(
            "zalo_send_file", {"recipient": "Nam", "path": "big.pdf"}
        )
        self.assertEqual(oversized["error"]["code"], "channel_media_too_large")
        self.assertEqual(self.service.bridge_calls, [])

    def test_send_sticker_searches_then_sends(self):
        self._bind()
        self._ok({"success": True, "messageId": "11"})

        result = self._call(
            "zalo_send_sticker",
            {
                "thread_type": "group",
                "recipient": "Team One",
                "keyword": "cat",
                "index": 2,
            },
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["message_id"], "11")
        self.assertEqual(result["data"]["keyword"], "cat")
        self.assertEqual(
            self.service.bridge_calls[0],
            (
                "/send-sticker",
                "POST",
                {
                    "chatId": "123",
                    "threadType": "group",
                    "keyword": "cat",
                    "index": 2,
                },
            ),
        )

    def test_send_sticker_rejects_a_bad_index(self):
        self._bind()

        result = self._call(
            "zalo_send_sticker", {"recipient": "Nam", "keyword": "cat", "index": 20}
        )

        self.assertEqual(result["error"]["code"], "channel_invalid_request")
        self.assertEqual(self.service.bridge_calls, [])

    def test_send_contact_card_posts_one_user(self):
        self._bind()
        self._ok({"success": True, "messageId": "12"})

        result = self._call(
            "zalo_send_contact_card",
            {"recipient": "Nam", "user_id": "77", "phone": "+84912345678"},
        )

        self.assertTrue(result["success"])
        self.assertEqual(
            self.service.bridge_calls[0],
            (
                "/send-card",
                "POST",
                {
                    "chatId": "111",
                    "threadType": "user",
                    "userId": "77",
                    "phoneNumber": "+84912345678",
                },
            ),
        )

    def test_send_contact_card_requires_a_numeric_user_id(self):
        self._bind()

        result = self._call(
            "zalo_send_contact_card", {"recipient": "Nam", "user_id": "abc"}
        )

        self.assertEqual(result["error"]["code"], "channel_invalid_request")
        self.assertEqual(self.service.bridge_calls, [])

    def test_forward_posts_the_source_and_target(self):
        self._bind()
        self._ok({"success": True, "messageId": "13"})

        result = self._call(
            "zalo_forward",
            {
                "source_recipient": "Nam",
                "message_id": "555",
                "thread_type": "group",
                "recipient": "Team One",
            },
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["source_thread_id"], "111")
        self.assertEqual(result["data"]["thread_id"], "123")
        self.assertEqual(
            self.service.bridge_calls[0],
            (
                "/forward",
                "POST",
                {
                    "sourceChatId": "111",
                    "sourceThreadType": "user",
                    "messageId": "555",
                    "chatId": "123",
                    "threadType": "group",
                },
            ),
        )

    def test_forward_source_must_be_readable(self):
        self._bind()
        self.service.dm_allowed = False

        result = self._call(
            "zalo_forward",
            {"source_recipient": "Nam", "message_id": "555", "recipient": "Team One", "thread_type": "group"},
        )

        self.assertEqual(result["error"]["code"], "channel_dm_policy_blocked")
        self.assertEqual(self.service.bridge_calls, [])

    def test_forward_target_must_be_readable(self):
        self._bind()
        self.service.dm_allowed = False

        result = self._call(
            "zalo_forward",
            {
                "source_thread_type": "group",
                "source_recipient": "Team One",
                "message_id": "555",
                "recipient": "Nam",
            },
        )

        self.assertEqual(result["error"]["code"], "channel_dm_policy_blocked")
        self.assertEqual(self.service.bridge_calls, [])

    def test_forward_in_a_channel_run_stays_inside_the_run_thread(self):
        self._bind(threads={"zalo": {"111": {"conversation_id": "conv-1"}}})

        outside = self._call(
            "zalo_forward",
            {
                "source_recipient": "Nam",
                "message_id": "555",
                "thread_type": "group",
                "recipient": "Team One",
            },
        )
        self.assertEqual(outside["error"]["code"], "channel_thread_out_of_scope")
        self.assertEqual(self.service.bridge_calls, [])

        self.service.bridge_calls.clear()
        inside = self._call(
            "zalo_forward",
            {
                "source_recipient": "Nam",
                "message_id": "555",
                "recipient": "Nam",
            },
        )
        self.assertTrue(inside["success"])
        self.assertEqual(self.service.bridge_calls[0][0], "/forward")

    def test_undo_posts_the_message_id(self):
        self._bind()
        self._ok({"success": True})

        result = self._call(
            "zalo_undo_message", {"recipient": "Nam", "message_id": "555"}
        )

        self.assertTrue(result["success"])
        self.assertTrue(result["data"]["undone"])
        self.assertEqual(
            self.service.bridge_calls[0],
            (
                "/undo",
                "POST",
                {"chatId": "111", "threadType": "user", "messageId": "555"},
            ),
        )

    def test_undo_maps_the_bridge_refusal(self):
        self._bind()
        self.service.bridge_result = {
            "ok": False,
            "status": 403,
            "data": None,
            "error": "not_own_message",
        }

        result = self._call(
            "zalo_undo_message", {"recipient": "Nam", "message_id": "555"}
        )

        self.assertEqual(result["error"]["code"], "channel_message_not_own")

    def test_send_extras_require_an_approved_group(self):
        self._bind()
        self._write_file("report.pdf", b"%PDF-1.4")
        self.service.group = {"status": "group_not_approved", "thread_id": "123"}

        cases = [
            (
                "zalo_send_file",
                {"thread_type": "group", "recipient": "123", "path": "report.pdf"},
            ),
            (
                "zalo_send_sticker",
                {"thread_type": "group", "recipient": "123", "keyword": "cat"},
            ),
            (
                "zalo_send_contact_card",
                {"thread_type": "group", "recipient": "123", "user_id": "77"},
            ),
            (
                "zalo_forward",
                {
                    "source_recipient": "Nam",
                    "message_id": "555",
                    "thread_type": "group",
                    "recipient": "123",
                },
            ),
            (
                "zalo_undo_message",
                {"thread_type": "group", "recipient": "123", "message_id": "555"},
            ),
        ]
        for name, args in cases:
            with self.subTest(name=name):
                result = self._call(name, args)
                self.assertEqual(
                    result["error"]["code"], "channel_group_not_approved"
                )
        self.assertEqual(self.service.bridge_calls, [])

    def test_send_extras_are_out_of_scope_for_another_thread(self):
        self._bind(threads={"zalo": {"999": {"conversation_id": "conv-1"}}})
        self._write_file("report.pdf", b"%PDF-1.4")

        cases = [
            ("zalo_send_file", {"recipient": "Nam", "path": "report.pdf"}),
            ("zalo_send_video", {"recipient": "Nam", "path": "report.pdf"}),
            ("zalo_send_voice", {"recipient": "Nam", "path": "report.pdf"}),
            ("zalo_send_sticker", {"recipient": "Nam", "keyword": "cat"}),
            ("zalo_send_contact_card", {"recipient": "Nam", "user_id": "77"}),
            ("zalo_undo_message", {"recipient": "Nam", "message_id": "555"}),
        ]
        for name, args in cases:
            with self.subTest(name=name):
                result = self._call(name, args)
                self.assertEqual(
                    result["error"]["code"], "channel_thread_out_of_scope"
                )
        self.assertEqual(self.service.bridge_calls, [])

    def test_send_extras_deliver_inside_the_run_thread(self):
        self._bind(threads={"zalo": {"111": {"conversation_id": "conv-1"}}})
        self._write_file("report.pdf", b"%PDF-1.4")

        result = self._call(
            "zalo_send_file", {"recipient": "Nam", "path": "report.pdf"}
        )

        self.assertTrue(result["success"])
        self.assertEqual(self.service.bridge_calls[0][0], "/send-media")


class ZaloGroupUtilityTests(_ZaloToolTestBase):
    """P-B phase 3 group utilities: polls, notes, reminders, and settings."""

    _GROUP_TOOLS = (
        "zalo_create_poll",
        "zalo_create_note",
        "zalo_create_reminder",
        "zalo_pin_conversation",
        "zalo_mute",
        "zalo_labels",
        "zalo_mark_unread",
    )

    def _ok(self, data):
        self.service.bridge_result = {"ok": True, "status": 200, "data": data}

    def test_group_utility_descriptions_ask_for_in_chat_confirmation(self):
        for name in self._GROUP_TOOLS:
            with self.subTest(name=name):
                description = zalo_tools._SCHEMAS[name]["function"]["description"]
                self.assertIn("explicit confirmation in chat", description)
                self.assertIn("Not available in a run started from a channel thread", description)

    def test_group_utility_schemas_require_their_scope_fields(self):
        expected = {
            "zalo_create_poll": {"recipient", "question", "options"},
            "zalo_create_note": {"recipient", "title"},
            "zalo_create_reminder": {
                "recipient",
                "title",
                "starts_in_minutes",
            },
            "zalo_pin_conversation": {"recipient", "pinned"},
            "zalo_mute": {"recipient"},
            "zalo_labels": {"action"},
            "zalo_mark_unread": {"recipient"},
        }
        for name, required in expected.items():
            with self.subTest(name=name):
                parameters = zalo_tools._SCHEMAS[name]["function"]["parameters"]
                self.assertEqual(set(parameters.get("required") or []), required)
                self.assertIs(parameters["additionalProperties"], False)

    def test_every_group_utility_is_denied_in_a_channel_run(self):
        self._bind(threads={"zalo": {"123": {"conversation_id": "conv-1"}}})

        cases = [
            (
                "zalo_create_poll",
                {"recipient": "Team One", "question": "Lunch?", "options": ["A", "B"]},
            ),
            ("zalo_create_note", {"recipient": "Team One", "title": "Plan"}),
            (
                "zalo_create_reminder",
                {"recipient": "Nam", "title": "Call", "starts_in_minutes": 30},
            ),
            ("zalo_pin_conversation", {"recipient": "Nam", "pinned": True}),
            ("zalo_mute", {"recipient": "Nam", "action": "mute"}),
            ("zalo_labels", {"action": "list"}),
            (
                "zalo_labels",
                {"action": "add", "recipient": "Nam", "label_id": 3},
            ),
            ("zalo_mark_unread", {"recipient": "Nam", "unread": True}),
        ]
        for name, args in cases:
            with self.subTest(name=name, args=args):
                result = self._call(name, args)
                self.assertEqual(
                    result["error"]["code"], "channel_thread_out_of_scope"
                )
        self.assertEqual(self.service.bridge_calls, [])

    def test_group_utilities_require_an_approved_group(self):
        self._bind()
        self.service.group = {"status": "group_not_approved", "thread_id": "123"}

        cases = [
            (
                "zalo_create_poll",
                {"recipient": "123", "question": "Lunch?", "options": ["A", "B"]},
            ),
            ("zalo_create_note", {"recipient": "123", "title": "Plan"}),
            (
                "zalo_create_reminder",
                {
                    "thread_type": "group",
                    "recipient": "123",
                    "title": "Call",
                    "starts_in_minutes": 30,
                },
            ),
            (
                "zalo_pin_conversation",
                {"thread_type": "group", "recipient": "123", "pinned": True},
            ),
            (
                "zalo_mute",
                {"thread_type": "group", "recipient": "123", "action": "mute"},
            ),
            (
                "zalo_labels",
                {"action": "add", "thread_type": "group", "recipient": "123", "label_id": 3},
            ),
            (
                "zalo_mark_unread",
                {"thread_type": "group", "recipient": "123", "unread": True},
            ),
        ]
        for name, args in cases:
            with self.subTest(name=name):
                result = self._call(name, args)
                self.assertEqual(
                    result["error"]["code"], "channel_group_not_approved"
                )
        self.assertEqual(self.service.bridge_calls, [])

    def test_direct_message_utilities_follow_the_dm_policy(self):
        self._bind()
        self.service.dm_allowed = False

        cases = [
            (
                "zalo_create_reminder",
                {"recipient": "Nam", "title": "Call", "starts_in_minutes": 30},
            ),
            ("zalo_pin_conversation", {"recipient": "Nam", "pinned": True}),
            ("zalo_mute", {"recipient": "Nam", "action": "unmute"}),
            (
                "zalo_labels",
                {"action": "remove", "recipient": "Nam", "label_id": 3},
            ),
            ("zalo_mark_unread", {"recipient": "Nam", "unread": False}),
        ]
        for name, args in cases:
            with self.subTest(name=name):
                result = self._call(name, args)
                self.assertEqual(
                    result["error"]["code"], "channel_dm_policy_blocked"
                )
        self.assertEqual(self.service.bridge_calls, [])

    def test_create_poll_posts_the_group_and_options(self):
        self._bind()
        self._ok(
            {
                "poll": {
                    "poll_id": "12",
                    "question": "Lunch?",
                    "options": [
                        {"option_id": 1, "content": "Pizza", "votes": 0, "voted": False}
                    ],
                    "closed": False,
                    "anonymous": True,
                    "multi_choice": True,
                    "vote_count": 0,
                    "created_time": 100,
                }
            }
        )

        result = self._call(
            "zalo_create_poll",
            {
                "recipient": "Team One",
                "question": "  Lunch?  ",
                "options": ["Pizza", "Pho"],
                "multi_choice": True,
                "expires_in_hours": 24,
            },
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["thread_id"], "123")
        self.assertEqual(result["data"]["poll"]["question"], "Lunch?")
        self.assertEqual(
            result["data"]["poll"]["options"][0],
            {
                "trust": "untrusted_external",
                "option_id": 1,
                "content": "Pizza",
                "votes": 0,
                "voted": False,
            },
        )
        self.assertEqual(
            self.service.bridge_calls[0],
            (
                "/poll-create",
                "POST",
                {
                    "groupId": "123",
                    "question": "Lunch?",
                    "options": ["Pizza", "Pho"],
                    "allowMultiChoices": True,
                    "allowAddNewOption": False,
                    "hideVotePreview": False,
                    "isAnonymous": False,
                    "expiresInHours": 24,
                },
            ),
        )

    def test_create_poll_validates_options_and_expiry(self):
        self._bind()

        cases = [
            {"recipient": "Team One", "question": "Q", "options": ["A"]},
            {
                "recipient": "Team One",
                "question": "Q",
                "options": [str(index) for index in range(11)],
            },
            {"recipient": "Team One", "question": "Q", "options": ["A", "A"]},
            {"recipient": "Team One", "question": "Q", "options": ["A", "  "]},
            {
                "recipient": "Team One",
                "question": "Q",
                "options": ["A", "B" * 501],
            },
            {
                "recipient": "Team One",
                "question": "Q",
                "options": ["A", "B"],
                "expires_in_hours": 721,
            },
            {"recipient": "Team One", "question": "  ", "options": ["A", "B"]},
        ]
        for args in cases:
            with self.subTest(args=args):
                result = self._call("zalo_create_poll", args)
                self.assertEqual(
                    result["error"]["code"], "channel_invalid_request"
                )
        self.assertEqual(self.service.bridge_calls, [])

    def test_create_note_posts_the_title_and_pin_flag(self):
        self._bind()
        self._ok(
            {
                "note": {
                    "note_id": "7",
                    "title": "Plan",
                    "created_time": 10,
                    "updated_time": 10,
                }
            }
        )

        result = self._call(
            "zalo_create_note",
            {"recipient": "Team One", "title": " Plan ", "pinned": True},
        )

        self.assertTrue(result["success"])
        self.assertEqual(
            result["data"]["note"],
            {
                "trust": "untrusted_external",
                "note_id": "7",
                "title": "Plan",
                "created_time": 10,
                "updated_time": 10,
                "pin_requested": True,
            },
        )
        self.assertEqual(
            self.service.bridge_calls[0],
            (
                "/note-create",
                "POST",
                {"groupId": "123", "title": "Plan", "pinned": True},
            ),
        )

    def test_create_reminder_posts_minutes_and_repeat(self):
        self._bind()
        self._ok(
            {
                "reminder": {
                    "reminder_id": "5",
                    "title": "Standup",
                    "start_time": 1_800_000,
                    "repeat": 1,
                }
            }
        )

        result = self._call(
            "zalo_create_reminder",
            {
                "recipient": "Nam",
                "title": "Standup",
                "starts_in_minutes": 60,
                "repeat": "daily",
                "emoji": "⏰",
            },
        )

        self.assertTrue(result["success"])
        self.assertEqual(
            result["data"]["reminder"],
            {
                "trust": "untrusted_external",
                "reminder_id": "5",
                "title": "Standup",
                "start_time": 1_800_000,
                "repeat": 1,
            },
        )
        self.assertEqual(
            self.service.bridge_calls[0],
            (
                "/reminder-create",
                "POST",
                {
                    "chatId": "111",
                    "threadType": "user",
                    "title": "Standup",
                    "startsInMinutes": 60,
                    "repeat": "daily",
                    "emoji": "⏰",
                },
            ),
        )

    def test_create_reminder_requires_a_future_start(self):
        self._bind()

        for minutes in (0, -5, 525_601):
            with self.subTest(minutes=minutes):
                result = self._call(
                    "zalo_create_reminder",
                    {
                        "recipient": "Nam",
                        "title": "Call",
                        "starts_in_minutes": minutes,
                    },
                )
                self.assertEqual(
                    result["error"]["code"], "channel_invalid_request"
                )
        self.assertEqual(self.service.bridge_calls, [])

    def test_pin_and_mark_unread_post_the_thread(self):
        self._bind()

        pinned = self._call(
            "zalo_pin_conversation", {"recipient": "Nam", "pinned": True}
        )
        unread = self._call(
            "zalo_mark_unread", {"recipient": "Nam", "unread": True}
        )

        self.assertTrue(pinned["success"])
        self.assertTrue(pinned["data"]["pinned"])
        self.assertTrue(unread["success"])
        self.assertTrue(unread["data"]["unread"])
        self.assertEqual(
            self.service.bridge_calls,
            [
                (
                    "/pin",
                    "POST",
                    {"chatId": "111", "threadType": "user", "pinned": True},
                ),
                (
                    "/unread-mark",
                    "POST",
                    {"chatId": "111", "threadType": "user", "unread": True},
                ),
            ],
        )

    def test_mute_maps_action_and_duration(self):
        self._bind()

        muted = self._call(
            "zalo_mute",
            {"recipient": "Nam", "action": "mute", "duration": "four_hours"},
        )
        unmuted = self._call(
            "zalo_mute", {"recipient": "Nam", "action": "unmute"}
        )

        self.assertTrue(muted["success"])
        self.assertEqual(
            muted["data"]["duration"], "four_hours"
        )
        self.assertIsNone(unmuted["data"]["duration"])
        self.assertEqual(
            self.service.bridge_calls[0],
            (
                "/mute",
                "POST",
                {
                    "chatId": "111",
                    "threadType": "user",
                    "action": "mute",
                    "duration": "four_hours",
                },
            ),
        )

    def test_mute_rejects_an_unknown_duration(self):
        self._bind()

        result = self._call(
            "zalo_mute", {"recipient": "Nam", "duration": "ten_minutes"}
        )

        self.assertEqual(result["error"]["code"], "channel_invalid_request")
        self.assertEqual(self.service.bridge_calls, [])

    def test_labels_list_returns_untrusted_summaries(self):
        self._bind()
        self._ok(
            {
                "labels": [
                    {"label_id": "3", "name": "Work", "conversation_count": 2}
                ]
            }
        )

        result = self._call("zalo_labels", {"action": "list"})

        self.assertTrue(result["success"])
        self.assertEqual(
            result["data"]["labels"],
            [
                {
                    "trust": "untrusted_external",
                    "label_id": "3",
                    "name": "Work",
                    "conversation_count": 2,
                }
            ],
        )
        self.assertEqual(self.service.bridge_calls, [("/labels", "GET", None)])

    def test_labels_add_posts_the_label_change(self):
        self._bind()
        self._ok(
            {
                "label": {"label_id": "3", "name": "Work", "conversation_count": 3},
                "labels": [
                    {"label_id": "3", "name": "Work", "conversation_count": 3}
                ],
            }
        )

        result = self._call(
            "zalo_labels",
            {"action": "add", "recipient": "Nam", "label_id": 3},
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["label_id"], "3")
        self.assertEqual(result["data"]["conversation_count"], 3)
        self.assertTrue(result["data"]["changed"])
        self.assertEqual(
            self.service.bridge_calls[0],
            (
                "/labels",
                "POST",
                {
                    "action": "add",
                    "labelId": 3,
                    "chatId": "111",
                    "threadType": "user",
                },
            ),
        )

    def test_labels_change_requires_a_thread_and_label(self):
        self._bind()

        cases = [
            {"action": "add"},
            {"action": "remove", "recipient": "Nam"},
            {"action": "add", "label_id": 3},
            {"action": "list", "recipient": "Nam"},
        ]
        for args in cases:
            with self.subTest(args=args):
                result = self._call("zalo_labels", args)
                self.assertEqual(
                    result["error"]["code"], "channel_invalid_request"
                )
        self.assertEqual(self.service.bridge_calls, [])


class ZaloAdminToolTests(_ZaloToolTestBase):
    """P-B phase 4 group administration and account utilities."""

    _ADMIN_TOOLS = (
        "zalo_group_members",
        "zalo_group_settings",
        "zalo_group_roles",
        "zalo_group_pending",
        "zalo_group_link",
        "zalo_create_group",
        "zalo_friend_requests",
        "zalo_friend_alias",
        "zalo_quick_reply",
    )

    def _ok(self, data):
        self.service.bridge_result = {"ok": True, "status": 200, "data": data}

    def test_admin_descriptions_ask_for_confirmation_and_warn(self):
        for name in self._ADMIN_TOOLS:
            with self.subTest(name=name):
                description = zalo_tools._SCHEMAS[name]["function"]["description"]
                self.assertIn("explicit confirmation in chat", description)
                self.assertTrue(
                    description.rstrip().endswith(
                        "wait for their explicit confirmation in chat."
                    )
                )
                self.assertIn(
                    "Not available in a run started from a channel thread",
                    description,
                )
                self.assertIn("may not be reversible", description)

    def test_admin_schemas_require_their_scope_fields(self):
        expected = {
            "zalo_group_members": {"action", "recipient"},
            "zalo_group_settings": {"action", "recipient"},
            "zalo_group_roles": {"action", "recipient", "user_ids"},
            "zalo_group_pending": {"action", "recipient"},
            "zalo_group_link": {"action", "recipient"},
            "zalo_create_group": {"name", "user_ids"},
            "zalo_friend_requests": {"action"},
            "zalo_friend_alias": {"action"},
            "zalo_quick_reply": {"action"},
        }
        for name, required in expected.items():
            with self.subTest(name=name):
                parameters = zalo_tools._SCHEMAS[name]["function"]["parameters"]
                self.assertEqual(set(parameters.get("required") or []), required)
                self.assertIs(parameters["additionalProperties"], False)
        settings = zalo_tools._SCHEMAS["zalo_group_settings"]["function"]["parameters"]
        self.assertIs(settings["properties"]["settings"]["additionalProperties"], False)
        self.assertEqual(
            set(settings["properties"]["settings"]["properties"]),
            set(zalo_tools._GROUP_SETTING_FIELDS),
        )

    def test_every_admin_tool_is_denied_in_a_channel_run(self):
        self._bind(threads={"zalo": {"123": {"conversation_id": "conv-1"}}})

        cases = [
            ("zalo_group_members", {"action": "list", "recipient": "Team One"}),
            (
                "zalo_group_members",
                {"action": "add", "recipient": "Team One", "user_ids": ["7"]},
            ),
            (
                "zalo_group_settings",
                {"action": "rename", "recipient": "Team One", "name": "New"},
            ),
            (
                "zalo_group_roles",
                {
                    "action": "add_deputy",
                    "recipient": "Team One",
                    "user_ids": ["7"],
                },
            ),
            ("zalo_group_pending", {"action": "list", "recipient": "Team One"}),
            ("zalo_group_link", {"action": "get", "recipient": "Team One"}),
            ("zalo_create_group", {"name": "New", "user_ids": ["7"]}),
            ("zalo_friend_requests", {"action": "list_sent"}),
            (
                "zalo_friend_requests",
                {"action": "accept", "user_id": "7"},
            ),
            ("zalo_friend_alias", {"action": "list"}),
            (
                "zalo_friend_alias",
                {"action": "set", "friend_id": "7", "alias": "Boss"},
            ),
            ("zalo_quick_reply", {"action": "list"}),
            (
                "zalo_quick_reply",
                {
                    "action": "add",
                    "keyword": "hi",
                    "title": "Hello",
                },
            ),
        ]
        for name, args in cases:
            with self.subTest(name=name, args=args):
                result = self._call(name, args)
                self.assertEqual(
                    result["error"]["code"], "channel_thread_out_of_scope"
                )
        self.assertEqual(self.service.bridge_calls, [])

    def test_admin_group_tools_require_an_approved_group(self):
        self._bind()
        self.service.group = {"status": "group_not_approved", "thread_id": "123"}

        cases = [
            ("zalo_group_members", {"action": "list", "recipient": "123"}),
            (
                "zalo_group_members",
                {"action": "remove", "recipient": "123", "user_ids": ["7"]},
            ),
            (
                "zalo_group_settings",
                {"action": "update", "recipient": "123", "settings": {"join_appr": True}},
            ),
            (
                "zalo_group_roles",
                {
                    "action": "remove_deputy",
                    "recipient": "123",
                    "user_ids": ["7"],
                },
            ),
            (
                "zalo_group_pending",
                {"action": "approve", "recipient": "123", "user_ids": ["7"]},
            ),
            ("zalo_group_link", {"action": "enable", "recipient": "123"}),
        ]
        for name, args in cases:
            with self.subTest(name=name):
                result = self._call(name, args)
                self.assertEqual(
                    result["error"]["code"], "channel_group_not_approved"
                )
        self.assertEqual(self.service.bridge_calls, [])

    def test_admin_group_tools_recheck_the_shared_thread_resolution(self):
        self._bind()
        self.service.threads["123"] = {
            "status": "group_not_approved",
            "thread_id": "123",
            "name": "Team One",
            "type": "group",
        }

        result = self._call(
            "zalo_group_members", {"action": "list", "recipient": "Team One"}
        )

        self.assertEqual(result["error"]["code"], "channel_group_not_approved")
        self.assertEqual(self.service.bridge_calls, [])

    def test_group_members_list_wraps_members_as_untrusted(self):
        self._bind()
        self._ok(
            {
                "name": "Team One",
                "member_count": 3,
                "members": [
                    {"user_id": "1", "display_name": "Owner", "role": "creator"},
                    {"user_id": "2", "display_name": "Admin", "role": "admin"},
                ],
                "has_more": True,
            }
        )

        result = self._call(
            "zalo_group_members",
            {"action": "list", "recipient": "Team One", "limit": 20},
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["group_id"], "123")
        self.assertEqual(result["data"]["name"], "Team One")
        self.assertEqual(result["data"]["member_count"], 3)
        self.assertTrue(result["data"]["has_more"])
        self.assertEqual(
            result["data"]["members"][0],
            {
                "trust": "untrusted_external",
                "user_id": "1",
                "display_name": "Owner",
                "role": "creator",
            },
        )
        self.assertEqual(
            self.service.bridge_calls[0],
            (
                "/group-members",
                "POST",
                {"action": "list", "groupId": "123", "memberLimit": 20},
            ),
        )

    def test_group_members_add_and_remove_post_their_ids(self):
        self._bind()
        self._ok({"requested": 2, "changed": ["7"], "failed": ["8"]})

        added = self._call(
            "zalo_group_members",
            {"action": "add", "recipient": "Team One", "user_ids": ["7", "8"]},
        )
        removed = self._call(
            "zalo_group_members",
            {"action": "remove", "recipient": "Team One", "user_ids": ["9"]},
        )

        self.assertEqual(added["data"]["requested"], 2)
        self.assertEqual(added["data"]["changed"], ["7"])
        self.assertEqual(added["data"]["failed"], ["8"])
        self.assertTrue(removed["success"])
        self.assertEqual(
            self.service.bridge_calls[0],
            (
                "/group-members",
                "POST",
                {"action": "add", "groupId": "123", "memberIds": ["7", "8"]},
            ),
        )
        self.assertEqual(
            self.service.bridge_calls[1],
            (
                "/group-members",
                "POST",
                {"action": "remove", "groupId": "123", "memberIds": ["9"]},
            ),
        )

    def test_group_members_validate_actions_and_user_ids(self):
        self._bind()

        cases = [
            {"action": "list", "recipient": "Team One", "user_ids": ["7"]},
            {"action": "add", "recipient": "Team One"},
            {"action": "remove", "recipient": "Team One", "user_ids": []},
            {"action": "add", "recipient": "Team One", "user_ids": ["x"]},
            {"action": "add", "recipient": "Team One", "user_ids": ["7", "7"]},
            {
                "action": "add",
                "recipient": "Team One",
                "user_ids": [str(index) for index in range(21)],
            },
            {"action": "list", "recipient": "Team One", "limit": 101},
            {"action": "kick", "recipient": "Team One"},
        ]
        for args in cases:
            with self.subTest(args=args):
                result = self._call("zalo_group_members", args)
                self.assertEqual(
                    result["error"]["code"], "channel_invalid_request"
                )
        self.assertEqual(self.service.bridge_calls, [])

    def test_group_settings_rename_posts_the_new_name(self):
        self._bind()
        self._ok({"name": "New Name", "renamed": True})

        result = self._call(
            "zalo_group_settings",
            {"action": "rename", "recipient": "Team One", "name": "  New Name  "},
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["name"], "New Name")
        self.assertTrue(result["data"]["renamed"])
        self.assertEqual(
            self.service.bridge_calls[0],
            (
                "/group-settings",
                "POST",
                {"action": "rename", "groupId": "123", "name": "New Name"},
            ),
        )

    def test_group_settings_update_sends_only_the_changed_keys(self):
        self._bind()
        self._ok(
            {
                "settings": {
                    "blockName": True,
                    "signAdminMsg": False,
                    "setTopicOnly": False,
                    "enableMsgHistory": False,
                    "joinAppr": True,
                    "lockCreatePost": False,
                    "lockCreatePoll": False,
                    "lockSendMsg": True,
                    "lockViewMember": False,
                }
            }
        )

        result = self._call(
            "zalo_group_settings",
            {
                "action": "update",
                "recipient": "Team One",
                "settings": {"lock_send_msg": True, "join_appr": True},
            },
        )

        self.assertTrue(result["success"])
        self.assertEqual(
            self.service.bridge_calls[0],
            (
                "/group-settings",
                "POST",
                {
                    "action": "update",
                    "groupId": "123",
                    "settings": {"joinAppr": True, "lockSendMsg": True},
                },
            ),
        )
        self.assertTrue(result["data"]["settings"]["lock_send_msg"])
        self.assertFalse(result["data"]["settings"]["sign_admin_msg"])

    def test_group_settings_require_one_payload(self):
        self._bind()

        cases = [
            {"action": "rename", "recipient": "Team One"},
            {"action": "rename", "recipient": "Team One", "name": "  "},
            {"action": "rename", "recipient": "Team One", "name": "x" * 101},
            {
                "action": "rename",
                "recipient": "Team One",
                "name": "New",
                "settings": {"join_appr": True},
            },
            {"action": "update", "recipient": "Team One"},
            {"action": "update", "recipient": "Team One", "settings": {}},
            {
                "action": "update",
                "recipient": "Team One",
                "settings": {"change_owner": True},
            },
            {"action": "rename", "recipient": "Team One", "name": "New", "extra": 1},
        ]
        for args in cases:
            with self.subTest(args=args):
                result = self._call("zalo_group_settings", args)
                self.assertEqual(
                    result["error"]["code"], "channel_invalid_request"
                )
        self.assertEqual(self.service.bridge_calls, [])

    def test_group_roles_post_deputy_changes(self):
        self._bind()
        self._ok({"requested": 1, "changed": ["7"], "failed": []})

        result = self._call(
            "zalo_group_roles",
            {"action": "add_deputy", "recipient": "Team One", "user_ids": ["7"]},
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["changed"], ["7"])
        self.assertEqual(
            self.service.bridge_calls[0],
            (
                "/group-roles",
                "POST",
                {"action": "add_deputy", "groupId": "123", "memberIds": ["7"]},
            ),
        )

    def test_group_pending_lists_and_reviews_requests(self):
        self._bind()
        self._ok(
            {
                "pending": [
                    {"user_id": "7", "display_name": "Lan"},
                ],
                "truncated": False,
            }
        )

        listed = self._call(
            "zalo_group_pending",
            {"action": "list", "recipient": "Team One", "limit": 10},
        )

        self.assertEqual(
            listed["data"]["pending"],
            [
                {
                    "trust": "untrusted_external",
                    "user_id": "7",
                    "display_name": "Lan",
                }
            ],
        )
        self.assertEqual(
            self.service.bridge_calls[0],
            (
                "/group-pending",
                "POST",
                {"action": "list", "groupId": "123", "limit": 10},
            ),
        )

        self.service.bridge_calls.clear()
        self._ok(
            {
                "results": [
                    {"user_id": "7", "status": "success"},
                    {"user_id": "8", "status": "already_member"},
                ]
            }
        )
        reviewed = self._call(
            "zalo_group_pending",
            {"action": "approve", "recipient": "Team One", "user_ids": ["7", "8"]},
        )

        self.assertEqual(reviewed["data"]["changed"], 1)
        self.assertEqual(reviewed["data"]["results"][1]["status"], "already_member")
        self.assertEqual(
            self.service.bridge_calls[0],
            (
                "/group-pending",
                "POST",
                {"action": "approve", "groupId": "123", "memberIds": ["7", "8"]},
            ),
        )

    def test_group_link_reads_and_changes_the_invite_link(self):
        self._bind()
        self._ok(
            {
                "enabled": True,
                "link": "https://zalo.me/g/abcdef",
                "expiration_date": 1700000000,
            }
        )

        result = self._call(
            "zalo_group_link", {"action": "enable", "recipient": "Team One"}
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["group_id"], "123")
        self.assertTrue(result["data"]["enabled"])
        self.assertEqual(result["data"]["link"], "https://zalo.me/g/abcdef")
        self.assertTrue(result["data"]["changed"])
        self.assertEqual(
            self.service.bridge_calls[0],
            (
                "/group-link",
                "POST",
                {"action": "enable", "groupId": "123"},
            ),
        )

        self.service.bridge_calls.clear()
        fetched = self._call(
            "zalo_group_link", {"action": "get", "recipient": "Team One"}
        )
        self.assertFalse(fetched["data"]["changed"])
        self.assertEqual(
            self.service.bridge_calls[0],
            ("/group-link", "POST", {"action": "get", "groupId": "123"}),
        )

    def test_create_group_posts_the_name_and_members(self):
        self._bind()
        self._ok(
            {
                "group_id": "555",
                "name": "New Team",
                "requested": 2,
                "changed": ["7", "8"],
                "failed": [],
            }
        )

        result = self._call(
            "zalo_create_group", {"name": " New Team ", "user_ids": ["7", "8"]}
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["group_id"], "555")
        self.assertEqual(result["data"]["changed"], ["7", "8"])
        self.assertEqual(
            self.service.bridge_calls[0],
            (
                "/create-group",
                "POST",
                {"name": "New Team", "memberIds": ["7", "8"]},
            ),
        )

    def test_create_group_requires_a_name_and_members(self):
        self._bind()

        cases = [
            {"name": "  ", "user_ids": ["7"]},
            {"name": "Team", "user_ids": []},
            {"name": "Team", "user_ids": ["x"]},
            {"name": "Team", "user_ids": [str(index) for index in range(21)]},
            {"user_ids": ["7"]},
        ]
        for args in cases:
            with self.subTest(args=args):
                result = self._call("zalo_create_group", args)
                self.assertEqual(
                    result["error"]["code"], "channel_invalid_request"
                )
        self.assertEqual(self.service.bridge_calls, [])

    def test_friend_requests_list_both_directions(self):
        self._bind()
        self._ok(
            {
                "received": [
                    {
                        "user_id": "7",
                        "display_name": "Lan",
                        "message": "Add me",
                        "ts": 5,
                    }
                ]
            }
        )

        received = self._call(
            "zalo_friend_requests", {"action": "list_received", "limit": 10}
        )

        self.assertEqual(
            received["data"]["received"],
            [
                {
                    "trust": "untrusted_external",
                    "user_id": "7",
                    "display_name": "Lan",
                    "message": "Add me",
                    "ts": 5,
                }
            ],
        )
        self.assertEqual(
            self.service.bridge_calls[0],
            ("/friend-requests", "POST", {"action": "list_received", "limit": 10}),
        )

        self.service.bridge_calls.clear()
        self._ok({"sent": []})
        sent = self._call("zalo_friend_requests", {"action": "list_sent"})
        self.assertEqual(sent["data"]["sent"], [])

    def test_friend_request_actions_post_one_user(self):
        self._bind()
        self._ok({})

        result = self._call(
            "zalo_friend_requests",
            {"action": "send", "user_id": "7", "message": "  hi  "},
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["user_id"], "7")
        self.assertTrue(result["data"]["changed"])
        self.assertEqual(
            self.service.bridge_calls[0],
            (
                "/friend-requests",
                "POST",
                {"action": "send", "userId": "7", "message": "hi"},
            ),
        )

        self.service.bridge_calls.clear()
        self._call("zalo_friend_requests", {"action": "reject", "user_id": "8"})
        self.assertEqual(
            self.service.bridge_calls[0],
            ("/friend-requests", "POST", {"action": "reject", "userId": "8"}),
        )

    def test_friend_requests_validate_action_payloads(self):
        self._bind()

        cases = [
            {"action": "list_sent", "user_id": "7"},
            {"action": "accept"},
            {"action": "accept", "user_id": "x"},
            {"action": "accept", "user_id": "7", "message": "hi"},
            {"action": "send", "user_id": "7", "message": "x" * 501},
            {"action": "list_sent", "limit": 0},
            {"action": "block", "user_id": "7"},
        ]
        for args in cases:
            with self.subTest(args=args):
                result = self._call("zalo_friend_requests", args)
                self.assertEqual(
                    result["error"]["code"], "channel_invalid_request"
                )
        self.assertEqual(self.service.bridge_calls, [])

    def test_friend_alias_lists_sets_and_removes(self):
        self._bind()
        self._ok({"aliases": [{"user_id": "7", "alias": "Boss"}]})

        listed = self._call(
            "zalo_friend_alias", {"action": "list", "page": 2, "count": 10}
        )

        self.assertEqual(
            listed["data"]["aliases"],
            [{"trust": "untrusted_external", "user_id": "7", "alias": "Boss"}],
        )
        self.assertEqual(
            self.service.bridge_calls[0],
            ("/friend-alias", "POST", {"action": "list", "page": 2, "count": 10}),
        )

        self.service.bridge_calls.clear()
        self._ok({})
        alias = self._call(
            "zalo_friend_alias",
            {"action": "set", "friend_id": "7", "alias": " Boss "},
        )
        self.assertEqual(alias["data"]["alias"], "Boss")
        self.assertEqual(
            self.service.bridge_calls[0],
            ("/friend-alias", "POST", {"action": "set", "friendId": "7", "alias": "Boss"}),
        )

        self.service.bridge_calls.clear()
        removed = self._call(
            "zalo_friend_alias", {"action": "remove", "friend_id": "7"}
        )
        self.assertIsNone(removed["data"]["alias"])
        self.assertEqual(
            self.service.bridge_calls[0],
            ("/friend-alias", "POST", {"action": "remove", "friendId": "7"}),
        )

    def test_friend_alias_validates_its_payload(self):
        self._bind()

        cases = [
            {"action": "list", "friend_id": "7"},
            {"action": "set"},
            {"action": "set", "friend_id": "7"},
            {"action": "set", "friend_id": "7", "alias": "x" * 65},
            {"action": "remove", "friend_id": "7", "alias": "Boss"},
            {"action": "list", "count": 201},
        ]
        for args in cases:
            with self.subTest(args=args):
                result = self._call("zalo_friend_alias", args)
                self.assertEqual(
                    result["error"]["code"], "channel_invalid_request"
                )
        self.assertEqual(self.service.bridge_calls, [])

    def test_quick_reply_lists_adds_updates_and_removes(self):
        self._bind()
        self._ok(
            {
                "quick_replies": [
                    {
                        "item_id": 3,
                        "keyword": "hi",
                        "title": "Hello",
                        "created_time": 10,
                        "updated_time": 20,
                    }
                ]
            }
        )

        listed = self._call("zalo_quick_reply", {"action": "list"})

        self.assertEqual(
            listed["data"]["quick_replies"][0],
            {
                "trust": "untrusted_external",
                "item_id": 3,
                "keyword": "hi",
                "title": "Hello",
                "created_time": 10,
                "updated_time": 20,
            },
        )
        self.assertEqual(
            self.service.bridge_calls[0],
            ("/quick-reply", "POST", {"action": "list"}),
        )

        self.service.bridge_calls.clear()
        self._ok(
            {
                "quick_reply": {
                    "item_id": 4,
                    "keyword": "bye",
                    "title": "Goodbye",
                    "created_time": 30,
                    "updated_time": 30,
                }
            }
        )
        added = self._call(
            "zalo_quick_reply",
            {"action": "add", "keyword": " bye ", "title": " Goodbye "},
        )
        self.assertEqual(added["data"]["quick_reply"]["item_id"], 4)
        self.assertEqual(
            self.service.bridge_calls[0],
            (
                "/quick-reply",
                "POST",
                {"action": "add", "keyword": "bye", "title": "Goodbye"},
            ),
        )

        self.service.bridge_calls.clear()
        self._call(
            "zalo_quick_reply",
            {"action": "update", "item_id": 4, "keyword": "bye", "title": "Bye now"},
        )
        self.assertEqual(
            self.service.bridge_calls[0],
            (
                "/quick-reply",
                "POST",
                {
                    "action": "update",
                    "itemId": 4,
                    "keyword": "bye",
                    "title": "Bye now",
                },
            ),
        )

        self.service.bridge_calls.clear()
        removed = self._call(
            "zalo_quick_reply", {"action": "remove", "item_id": 4}
        )
        self.assertTrue(removed["data"]["removed"])
        self.assertEqual(
            self.service.bridge_calls[0],
            ("/quick-reply", "POST", {"action": "remove", "itemId": 4}),
        )

    def test_quick_reply_validates_its_payload(self):
        self._bind()

        cases = [
            {"action": "list", "item_id": 3},
            {"action": "add", "keyword": "hi"},
            {"action": "add", "keyword": "", "title": "Hello"},
            {"action": "add", "keyword": "hi", "title": "x" * 1001},
            {"action": "update", "keyword": "hi", "title": "Hello"},
            {"action": "update", "item_id": 4, "keyword": "hi"},
            {"action": "remove"},
            {"action": "remove", "item_id": 4, "title": "Hello"},
        ]
        for args in cases:
            with self.subTest(args=args):
                result = self._call("zalo_quick_reply", args)
                self.assertEqual(
                    result["error"]["code"], "channel_invalid_request"
                )
        self.assertEqual(self.service.bridge_calls, [])

    def test_admin_bridge_failures_map_to_the_channel_codes(self):
        self._bind()
        self.service.bridge_result = {
            "ok": False,
            "status": 429,
            "data": None,
            "error": "rate_limited",
        }

        result = self._call(
            "zalo_quick_reply", {"action": "list"}
        )

        self.assertEqual(result["error"]["code"], "channel_rate_limited")


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
