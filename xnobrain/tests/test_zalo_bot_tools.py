"""Zalo Bot agent tools: schemas, scope, approval, media, and storage."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from xnobrain.integrations import zalo_bot_tools, zalo_channels
from xnobrain.repositories.channels import ChannelsRepository
from xnobrain.repositories.files import FileRepository
from xnobrain.services.base import ServiceError
from xnobrain.services.channels import ChannelsService

TOOL_NAMES = {
    "zalo_bot_status",
    "zalo_bot_list_chats",
    "zalo_bot_read_history",
    "zalo_bot_search_messages",
    "zalo_bot_contacts",
    "zalo_bot_send_photo",
    "zalo_bot_send_sticker",
    "zalo_bot_send_voice",
    "zalo_bot_group_settings",
}


class _Platform:
    def __init__(self, repository, profile, root_profile):
        self.repository = repository
        self.profile = profile
        self.config = SimpleNamespace(root_profile=root_profile)
        self.agents = SimpleNamespace(get_conversation=lambda *args: {"id": args[1]})

    def _conversation_profile(self, agent_id):
        if agent_id != "agent-1":
            raise ServiceError("agent profile not found", status=404, code="not_found")
        return self.profile


class ZaloBotToolFixture(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        zalo_bot_tools._BINDINGS.clear()
        zalo_bot_tools._SCHEMAS.clear()
        self.addCleanup(zalo_bot_tools._BINDINGS.clear)
        self.addCleanup(zalo_bot_tools._SCHEMAS.clear)

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        profiles = root / "profiles"
        self.root_profile = profiles / "big-brother"
        self.profile = profiles / "agent-1"
        self.root_profile.mkdir(parents=True)
        self.profile.mkdir(parents=True)
        self.profile.joinpath("config.yaml").write_text(
            "platforms:\n"
            "  zalo:\n"
            "    enabled: false\n"
            "    extra:\n"
            "      dm_policy: disabled\n"
            "  zalo_bot:\n"
            "    enabled: true\n"
            "    extra:\n"
            "      dm_policy: pairing\n",
            encoding="utf-8",
        )
        self.profile.joinpath(".env").write_text(
            "ZALO_BOT_ALLOWED_USERS=chat_dm,chat_second\n", encoding="utf-8"
        )
        self.environment = mock.patch.dict(
            os.environ,
            {
                "FT_ENABLE_AGENT_CHANNELS": "true",
                "HERMES_HOME": str(self.root_profile),
                "HERMES_ROOT_PROFILE": str(self.root_profile),
                "HERMES_PROFILES_ROOT": str(profiles),
                "DATA_DIR": str(root / "data"),
                "RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": "",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.repository = FileRepository(
            root / "data", profiles, root_profile=self.root_profile
        )
        self.service = ChannelsService(
            _Platform(self.repository, self.profile, self.root_profile)
        )

    # ── Fixtures ──────────────────────────────────────────────────────

    def _record(
        self,
        text: str,
        ts: float | None = None,
        chat_type: str = "dm",
        message_id: str = "m-1",
    ) -> dict:
        return {
            "message_id": message_id,
            "chat_id": "chat_dm",
            "chat_type": chat_type,
            "direction": "in",
            "sender_id": "sender-1",
            "sender_name": "Sender",
            "type": "text",
            "text": text,
            "ts": time.time() if ts is None else ts,
            "trust": "untrusted_external",
        }

    def _write_history(self, chat_id: str, records: list[dict]) -> Path:
        directory = self.profile / "zalo_bot" / "history"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{chat_id}.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path

    def _write_groups(self, groups: dict) -> None:
        self.service.repository.write_groups(
            self.profile,
            {
                "schema_version": 1,
                "platforms": {"zalo_bot": {"groups": groups}},
            },
        )

    def _bind(self, conversation_id: str = "conv-1") -> None:
        zalo_bot_tools._BINDINGS[conversation_id] = (
            self.service,
            self.profile,
            "agent-1",
            conversation_id,
            "run-1",
            lambda approval: None,
            asyncio.get_running_loop(),
        )

    def _call(self, name: str, args: dict | None = None, conversation_id: str = "conv-1"):
        return json.loads(
            zalo_bot_tools.call(name, args or {}, session_id=conversation_id)
        )

    # ── Registration and binding ──────────────────────────────────────

    def test_schemas_are_strict_and_registered(self):
        registry = mock.Mock()
        registry.get_entry.return_value = None
        with mock.patch("tools.registry.registry", registry):
            zalo_bot_tools.register_tools(self.profile)

        registered = {call.kwargs["name"] for call in registry.register.call_args_list}
        self.assertEqual(registered, TOOL_NAMES)
        for call in registry.register.call_args_list:
            self.assertEqual(call.kwargs["toolset"], "xnobrain_channels")
        for name in TOOL_NAMES:
            schema = zalo_bot_tools._SCHEMAS[name]["function"]
            self.assertIs(schema["parameters"]["additionalProperties"], False)
            self.assertEqual(schema["name"], name)

    async def test_bind_requires_the_feature_flag(self):
        with mock.patch.dict(
            os.environ, {"FT_ENABLE_AGENT_CHANNELS": "false"}
        ), mock.patch.object(
            zalo_channels, "platform_plugin_registered", return_value=True
        ):
            with zalo_bot_tools.bind_run(
                self.service, self.profile, "agent-1", "conv-1", "run-1", None
            ) as enabled:
                self.assertFalse(enabled)
        self.assertNotIn("conv-1", zalo_bot_tools._BINDINGS)

    async def test_bind_requires_an_enabled_bot_platform(self):
        disabled = self.profile.parent / "disabled"
        disabled.mkdir()
        (disabled / "config.yaml").write_text(
            "platforms:\n  zalo_bot:\n    enabled: false\n", encoding="utf-8"
        )
        with mock.patch.object(
            zalo_channels, "platform_plugin_registered", return_value=True
        ):
            with zalo_bot_tools.bind_run(
                self.service, disabled, "agent-1", "conv-1", "run-1", None
            ) as enabled:
                self.assertFalse(enabled)
        self.assertNotIn("conv-1", zalo_bot_tools._BINDINGS)

    async def test_bind_enables_and_installs_tools(self):
        with mock.patch.object(
            zalo_channels, "platform_plugin_registered", return_value=True
        ), mock.patch("tools.registry.registry", mock.Mock()):
            with zalo_bot_tools.bind_run(
                self.service, self.profile, "agent-1", "conv-1", "run-1", None
            ) as enabled:
                self.assertTrue(enabled)
                self.assertIn("conv-1", zalo_bot_tools._BINDINGS)
                agent = SimpleNamespace(
                    tools=[],
                    valid_tool_names=set(),
                    enabled_toolsets=["web"],
                    _delegate_depth=0,
                )
                child = SimpleNamespace(
                    tools=[], valid_tool_names=set(), _delegate_depth=1
                )
                zalo_bot_tools.install_tools(agent, "conv-1", self.profile)
                zalo_bot_tools.install_tools(child, "conv-1", self.profile)
        self.assertNotIn("conv-1", zalo_bot_tools._BINDINGS)
        self.assertEqual(
            {tool["function"]["name"] for tool in agent.tools}, TOOL_NAMES
        )
        self.assertIn(zalo_bot_tools.TOOLSET, agent.enabled_toolsets)
        self.assertEqual(child.tools, [])

    async def test_register_tools_is_scoped_and_unbound_calls_fail(self):
        from hermes_constants import hermes_home_key
        from tools.registry import registry

        other = self.profile.parent / "agent-2"
        other.mkdir()

        zalo_bot_tools.register_tools(self.profile)
        self.assertEqual(set(zalo_bot_tools._SCHEMAS), TOOL_NAMES)
        scope = hermes_home_key(self.profile)
        other_scope = hermes_home_key(other)
        for name in TOOL_NAMES:
            self.assertIsNotNone(registry.get_entry(name, scope=scope))
            self.assertIsNone(registry.get_entry(name, scope=other_scope))

        self._bind()
        agent = SimpleNamespace(
            tools=[], valid_tool_names=set(), enabled_toolsets=["web"]
        )
        zalo_bot_tools.install_tools(agent, "conv-1", self.profile)
        self.assertIn(zalo_bot_tools.TOOLSET, agent.enabled_toolsets)

        unbound = self._call("zalo_bot_status", conversation_id="conv-unbound")
        self.assertEqual(unbound["error"]["code"], "channel_authority_required")

    # ── Reads ─────────────────────────────────────────────────────────

    async def test_read_history_honours_limit_since_and_trust(self):
        now = time.time()
        self._write_history(
            "chat_dm",
            [
                self._record("old", ts=now - 7200, message_id="m-old"),
                self._record("middle", ts=now - 600, message_id="m-mid"),
                self._record("new", ts=now, message_id="m-new"),
            ],
        )
        self._bind()

        latest = self._call(
            "zalo_bot_read_history", {"chat_id": "chat_dm", "limit": 1}
        )
        self.assertTrue(latest["success"])
        self.assertTrue(latest["data"]["truncated"])
        self.assertEqual([m["text"] for m in latest["data"]["messages"]], ["new"])
        self.assertEqual(
            latest["data"]["messages"][0]["trust"], "untrusted_external"
        )

        recent = self._call(
            "zalo_bot_read_history",
            {"chat_id": "chat_dm", "since_hours": 1, "limit": 50},
        )
        self.assertEqual(
            [m["text"] for m in recent["data"]["messages"]], ["middle", "new"]
        )
        self.assertFalse(recent["data"]["truncated"])

    async def test_read_history_refuses_unapproved_group_and_unknown_dm(self):
        self._write_groups({"group_pending": {"status": "pending", "name": "P"}})
        self._write_history(
            "group_pending", [self._record("hi", chat_type="group")]
        )
        self._bind()

        unapproved = self._call(
            "zalo_bot_read_history", {"chat_id": "group_pending"}
        )
        self.assertEqual(
            unapproved["error"]["code"], "channel_group_not_approved"
        )

        unknown = self._call("zalo_bot_read_history", {"chat_id": "chat_unknown"})
        self.assertEqual(unknown["error"]["code"], "channel_chat_not_found")

        invalid = self._call(
            "zalo_bot_read_history", {"chat_id": "../escape"}
        )
        self.assertEqual(invalid["error"]["code"], "channel_recipient_invalid")

    async def test_read_history_approved_group_succeeds(self):
        self._write_groups({"group_1": {"status": "approved", "name": "One"}})
        self._write_history("group_1", [self._record("group hi", chat_type="group")])
        self._bind()

        result = self._call("zalo_bot_read_history", {"chat_id": "group_1"})

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["messages"][0]["text"], "group hi")

    async def test_channel_run_reads_only_its_own_thread(self):
        self._write_history("chat_dm", [self._record("own")])
        self._write_history("chat_second", [self._record("other")])
        self.service.repository.write_thread(
            self.profile,
            "zalo_bot",
            "chat_dm",
            agent_id="agent-1",
            conversation_id="conv-1",
            title="Thread",
            sender_id="sender-1",
            timestamp=time.time(),
        )
        self._bind()

        own = self._call("zalo_bot_read_history", {"chat_id": "chat_dm"})
        self.assertTrue(own["success"])

        other = self._call("zalo_bot_read_history", {"chat_id": "chat_second"})
        self.assertEqual(
            other["error"]["code"], "channel_thread_out_of_scope"
        )

    async def test_list_chats_merges_history_and_approved_groups(self):
        self._write_groups({"group_1": {"status": "approved", "name": "One"}})
        self._write_history("chat_dm", [self._record("hi")])
        self._write_history("group_1", [self._record("yo", chat_type="group")])
        self._bind()

        result = self._call("zalo_bot_list_chats")

        self.assertTrue(result["success"])
        chats = {item["chat_id"]: item for item in result["data"]["chats"]}
        self.assertEqual(set(chats), {"chat_dm", "group_1"})
        self.assertEqual(chats["group_1"]["chat_type"], "group")
        self.assertTrue(chats["group_1"]["approved"])
        self.assertEqual(chats["group_1"]["name"], "One")
        self.assertEqual(chats["chat_dm"]["message_count"], 1)

    async def test_list_chats_in_channel_run_returns_only_the_origin(self):
        self._write_history("chat_dm", [self._record("own")])
        self._write_history("chat_second", [self._record("other")])
        self.service.repository.write_thread(
            self.profile,
            "zalo_bot",
            "chat_dm",
            agent_id="agent-1",
            conversation_id="conv-1",
            title="Thread",
            sender_id="sender-1",
            timestamp=time.time(),
        )
        self._bind()

        result = self._call("zalo_bot_list_chats")

        self.assertEqual(
            [item["chat_id"] for item in result["data"]["chats"]], ["chat_dm"]
        )

    async def test_list_chats_for_another_origin_platform_is_empty(self):
        self._write_history("chat_dm", [self._record("own")])
        self.service.repository.write_thread(
            self.profile,
            "zalo",
            "111",
            agent_id="agent-1",
            conversation_id="conv-9",
            title="Personal",
            sender_id="111",
            timestamp=time.time(),
        )
        self._bind("conv-9")

        result = self._call("zalo_bot_list_chats", conversation_id="conv-9")

        self.assertEqual(result["data"]["chats"], [])

    async def test_search_is_case_insensitive_and_scoped(self):
        self._write_history(
            "chat_dm",
            [
                self._record("Hello World", message_id="m-1"),
                self._record("nothing", message_id="m-2"),
            ],
        )
        self._write_history(
            "chat_second", [self._record("hello there", message_id="m-3")]
        )
        self._bind()

        result = self._call(
            "zalo_bot_search_messages", {"query": "HELLO"}
        )
        self.assertTrue(result["success"])
        matches = result["data"]["messages"]
        self.assertEqual({m["chat_id"] for m in matches}, {"chat_dm", "chat_second"})
        self.assertEqual(
            {m["text"] for m in matches}, {"Hello World", "hello there"}
        )
        self.assertEqual(matches[0]["trust"], "untrusted_external")

        only = self._call(
            "zalo_bot_search_messages", {"query": "hello", "limit": 1}
        )
        self.assertTrue(only["data"]["truncated"])
        self.assertEqual(len(only["data"]["messages"]), 1)

    async def test_search_in_channel_run_is_limited_to_the_origin(self):
        self._write_history("chat_dm", [self._record("hello own")])
        self._write_history("chat_second", [self._record("hello other")])
        self.service.repository.write_thread(
            self.profile,
            "zalo_bot",
            "chat_dm",
            agent_id="agent-1",
            conversation_id="conv-1",
            title="Thread",
            sender_id="sender-1",
            timestamp=time.time(),
        )
        self._bind()

        result = self._call("zalo_bot_search_messages", {"query": "hello"})
        self.assertEqual(
            [m["text"] for m in result["data"]["messages"]], ["hello own"]
        )

        explicit = self._call(
            "zalo_bot_search_messages",
            {"query": "hello", "chat_id": "chat_second"},
        )
        self.assertEqual(
            explicit["error"]["code"], "channel_thread_out_of_scope"
        )

    async def test_contacts_are_trusted_as_untrusted_external_data(self):
        crm = self.profile / "zalo-bot-crm"
        crm.mkdir()
        (crm / "chat_dm_contacts.json").write_text(
            json.dumps(
                {
                    "u1": {
                        "user_id": "u1",
                        "phone": "0900",
                        "name": "Nam",
                        "labels": ["vip"],
                        "updated_at": 2,
                    },
                    "u2": {
                        "user_id": "u2",
                        "phone": "",
                        "name": "Lan",
                        "labels": ["lead"],
                        "updated_at": 1,
                    },
                }
            ),
            encoding="utf-8",
        )
        self._bind()

        result = self._call("zalo_bot_contacts", {"chat_id": "chat_dm"})
        self.assertEqual(
            [contact["user_id"] for contact in result["data"]["contacts"]],
            ["u1", "u2"],
        )
        self.assertTrue(
            all(
                contact["trust"] == "untrusted_external"
                for contact in result["data"]["contacts"]
            )
        )

        filtered = self._call(
            "zalo_bot_contacts", {"chat_id": "chat_dm", "label": "VIP"}
        )
        self.assertEqual(
            [contact["user_id"] for contact in filtered["data"]["contacts"]],
            ["u1"],
        )

        unknown = self._call("zalo_bot_contacts", {"chat_id": "chat_unknown"})
        self.assertEqual(unknown["error"]["code"], "channel_chat_not_found")

    # ── Sends ─────────────────────────────────────────────────────────

    def _assert_out_record(self, chat_id: str, message_type: str) -> dict:
        path = self.profile / "zalo_bot" / "history" / f"{chat_id}.jsonl"
        self.assertTrue(path.is_file())
        record = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(record["direction"], "out")
        self.assertEqual(record["type"], message_type)
        self.assertEqual(record["sender_id"], "")
        self.assertEqual(record["sender_name"], "bot")
        self.assertEqual(record["trust"], "untrusted_external")
        return record

    async def test_text_send_appends_an_outbound_record(self):
        with mock.patch.object(
            zalo_channels,
            "send_text",
            mock.AsyncMock(return_value={"message_id": "m-text"}),
        ):
            result = await self.service.send_message(
                "agent-1", "conv-1", "chat_dm", "Chat", "hello", platform="zalo_bot"
            )

        self.assertEqual(result["message_id"], "m-text")
        record = self._assert_out_record("chat_dm", "text")
        self.assertEqual(record["text"], "hello")
        self.assertEqual(record["chat_type"], "dm")
        self.assertEqual(record["message_id"], "m-text")

    async def test_media_sends_request_approval_and_store_out_records(self):
        self._bind()
        cases = (
            (
                "zalo_bot_send_photo",
                {"chat_id": "chat_dm", "thread_type": "dm",
                 "photo_url": "https://cdn.example/photo.jpg", "caption": "look"},
                "zalo_bot.send_photo",
                ("chat_dm", "photo", "https://cdn.example/photo.jpg", "look"),
                "image",
            ),
            (
                "zalo_bot_send_sticker",
                {"chat_id": "chat_dm", "thread_type": "dm", "sticker": "st-1"},
                "zalo_bot.send_sticker",
                ("chat_dm", "sticker", "st-1", ""),
                "sticker",
            ),
            (
                "zalo_bot_send_voice",
                {"chat_id": "chat_dm", "thread_type": "dm",
                 "voice_url": "https://cdn.example/voice.aac"},
                "zalo_bot.send_voice",
                ("chat_dm", "voice", "https://cdn.example/voice.aac", ""),
                "voice",
            ),
        )
        for name, args, pattern, delivered, record_type in cases:
            with self.subTest(tool=name):
                history = self.profile / "zalo_bot" / "history" / "chat_dm.jsonl"
                if history.exists():
                    history.unlink()
                with mock.patch.object(
                    zalo_bot_tools,
                    "_request_approval",
                    return_value={"resolved": True, "choice": "once"},
                ) as approval, mock.patch.object(
                    zalo_bot_tools,
                    "_deliver_media",
                    return_value={"message_id": "sent-1"},
                ) as deliver:
                    result = self._call(name, args)

                self.assertTrue(result["success"], result)
                self.assertEqual(result["data"]["message_id"], "sent-1")
                self.assertEqual(
                    approval.call_args.args[2]["pattern_key"], pattern
                )
                deliver.assert_called_once_with(
                    mock.ANY, *delivered
                )
                record = self._assert_out_record("chat_dm", record_type)
                self.assertEqual(record["media_url"], delivered[2])

    async def test_denied_media_send_stores_nothing(self):
        self._bind()
        with mock.patch.object(
            zalo_bot_tools,
            "_request_approval",
            return_value={"resolved": True, "choice": "deny"},
        ), mock.patch.object(zalo_bot_tools, "_deliver_media") as deliver:
            result = self._call(
                "zalo_bot_send_photo",
                {
                    "chat_id": "chat_dm",
                    "thread_type": "dm",
                    "photo_url": "https://cdn.example/photo.jpg",
                },
            )
        self.assertEqual(result["error"]["code"], "channel_send_denied")
        deliver.assert_not_called()
        self.assertFalse(
            (
                self.profile / "zalo_bot" / "history" / "chat_dm.jsonl"
            ).exists()
        )

    async def test_voice_to_a_group_is_refused_before_approval(self):
        self._bind()
        with mock.patch.object(zalo_bot_tools, "_request_approval") as approval:
            result = self._call(
                "zalo_bot_send_voice",
                {
                    "chat_id": "chat_dm",
                    "thread_type": "group",
                    "voice_url": "https://cdn.example/voice.aac",
                },
            )
        self.assertEqual(result["error"]["code"], "channel_group_unsupported")
        approval.assert_not_called()

    async def test_voice_to_a_group_id_is_refused_even_as_a_dm(self):
        self._write_groups({"group_1": {"status": "approved"}})
        self._bind()
        with mock.patch.object(zalo_bot_tools, "_request_approval") as approval:
            result = self._call(
                "zalo_bot_send_voice",
                {
                    "chat_id": "group_1",
                    "thread_type": "dm",
                    "voice_url": "https://cdn.example/voice.aac",
                },
            )
        self.assertEqual(result["error"]["code"], "channel_group_unsupported")
        approval.assert_not_called()

    async def test_media_to_an_unapproved_group_is_refused(self):
        self._write_groups({"group_1": {"status": "pending"}})
        self._bind()
        with mock.patch.object(zalo_bot_tools, "_request_approval") as approval:
            result = self._call(
                "zalo_bot_send_photo",
                {
                    "chat_id": "group_1",
                    "thread_type": "group",
                    "photo_url": "https://cdn.example/photo.jpg",
                },
            )
        self.assertEqual(result["error"]["code"], "channel_group_not_approved")
        approval.assert_not_called()

    async def test_media_send_failure_returns_a_content_free_code(self):
        self._bind()
        with mock.patch.object(
            zalo_bot_tools,
            "_request_approval",
            return_value={"resolved": True, "choice": "once"},
        ), mock.patch.object(
            zalo_bot_tools,
            "_deliver_media",
            side_effect=zalo_channels.ChannelDeliveryError("channel_send_failed"),
        ):
            result = self._call(
                "zalo_bot_send_sticker",
                {"chat_id": "chat_dm", "thread_type": "dm", "sticker": "st-1"},
            )
        self.assertEqual(result["error"]["code"], "channel_send_failed")
        self.assertFalse(
            (
                self.profile / "zalo_bot" / "history" / "chat_dm.jsonl"
            ).exists()
        )

    # ── Group settings ────────────────────────────────────────────────

    async def test_group_settings_write_the_plugin_file(self):
        self._write_groups({"group_1": {"status": "approved", "name": "One"}})
        self._bind()
        with mock.patch.object(
            zalo_bot_tools,
            "_request_approval",
            return_value={"resolved": True, "choice": "once"},
        ) as approval:
            result = self._call(
                "zalo_bot_group_settings",
                {"chat_id": "group_1", "follow": True, "silent": False},
            )

        self.assertTrue(result["success"])
        self.assertEqual(
            approval.call_args.args[2]["pattern_key"], "zalo_bot.group_settings"
        )
        path = self.profile / "zalo-bot-settings" / "group_1.json"
        self.assertEqual(
            json.loads(path.read_text(encoding="utf-8")),
            {"follow": True, "silent": False},
        )
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    async def test_group_settings_follow_off_deletes_history(self):
        self._write_groups({"group_1": {"status": "approved"}})
        path = self._write_history(
            "group_1", [self._record("hi", chat_type="group")]
        )
        self._bind()
        with mock.patch.object(
            zalo_bot_tools,
            "_request_approval",
            return_value={"resolved": True, "choice": "always"},
        ):
            result = self._call(
                "zalo_bot_group_settings", {"chat_id": "group_1", "follow": False}
            )

        self.assertTrue(result["success"])
        self.assertFalse(path.exists())

    async def test_denied_group_settings_leave_files_unchanged(self):
        self._write_groups({"group_1": {"status": "approved"}})
        settings_dir = self.profile / "zalo-bot-settings"
        settings_dir.mkdir()
        settings_path = settings_dir / "group_1.json"
        settings_path.write_text('{"follow": true}\n', encoding="utf-8")
        history_path = self._write_history(
            "group_1", [self._record("hi", chat_type="group")]
        )
        self._bind()
        with mock.patch.object(
            zalo_bot_tools,
            "_request_approval",
            return_value={"resolved": True, "choice": "deny"},
        ):
            result = self._call(
                "zalo_bot_group_settings", {"chat_id": "group_1", "follow": False}
            )

        self.assertEqual(result["error"]["code"], "channel_action_denied")
        self.assertEqual(
            json.loads(settings_path.read_text(encoding="utf-8")), {"follow": True}
        )
        self.assertTrue(history_path.exists())

    # ── Status ────────────────────────────────────────────────────────

    async def test_status_reports_the_bot_channel_and_identity(self):
        self._bind()
        with mock.patch.object(
            zalo_bot_tools,
            "_bot_identity",
            return_value={"id": "bot-1", "display_name": "Bot"},
        ):
            result = self._call("zalo_bot_status")

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["bot"], {"id": "bot-1", "name": "Bot"})
        self.assertEqual(result["data"]["dm_policy"], "pairing")
        self.assertEqual(result["data"]["history_retention_days"], 30)
        self.assertIs(result["data"]["connected"], False)

    async def test_status_still_succeeds_when_get_me_fails(self):
        self._bind()
        with mock.patch.object(
            zalo_bot_tools, "_bot_identity", side_effect=RuntimeError("api down")
        ):
            result = self._call("zalo_bot_status")

        self.assertTrue(result["success"])
        self.assertIsNone(result["data"]["bot"])

    async def test_tool_calls_require_a_binding(self):
        result = self._call("zalo_bot_status", conversation_id="missing")
        self.assertEqual(result["error"]["code"], "channel_authority_required")


class ZaloBotToolRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.profiles = self.root / "profiles"
        self.profile = self.profiles / "agent-1"
        self.profile.mkdir(parents=True)
        self.base = FileRepository(
            self.root / "data", self.profiles, root_profile=self.profile
        )
        self.repository = ChannelsRepository(self.base)

    def test_append_bot_history_writes_a_private_line(self):
        self.assertTrue(
            self.repository.append_bot_history(
                self.profile,
                "chat_1",
                {
                    "message_id": "m-1",
                    "chat_type": "group",
                    "direction": "in",
                    "sender_id": "bot",
                    "sender_name": "bot",
                    "type": "image",
                    "text": "caption",
                    "media_url": "https://cdn.example/p.jpg",
                    "ts": 10,
                },
            )
        )
        path = self.profile / "zalo_bot" / "history" / "chat_1.jsonl"
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(
            (self.profile / "zalo_bot" / "history").stat().st_mode & 0o777,
            0o700,
        )
        records = [
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["direction"], "out")
        self.assertEqual(records[0]["trust"], "untrusted_external")
        self.assertEqual(records[0]["chat_id"], "chat_1")

        self.assertFalse(
            self.repository.append_bot_history(self.profile, "../bad", {"ts": 1})
        )

    def test_bot_contacts_reads_and_filters(self):
        crm = self.profile / "zalo-bot-crm"
        crm.mkdir()
        (crm / "chat_1_contacts.json").write_text(
            json.dumps(
                {
                    "u1": {"user_id": "u1", "name": "Nam", "labels": ["vip"]},
                    "u2": {"user_id": "u2", "name": "Lan", "labels": []},
                    "bad": "not a record",
                }
            ),
            encoding="utf-8",
        )

        contacts = self.repository.bot_contacts(self.profile, "chat_1")

        self.assertEqual([contact["user_id"] for contact in contacts], ["u1", "u2"])
        self.assertEqual(self.repository.bot_contacts(self.profile, "../bad"), [])

    def test_write_bot_group_settings_merges_and_is_private(self):
        settings = self.repository.write_bot_group_settings(
            self.profile, "group_1", {"follow": False}
        )
        self.assertEqual(settings, {"follow": False})
        self.repository.write_bot_group_settings(
            self.profile, "group_1", {"silent": True}
        )
        path = self.profile / "zalo-bot-settings" / "group_1.json"
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(
            json.loads(path.read_text(encoding="utf-8")),
            {"follow": False, "silent": True},
        )

    def test_symlinked_plugin_directories_are_refused(self):
        outside_crm = self.root / "outside-crm"
        outside_crm.mkdir()
        (outside_crm / "chat_1_contacts.json").write_text("{}", encoding="utf-8")
        os.symlink(outside_crm, self.profile / "zalo-bot-crm")
        with self.assertRaises(Exception) as raised:
            self.repository.bot_contacts(self.profile, "chat_1")
        self.assertEqual(
            getattr(raised.exception, "code", ""), "channel_path_unsafe"
        )

        outside_settings = self.root / "outside-settings"
        outside_settings.mkdir()
        os.symlink(outside_settings, self.profile / "zalo-bot-settings")
        with self.assertRaises(Exception) as raised:
            self.repository.write_bot_group_settings(
                self.profile, "group_1", {"follow": False}
            )
        self.assertEqual(
            getattr(raised.exception, "code", ""), "channel_path_unsafe"
        )


class _PlatformKey:
    def __init__(self, value):
        self.value = value


class SendBotMediaTests(unittest.IsolatedAsyncioTestCase):
    def _install_adapter_module(self, helper):
        module = types.ModuleType("fake_zalo_bot_adapter")
        module._standalone_api = helper
        sys.modules[module.__name__] = module
        self.addCleanup(sys.modules.pop, module.__name__, None)

        def sender(pconfig, chat_id, message, **kwargs):
            return None

        sender.__module__ = module.__name__
        return sender

    async def test_send_bot_media_maps_each_kind_to_the_allowlist(self):
        calls = []

        async def helper(pconfig, action, **kwargs):
            calls.append((action, kwargs))
            return {"success": True, "message_id": "m-1"}

        sender = self._install_adapter_module(helper)
        platform = _PlatformKey("zalo_bot")
        pconfig = SimpleNamespace(
            enabled=True,
            extra={},
            standalone_sender_fn=sender,
        )
        config = SimpleNamespace(platforms={platform: pconfig})
        with (
            mock.patch("tools.send_message_tool.prepare_send_message_platforms"),
            mock.patch(
                "gateway.platform_registry.platform_registry.get",
                return_value=pconfig,
            ),
            mock.patch(
                "gateway.config.load_gateway_config", return_value=config
            ),
        ):
            photo = await zalo_channels.send_bot_media(
                "chat_1", "photo", "https://cdn.example/p.jpg", "look"
            )
            sticker = await zalo_channels.send_bot_media(
                "chat_1", "sticker", "st-1"
            )
            voice = await zalo_channels.send_bot_media(
                "chat_1", "voice", "https://cdn.example/v.aac"
            )

        self.assertEqual(photo, {"message_id": "m-1"})
        self.assertEqual(sticker, {"message_id": "m-1"})
        self.assertEqual(voice, {"message_id": "m-1"})
        self.assertEqual(
            calls,
            [
                (
                    "send_photo",
                    {"photo_url": "https://cdn.example/p.jpg", "caption": "look"},
                ),
                ("send_sticker", {"sticker": "st-1"}),
                ("send_voice", {"voice_url": "https://cdn.example/v.aac"}),
            ],
        )

    async def test_send_bot_media_requires_the_adapter_helper(self):
        platform = _PlatformKey("zalo_bot")
        pconfig = SimpleNamespace(
            enabled=True,
            extra={},
            standalone_sender_fn=lambda *args, **kwargs: None,
        )
        with (
            mock.patch("tools.send_message_tool.prepare_send_message_platforms"),
            mock.patch(
                "gateway.platform_registry.platform_registry.get",
                return_value=pconfig,
            ),
        ):
            with self.assertRaises(zalo_channels.ChannelDeliveryError) as raised:
                await zalo_channels.send_bot_media(
                    "chat_1", "photo", "https://cdn.example/p.jpg"
                )
        self.assertEqual(
            raised.exception.code, "channel_platform_unavailable"
        )

    async def test_send_bot_media_rejects_an_invalid_chat_id(self):
        with self.assertRaises(zalo_channels.ChannelDeliveryError) as raised:
            await zalo_channels.send_bot_media("../escape", "photo", "https://x")
        self.assertEqual(raised.exception.code, "channel_recipient_invalid")


if __name__ == "__main__":
    unittest.main()
