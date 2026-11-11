"""Channel recipient resolution, tool approval flow, and link persistence."""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from xnobrain.integrations import channel_tools, zalo_channels
from xnobrain.repositories.files import FileRepository
from xnobrain.services.base import ServiceError
from xnobrain.services.channels import ChannelsService


class _Platform:
    def __init__(self, repository, profile):
        self.repository = repository
        self.profile = profile
        self.agents = SimpleNamespace(get_conversation=lambda *args: {"id": args[1]})

    def _conversation_profile(self, agent_id):
        if agent_id != "agent-1":
            raise ServiceError("agent profile not found", status=404, code="not_found")
        return self.profile


class _FakeService:
    def __init__(self, resolution, bot_resolution=None):
        self.resolution = resolution
        self.bot_resolution = bot_resolution or {"status": "not_found"}
        self.calls = []
        self.bot_calls = []

    def resolve_recipient(self, agent_id, recipient):
        return self.resolution

    def resolve_bot_recipient(self, agent_id, recipient, thread_type="dm"):
        self.bot_calls.append((recipient, thread_type))
        return self.bot_resolution

    async def send_message(
        self, agent_id, conversation_id, thread_id, name, text, platform="zalo"
    ):
        self.calls.append((agent_id, conversation_id, thread_id, name, text, platform))
        return {"message_id": "sent-1", "name": name}


class _PlatformKey:
    def __init__(self, value):
        self.value = value


class SendTextPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_text_routes_through_hermes_send_platform(self):
        platform = _PlatformKey("zalo")
        pconfig = SimpleNamespace(enabled=True, extra={})
        config = SimpleNamespace(platforms={platform: pconfig})
        with (
            mock.patch("tools.send_message_tool.prepare_send_message_platforms"),
            mock.patch(
                "gateway.platform_registry.platform_registry.get",
                return_value=SimpleNamespace(max_message_length=2000),
            ),
            mock.patch("gateway.config.Platform", return_value=platform),
            mock.patch("gateway.config.load_gateway_config", return_value=config),
            mock.patch(
                "tools.send_message_tool._send_to_platform",
                mock.AsyncMock(return_value={"success": True, "message_id": "9"}),
            ) as send,
        ):
            result = await zalo_channels.send_text("111", "hello")

        self.assertEqual(result, {"message_id": "9"})
        send.assert_awaited_once_with(platform, pconfig, "111", "hello")

    async def test_send_text_maps_platform_errors_to_safe_codes(self):
        platform = _PlatformKey("zalo")
        pconfig = SimpleNamespace(enabled=True, extra={})
        config = SimpleNamespace(platforms={platform: pconfig})
        with (
            mock.patch("tools.send_message_tool.prepare_send_message_platforms"),
            mock.patch(
                "gateway.platform_registry.platform_registry.get",
                return_value=SimpleNamespace(max_message_length=2000),
            ),
            mock.patch("gateway.config.Platform", return_value=platform),
            mock.patch("gateway.config.load_gateway_config", return_value=config),
            mock.patch(
                "tools.send_message_tool._send_to_platform",
                mock.AsyncMock(return_value={"error": "bridge refused"}),
            ),
        ):
            with self.assertRaises(zalo_channels.ChannelDeliveryError) as raised:
                await zalo_channels.send_text("111", "hello")

        self.assertEqual(raised.exception.code, "channel_send_failed")


class RecipientResolutionTests(unittest.TestCase):
    def test_exact_thread_id_and_name_resolve(self):
        history = [{"threadId": "111", "senderName": "Nam"}]
        directory = [{"id": "222", "name": "Lan", "type": "dm"}]

        by_id = zalo_channels.resolve_recipient(history, directory, "111")
        by_name = zalo_channels.resolve_recipient(history, directory, "nam")
        prefixed = zalo_channels.resolve_recipient(history, directory, "zalo:Lan")

        self.assertEqual(by_id["thread_id"], "111")
        self.assertEqual(by_name["thread_id"], "111")
        self.assertEqual(prefixed["thread_id"], "222")

    def test_unknown_recipient_lists_known_names(self):
        result = zalo_channels.resolve_recipient(
            [{"threadId": "111", "senderName": "Nam"}],
            [{"id": "222", "name": "Lan"}],
            "Nobody",
        )

        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["known_names"], ["Lan", "Nam"])

    def test_duplicate_names_return_candidates(self):
        result = zalo_channels.resolve_recipient(
            [{"threadId": "111", "senderName": "Nam"}],
            [{"id": "222", "name": "Nam"}],
            "Nam",
        )

        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(len(result["candidates"]), 2)


class ChannelServiceSendTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.profile = root / "profiles" / "agent-1"
        self.profile.mkdir(parents=True)
        self.environment = mock.patch.dict(
            os.environ, {"RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": ""}
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.repository = FileRepository(root / "data", root / "profiles")
        self.service = ChannelsService(_Platform(self.repository, self.profile))

    async def test_approved_send_writes_the_link_after_delivery(self):
        with mock.patch.object(
            zalo_channels, "send_text", mock.AsyncMock(return_value={"message_id": "42"})
        ) as send:
            result = await self.service.send_message(
                "agent-1", "conv-1", "111", "Nam", "hello"
            )

        send.assert_awaited_once_with("111", "hello", platform="zalo")
        self.assertEqual(result["message_id"], "42")
        links = json.loads((self.profile / "zalo" / "links.json").read_text())
        self.assertEqual(links["111"]["agent_id"], "agent-1")
        self.assertEqual(links["111"]["conversation_id"], "conv-1")
        self.assertEqual(links["111"]["name"], "Nam")
        self.assertIn("ts", links["111"])

    async def test_failed_send_does_not_write_a_link(self):
        with mock.patch.object(
            zalo_channels,
            "send_text",
            mock.AsyncMock(side_effect=zalo_channels.ChannelDeliveryError("channel_send_failed")),
        ):
            with self.assertRaises(ServiceError) as raised:
                await self.service.send_message("agent-1", "conv-1", "111", "Nam", "hello")

        self.assertEqual(raised.exception.code, "channel_send_failed")
        self.assertFalse((self.profile / "zalo" / "links.json").exists())

    async def test_overlong_text_is_rejected_before_delivery(self):
        with mock.patch.object(
            zalo_channels, "max_message_length", return_value=2000
        ), mock.patch.object(zalo_channels, "send_text", mock.AsyncMock()) as send:
            with self.assertRaises(ServiceError) as raised:
                await self.service.send_message(
                    "agent-1", "conv-1", "111", "Nam", "x" * 2001
                )

        self.assertEqual(raised.exception.code, "channel_message_too_long")
        send.assert_not_awaited()

    async def test_bot_send_uses_the_generalized_sender_without_a_personal_link(self):
        with mock.patch.object(
            zalo_channels, "send_text", mock.AsyncMock(return_value={"message_id": "7"})
        ) as send:
            result = await self.service.send_message(
                "agent-1",
                "conv-1",
                "chat_1",
                "Bot chat",
                "hello",
                platform="zalo_bot",
            )

        send.assert_awaited_once_with("chat_1", "hello", platform="zalo_bot")
        self.assertEqual(result["message_id"], "7")
        self.assertEqual(result["name"], "Bot chat")
        self.assertFalse((self.profile / "zalo" / "links.json").exists())


class BotRecipientResolutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
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
        self.platform = _Platform(self.repository, self.profile)
        self.platform.config = SimpleNamespace(root_profile=self.root_profile)
        self.service = ChannelsService(self.platform)

    def _approve_pairing(self, chat_id: str, name: str) -> None:
        from gateway.pairing import PairingStore

        store = PairingStore(profile="agent-1")
        code = store.generate_code("zalo_bot", chat_id, name)
        self.assertIsNotNone(store.approve_code("zalo_bot", code))

    def test_dm_requires_an_approved_pairing_or_allowed_user(self):
        missing = self.service.resolve_bot_recipient("agent-1", "chat_1", "dm")
        self.assertEqual(missing["status"], "not_found")

        self._approve_pairing("chat_1", "Nam")
        paired = self.service.resolve_bot_recipient("agent-1", "chat_1", "dm")
        self.assertEqual(paired["status"], "ok")
        self.assertEqual(paired["name"], "Nam")

        self.profile.joinpath(".env").write_text(
            "ZALO_BOT_ALLOWED_USERS=chat_2\n", encoding="utf-8"
        )
        allowed = self.service.resolve_bot_recipient("agent-1", "chat_2", "dm")
        self.assertEqual(allowed["status"], "ok")

        invalid = self.service.resolve_bot_recipient("agent-1", "../escape", "dm")
        self.assertEqual(invalid["status"], "invalid")

    def test_group_requires_owner_approval(self):
        self.service.repository.write_groups(
            self.profile,
            {
                "schema_version": 1,
                "platforms": {
                    "zalo_bot": {
                        "groups": {
                            "group_1": {
                                "status": "approved",
                                "name": "Group One",
                            }
                        }
                    }
                },
            },
        )

        approved = self.service.resolve_bot_recipient("agent-1", "group_1", "group")
        self.assertEqual(approved["status"], "ok")
        self.assertEqual(approved["name"], "Group One")

        pending = self.service.resolve_bot_recipient("agent-1", "group_2", "group")
        self.assertEqual(pending["status"], "group_not_approved")


class ChannelToolCallTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        channel_tools._BINDINGS.clear()
        channel_tools._PLATFORMS.clear()
        self.addCleanup(channel_tools._BINDINGS.clear)
        self.addCleanup(channel_tools._PLATFORMS.clear)

    def _bind(self, service, platforms=None):
        channel_tools._BINDINGS["conv-1"] = (
            service,
            "agent-1",
            "conv-1",
            "run-1",
            lambda approval: None,
            None,
        )
        channel_tools._PLATFORMS["conv-1"] = frozenset(
            platforms if platforms is not None else {"zalo"}
        )

    def _call(self, args):
        return json.loads(channel_tools.call(args, session_id="conv-1"))

    async def test_deny_sends_nothing(self):
        service = _FakeService({"status": "ok", "thread_id": "111", "name": "Nam"})
        self._bind(service)
        with mock.patch.object(
            channel_tools,
            "_request_approval",
            return_value={"resolved": True, "choice": "deny"},
        ), mock.patch.object(channel_tools, "_deliver") as deliver:
            result = self._call({"recipient": "Nam", "text": "hello"})

        self.assertEqual(result["error"]["code"], "channel_send_denied")
        self.assertEqual(service.calls, [])
        deliver.assert_not_called()

    async def test_approval_sends_and_returns_the_link_identity(self):
        service = _FakeService({"status": "ok", "thread_id": "111", "name": "Nam"})
        self._bind(service)

        def deliver(binding, thread_id, name, text, platform="zalo"):
            service.calls.append(("agent-1", "conv-1", thread_id, name, text))
            return {"message_id": "sent-1"}

        with mock.patch.object(
            channel_tools,
            "_request_approval",
            return_value={"resolved": True, "choice": "once"},
        ) as approval, mock.patch.object(channel_tools, "_deliver", side_effect=deliver):
            result = self._call({"recipient": "Nam", "text": "hello"})

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["thread_id"], "111")
        self.assertEqual(service.calls, [("agent-1", "conv-1", "111", "Nam", "hello")])
        approval.assert_called_once()
        self.assertEqual(approval.call_args.args[2]["pattern_key"], "zalo.send_message")

    async def test_ambiguous_recipient_never_asks_for_approval(self):
        service = _FakeService(
            {
                "status": "ambiguous",
                "candidates": [
                    {"thread_id": "111", "name": "Nam"},
                    {"thread_id": "222", "name": "Nam"},
                ],
            }
        )
        self._bind(service)
        with mock.patch.object(channel_tools, "_request_approval") as approval:
            result = self._call({"recipient": "Nam", "text": "hello"})

        self.assertEqual(result["error"]["code"], "channel_recipient_ambiguous")
        approval.assert_not_called()

    async def test_unknown_recipient_returns_known_names(self):
        service = _FakeService({"status": "not_found", "known_names": ["Lan", "Nam"]})
        self._bind(service)
        with mock.patch.object(channel_tools, "_request_approval") as approval:
            result = self._call({"recipient": "Nobody", "text": "hello"})

        self.assertEqual(result["error"]["code"], "channel_recipient_not_found")
        self.assertEqual(result["known_names"], ["Lan", "Nam"])
        approval.assert_not_called()

    async def test_bot_dm_to_a_paired_user_is_approved_and_delivered(self):
        service = _FakeService(
            {"status": "ok"},
            bot_resolution={"status": "ok", "thread_id": "chat_1", "name": "Nam"},
        )
        self._bind(service, platforms={"zalo_bot"})
        delivered = []

        def deliver(binding, thread_id, name, text, platform="zalo"):
            delivered.append((thread_id, name, text, platform))
            return {"message_id": "sent-9"}

        with mock.patch.object(
            channel_tools,
            "_request_approval",
            return_value={"resolved": True, "choice": "once"},
        ) as approval, mock.patch.object(
            channel_tools, "_deliver", side_effect=deliver
        ):
            result = self._call(
                {"platform": "zalo_bot", "recipient": "chat_1", "text": "hello"}
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["message_id"], "sent-9")
        self.assertEqual(service.bot_calls, [("chat_1", "dm")])
        self.assertEqual(delivered, [("chat_1", "Nam", "hello", "zalo_bot")])
        self.assertEqual(approval.call_args.args[2]["pattern_key"], "zalo_bot.send_message")
        self.assertIn("Zalo Bot", approval.call_args.args[2]["command"])

    async def test_bot_group_requires_owner_approval(self):
        service = _FakeService(
            {"status": "ok"},
            bot_resolution={"status": "group_not_approved", "thread_id": "group_1"},
        )
        self._bind(service, platforms={"zalo_bot"})
        with mock.patch.object(channel_tools, "_request_approval") as approval:
            result = self._call(
                {
                    "platform": "zalo_bot",
                    "thread_type": "group",
                    "recipient": "group_1",
                    "text": "hello",
                }
            )

        self.assertEqual(result["error"]["code"], "channel_group_not_approved")
        approval.assert_not_called()

    async def test_approved_bot_group_is_delivered(self):
        service = _FakeService(
            {"status": "ok"},
            bot_resolution={
                "status": "ok",
                "thread_id": "group_1",
                "name": "Group One",
            },
        )
        self._bind(service, platforms={"zalo_bot"})
        delivered = []

        def deliver(binding, thread_id, name, text, platform="zalo"):
            delivered.append((thread_id, name, text, platform))
            return {"message_id": "sent-10"}

        with mock.patch.object(
            channel_tools,
            "_request_approval",
            return_value={"resolved": True, "choice": "always"},
        ), mock.patch.object(channel_tools, "_deliver", side_effect=deliver):
            result = self._call(
                {
                    "platform": "zalo_bot",
                    "thread_type": "group",
                    "recipient": "group_1",
                    "text": "hello",
                }
            )

        self.assertTrue(result["success"])
        self.assertEqual(service.bot_calls, [("group_1", "group")])
        self.assertEqual(delivered, [("group_1", "Group One", "hello", "zalo_bot")])

    async def test_personal_zalo_group_send_is_unsupported(self):
        service = _FakeService({"status": "ok"})
        self._bind(service, platforms={"zalo"})
        with mock.patch.object(channel_tools, "_request_approval") as approval:
            result = self._call(
                {"platform": "zalo", "thread_type": "group", "recipient": "111", "text": "hi"}
            )

        self.assertEqual(result["error"]["code"], "channel_group_unsupported")
        approval.assert_not_called()

    async def test_platform_that_is_not_enabled_is_refused(self):
        service = _FakeService({"status": "ok"})
        self._bind(service, platforms={"zalo"})
        with mock.patch.object(channel_tools, "_request_approval") as approval:
            result = self._call(
                {"platform": "zalo_bot", "recipient": "chat_1", "text": "hi"}
            )

        self.assertEqual(result["error"]["code"], "channel_platform_not_configured")
        approval.assert_not_called()

    async def test_bind_requires_an_enabled_profile_and_registered_plugin(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        profile = Path(temporary.name)
        (profile / "config.yaml").write_text(
            "platforms:\n  zalo:\n    enabled: true\n", encoding="utf-8"
        )
        with mock.patch.dict(
            os.environ, {"FT_ENABLE_AGENT_CHANNELS": "true"}
        ), mock.patch.object(
            zalo_channels, "zalo_plugin_registered", return_value=True
        ):
            with channel_tools.bind_run(
                _FakeService({"status": "ok"}), profile, "agent-1", "conv-1", "run-1", None
            ) as enabled:
                self.assertTrue(enabled)
                self.assertIn("conv-1", channel_tools._BINDINGS)

        self.assertNotIn("conv-1", channel_tools._BINDINGS)

    async def test_bind_records_an_enabled_bot_platform(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        profile = Path(temporary.name)
        (profile / "config.yaml").write_text(
            "platforms:\n  zalo_bot:\n    enabled: true\n", encoding="utf-8"
        )
        with mock.patch.dict(
            os.environ, {"FT_ENABLE_AGENT_CHANNELS": "true"}
        ), mock.patch.object(
            zalo_channels, "platform_plugin_registered", return_value=True
        ):
            with channel_tools.bind_run(
                _FakeService({"status": "ok"}), profile, "agent-1", "conv-1", "run-1", None
            ) as enabled:
                self.assertTrue(enabled)
                self.assertEqual(channel_tools._PLATFORMS["conv-1"], {"zalo_bot"})

        self.assertNotIn("conv-1", channel_tools._PLATFORMS)

    async def test_bind_is_disabled_by_the_feature_flag(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        profile = Path(temporary.name)
        (profile / "config.yaml").write_text(
            "platforms:\n  zalo:\n    enabled: true\n", encoding="utf-8"
        )
        with mock.patch.dict(
            os.environ, {"FT_ENABLE_AGENT_CHANNELS": "false"}
        ), mock.patch.object(
            zalo_channels, "zalo_plugin_registered", return_value=True
        ):
            with channel_tools.bind_run(
                _FakeService({"status": "ok"}), profile, "agent-1", "conv-1", "run-1", None
            ) as enabled:
                self.assertFalse(enabled)
                self.assertNotIn("conv-1", channel_tools._BINDINGS)

    async def test_install_adds_the_tool_only_for_the_bound_run(self):
        channel_tools._SCHEMA.update(
            {"type": "function", "function": {"name": "send_channel_message"}}
        )
        self._bind(_FakeService({"status": "ok"}))
        agent = SimpleNamespace(tools=[], valid_tool_names=set(), _delegate_depth=0)
        child = SimpleNamespace(tools=[], valid_tool_names=set(), _delegate_depth=1)

        channel_tools.install_tools(agent, "conv-1")
        channel_tools.install_tools(child, "conv-1")

        self.assertEqual(
            [tool["function"]["name"] for tool in agent.tools], ["send_channel_message"]
        )
        self.assertIn("send_channel_message", agent.valid_tool_names)
        self.assertEqual(child.tools, [])


if __name__ == "__main__":
    unittest.main()
