"""Discord channel: token API, connect card, turns, send path, and skill."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from xnobrain.handlers.api import APIHandlers
from xnobrain.handlers.extension_validation import _REDACTED_OPERATION_PREFIXES
from xnobrain.integrations import channel_tools, gateway_cli, hermes_support, zalo_channels
from xnobrain.integrations.integration_connect_tools import bind_run as bind_connect
from xnobrain.models.channels import ChannelDiscordPatch, ChannelTurnRequest
from xnobrain.repositories.files import FileRepository
from xnobrain.routes.definition import API_PREFIX
from xnobrain.routes.setup import setup_routes
from xnobrain.services.base import ServiceError
from xnobrain.services.channels import ChannelsService
from xnobrain.services.conversation_authority import HostAuthority
from xnobrain.tests.test_channel_turn import _GrantSource
from xnobrain.tests.test_channel_turn import _Platform as TurnPlatform

TOKEN = "MTIzNDU2Nzg5MDEyMzQ1Njc4.GaBcDe." + "a" * 40
SECOND_TOKEN = "OTg3NjU0MzIxMDk4NzY1NDMy.GhIjKl." + "b" * 40
BAD_TOKEN = "not a valid discord token!"


def _read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _configured_profile(config: dict, platform: str) -> dict:
    platforms = config.get("platforms")
    return platforms.get(platform, {}) if isinstance(platforms, dict) else {}


class _Platform:
    def __init__(self, repository: FileRepository, root: Path):
        self.repository = repository
        self.root = root
        self.config = SimpleNamespace(root_profile=root)
        self.agents = SimpleNamespace()

    def _conversation_profile(self, agent_id: str) -> Path:
        if agent_id == "big-brother":
            return self.root
        profile = self.repository.profile_path(agent_id)
        if not profile.is_dir():
            raise ServiceError("agent profile not found", status=404, code="not_found")
        return profile

    def _snapshot_agent(self, agent_id, kind, target, content):
        return self.repository.snapshot(agent_id, kind, target, content)


class DiscordChannelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.profiles = root / "profiles"
        self.root = self.profiles / "big-brother"
        self.profile = self.profiles / "agent-1"
        self.root.mkdir(parents=True)
        self.profile.mkdir(parents=True)
        self.profile.joinpath("config.yaml").write_text(
            "platforms:\n"
            "  zalo:\n"
            "    enabled: false\n"
            "    extra:\n"
            "      dm_policy: disabled\n"
            "  zalo_bot:\n"
            "    enabled: false\n"
            "    extra:\n"
            "      dm_policy: disabled\n"
            "  discord:\n"
            "    enabled: false\n"
            "    extra:\n"
            "      dm_policy: disabled\n",
            encoding="utf-8",
        )
        self.root.joinpath("config.yaml").write_text(
            "gateway:\n  multiplex_profiles: false\n  multiplex_profile_allowlist: []\n",
            encoding="utf-8",
        )
        self.root.joinpath("gateway.pid").write_text(
            '{"pid": 111, "kind": "gateway"}', encoding="utf-8"
        )
        self.environment = mock.patch.dict(
            os.environ,
            {
                "FT_ENABLE_AGENT_CHANNELS": "true",
                "HERMES_ROOT_PROFILE": str(self.root),
                "DATA_DIR": str(root / "data"),
                "RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": "",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.repository = FileRepository(root / "data", self.profiles, root_profile=self.root)
        self.service = ChannelsService(_Platform(self.repository, self.root))
        self.kills: list[tuple[int, int]] = []

        def kill(pid, sig):
            self.kills.append((pid, sig))
            return None

        self.kill = mock.patch("os.kill", side_effect=kill)
        self.kill.start()
        self.addCleanup(self.kill.stop)

    def test_enable_without_token_reports_needs_token(self):
        state = self.service.set_discord_enabled("agent-1", True)

        self.assertTrue(state["enabled"])
        self.assertFalse(state["token_set"])
        self.assertEqual(state["status"], "needs_token")
        config = _read_yaml(self.profile / "config.yaml")
        self.assertTrue(_configured_profile(config, "discord")["enabled"])
        self.assertEqual(
            _configured_profile(config, "discord")["extra"]["dm_policy"], "disabled"
        )
        root_config = _read_yaml(self.root / "config.yaml")
        self.assertTrue(root_config["gateway"]["multiplex_profiles"])
        self.assertEqual(
            root_config["gateway"]["multiplex_profile_allowlist"], ["agent-1"]
        )
        self.assertIn((111, 10), self.kills)

        disabled = self.service.set_discord_enabled("agent-1", False)
        self.assertEqual(disabled["status"], "disabled")
        root_config = _read_yaml(self.root / "config.yaml")
        self.assertFalse(root_config["gateway"]["multiplex_profiles"])
        self.assertEqual(root_config["gateway"]["multiplex_profile_allowlist"], [])

    def test_token_and_settings_are_written_to_the_profile(self):
        self.service.set_discord_enabled("agent-1", True)

        state = self.service.patch_discord(
            "agent-1",
            {
                "bot_token": TOKEN,
                "allowed_users": ["123456789012345678", "987654321098765432", "123456789012345678"],
                "home_channel": "987654321098765432",
                "dm_policy": "allowlist",
            },
        )

        self.assertTrue(state["token_set"])
        self.assertFalse(state["app_token_set"])
        self.assertEqual(state["status"], "pending_restart")
        self.assertEqual(
            state["allowed_users"], ["123456789012345678", "987654321098765432"]
        )
        self.assertEqual(state["home_channel"], "987654321098765432")
        self.assertEqual(state["dm_policy"], "allowlist")
        self.assertIsNone(state["history_retention_days"])
        env = (self.profile / ".env").read_text(encoding="utf-8")
        self.assertIn(f"DISCORD_BOT_TOKEN={TOKEN}", env)
        self.assertIn("DISCORD_ALLOWED_USERS=123456789012345678,987654321098765432", env)
        self.assertIn("DISCORD_HOME_CHANNEL=987654321098765432", env)
        config = _read_yaml(self.profile / "config.yaml")
        extra = _configured_profile(config, "discord")["extra"]
        self.assertEqual(extra["dm_policy"], "allowlist")

    def test_status_follows_the_gateway_platform_state(self):
        self.service.set_discord_enabled("agent-1", True)
        self.service.patch_discord("agent-1", {"bot_token": TOKEN})
        running = {"running": True, "pid": 111, "pending_restart": False}
        state_file = self.root / "gateway_state.json"

        with mock.patch.object(self.service, "_gateway_state", return_value=running):
            state_file.write_text(
                '{"platforms": {"discord": {"state": "connected"}}}', encoding="utf-8"
            )
            self.assertEqual(
                self.service.list_channels("agent-1")["channels"][3]["status"], "connected"
            )
            state_file.write_text(
                '{"platforms": {"discord": {"state": "retrying"}}}', encoding="utf-8"
            )
            self.assertEqual(
                self.service.list_channels("agent-1")["channels"][3]["status"], "configured"
            )

    def test_token_is_write_only_and_absent_from_logs_and_snapshots(self):
        self.service.set_discord_enabled("agent-1", True)
        records: list[str] = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        logger = logging.getLogger()
        handler = Capture(level=logging.DEBUG)
        previous_level = logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        try:
            state = self.service.patch_discord(
                "agent-1", {"bot_token": TOKEN, "dm_policy": "allowlist"}
            )
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous_level)

        self.assertTrue(state["token_set"])
        self.assertNotIn(TOKEN, json.dumps(state))
        self.assertNotIn(TOKEN, repr(state))
        self.assertNotIn(TOKEN, "\n".join(records))
        for snapshot in (self.profile / "snapshots" / "config" / "config").glob("*.yaml"):
            self.assertNotIn(TOKEN.encode(), snapshot.read_bytes())
        listed = self.service.list_channels("agent-1")
        self.assertNotIn(TOKEN, json.dumps(listed))

    def test_token_can_be_cleared_with_null(self):
        self.service.set_discord_enabled("agent-1", True)
        self.service.patch_discord("agent-1", {"bot_token": TOKEN})

        state = self.service.patch_discord("agent-1", {"bot_token": None})

        self.assertFalse(state["token_set"])
        self.assertEqual(state["status"], "needs_token")
        env = (self.profile / ".env").read_text(encoding="utf-8")
        self.assertNotIn("DISCORD_BOT_TOKEN", env)

    def test_token_used_by_another_agent_is_rejected(self):
        second = self.profiles / "agent-2"
        second.mkdir()
        self.service.patch_discord("agent-1", {"bot_token": TOKEN})

        with self.assertRaises(ServiceError) as raised:
            self.service.patch_discord("agent-2", {"bot_token": TOKEN})

        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(raised.exception.code, "bot_token_in_use")
        self.assertNotIn(TOKEN, str(raised.exception))
        self.assertIsNone(self.service.repository.env(second).get("DISCORD_BOT_TOKEN"))

    def test_invalid_allowed_users_and_home_channel_are_refused(self):
        with self.assertRaises(ServiceError) as raised:
            self.service.patch_discord("agent-1", {"allowed_users": ["C0123ABCDEF"]})
        self.assertEqual(raised.exception.code, "invalid_allowed_users")
        with self.assertRaises(ServiceError) as raised:
            self.service.patch_discord("agent-1", {"home_channel": "../escape"})
        self.assertEqual(raised.exception.code, "invalid_home_channel")
        self.assertEqual(
            ChannelDiscordPatch.model_validate({"dm_policy": "open"}).dm_policy,
            "open",
        )

    def test_logout_clears_token_and_disables_the_channel(self):
        self.service.set_discord_enabled("agent-1", True)
        self.service.patch_discord("agent-1", {"bot_token": TOKEN})

        state = self.service.logout_discord("agent-1")

        self.assertFalse(state["enabled"])
        self.assertFalse(state["token_set"])
        self.assertEqual(state["status"], "disabled")
        env = (self.profile / ".env").read_text(encoding="utf-8")
        self.assertNotIn("DISCORD_BOT_TOKEN", env)
        config = _read_yaml(self.profile / "config.yaml")
        self.assertFalse(_configured_profile(config, "discord")["enabled"])
        root_config = _read_yaml(self.root / "config.yaml")
        self.assertEqual(root_config["gateway"]["multiplex_profile_allowlist"], [])

        repeated = self.service.logout_discord("agent-1")
        self.assertFalse(repeated["enabled"])
        self.assertEqual(repeated["status"], "disabled")

    def test_list_channels_includes_discord(self):
        self.service.set_discord_enabled("agent-1", True)

        listed = self.service.list_channels("agent-1")

        self.assertEqual(
            [channel["platform"] for channel in listed["channels"]],
            ["zalo", "zalo_bot", "telegram", "discord", "slack", "whatsapp"],
        )
        discord = listed["channels"][3]
        self.assertTrue(discord["enabled"])
        self.assertEqual(discord["status"], "needs_token")
        self.assertFalse(discord["token_set"])

    async def test_invalid_token_returns_422_without_echoing_it(self):
        app = FastAPI()
        setup_routes(app, APIHandlers(mock.Mock(channels=self.service)))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.patch(
                f"{API_PREFIX}/agents/agent-1/channels/discord",
                json={"bot_token": BAD_TOKEN, "dm_policy": "allowlist"},
            )
            enabled_response = await client.put(
                f"{API_PREFIX}/agents/agent-1/channels/discord",
                json={"enabled": True},
            )
            valid_response = await client.patch(
                f"{API_PREFIX}/agents/agent-1/channels/discord",
                json={"bot_token": TOKEN},
            )

        self.assertEqual(response.status_code, 422)
        self.assertNotIn(BAD_TOKEN, response.text)
        self.assertNotIn(TOKEN, valid_response.text)
        self.assertEqual(enabled_response.status_code, 200)
        self.assertEqual(valid_response.status_code, 200)
        self.assertTrue(valid_response.json()["data"]["token_set"])
        self.assertIn("channels_discord_", _REDACTED_OPERATION_PREFIXES)

    def test_turn_request_accepts_snowflake_ids(self):
        group = ChannelTurnRequest.model_validate(
            {
                "platform": "discord",
                "agent_id": "agent-1",
                "thread_id": "987654321098765432",
                "thread_type": "group",
                "sender_id": "123456789012345678",
                "text": "hello",
                "message_id": "message-1",
            }
        )
        self.assertEqual(group.thread_id, "987654321098765432")
        with self.assertRaises(ValidationError):
            ChannelTurnRequest.model_validate(
                {
                    "platform": "discord",
                    "agent_id": "agent-1",
                    "thread_id": "channel/../../escape",
                    "thread_type": "group",
                    "sender_id": "123456789012345678",
                    "text": "hello",
                    "message_id": "message-1",
                }
            )


class DiscordConnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_connect_card_supports_discord(self):
        channels_service = mock.Mock()
        channels_service.connect_target.return_value = Path("/tmp/profile")
        service = mock.Mock()
        authority = HostAuthority(
            subject="user-1",
            tenant_id="tenant-1",
            agent_id="agent-1",
            platform="discord",
        )
        with bind_connect(
            service,
            "session-dc",
            channels_service=channels_service,
            host_authority=authority,
        ) as bound:
            self.assertTrue(bound)
            from xnobrain.integrations import integration_connect_tools

            result = json.loads(
                integration_connect_tools.call(
                    integration_connect_tools.TOOL_NAME,
                    {"kind": "channel", "platform": "discord"},
                    session_id="session-dc",
                )
            )

        self.assertTrue(result["success"])
        request = result["connect_request"]
        self.assertEqual(request["platform"], "discord")
        self.assertEqual(request["display_name"], "Discord")
        self.assertEqual(request["auth_kind"], "api_key")
        channels_service.connect_target.assert_called_once()
        event = hermes_support._integration_connect_event(result)
        self.assertEqual(event["event"], "integration.connect_requested")
        self.assertEqual(event["request"]["platform"], "discord")


class DiscordSendPathTests(unittest.IsolatedAsyncioTestCase):
    class _PlatformKey:
        def __init__(self, value):
            self.value = value

    async def test_send_uses_the_plugin_standalone_sender(self):
        platform = self._PlatformKey("discord")
        pconfig = SimpleNamespace(enabled=True, extra={})
        sender = mock.AsyncMock(return_value={"success": True, "message_id": "42"})
        entry = SimpleNamespace(max_message_length=2000, standalone_sender_fn=sender)
        config = SimpleNamespace(platforms={platform: pconfig})
        with (
            mock.patch("tools.send_message_tool.prepare_send_message_platforms"),
            mock.patch(
                "gateway.platform_registry.platform_registry.get", return_value=entry
            ),
            mock.patch("gateway.config.Platform", return_value=platform),
            mock.patch("gateway.config.load_gateway_config", return_value=config),
        ):
            result = await zalo_channels.send_text(
                "123456789012345678", "hello", platform="discord"
            )

        self.assertEqual(result, {"message_id": "42"})
        sender.assert_awaited_once_with(pconfig, "123456789012345678", "hello")

    async def test_send_rejects_an_invalid_discord_chat_id(self):
        with self.assertRaises(zalo_channels.ChannelDeliveryError) as raised:
            await zalo_channels.send_text("C0123ABCDEF", "hello", platform="discord")
        self.assertEqual(raised.exception.code, "channel_recipient_invalid")


class DiscordBindRunTests(unittest.IsolatedAsyncioTestCase):
    async def test_bind_run_reports_discord_when_enabled(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        profile = Path(temporary.name)
        (profile / "config.yaml").write_text(
            "platforms:\n  discord:\n    enabled: true\n", encoding="utf-8"
        )
        with mock.patch.dict(
            os.environ, {"FT_ENABLE_AGENT_CHANNELS": "true"}
        ), mock.patch.object(
            zalo_channels,
            "platform_plugin_registered",
            side_effect=lambda value: value == "discord",
        ):
            with channel_tools.bind_run(
                object(), profile, "agent-1", "conv-dc", "run-dc", None
            ) as enabled:
                self.assertTrue(enabled)
                self.assertEqual(
                    channel_tools._PLATFORMS["conv-dc"], frozenset({"discord"})
                )

        self.assertNotIn("conv-dc", channel_tools._PLATFORMS)


class DiscordTurnTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.profiles = root / "profiles"
        self.profile = self.profiles / "agent-1"
        self.profile.mkdir(parents=True)
        self.profile.joinpath("config.yaml").write_text(
            yaml.safe_dump(
                {
                    "platforms": {
                        "discord": {
                            "enabled": True,
                            "extra": {"dm_policy": "pairing"},
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        self.environment = mock.patch.dict(
            os.environ,
            {
                "FT_ENABLE_AGENT_CHANNELS": "true",
                "RUNTIME_INTERNAL_SERVICE_TOKEN": "channel-turn-test-token",
                "HERMES_ROOT_PROFILE": str(root / "root"),
                "DATA_DIR": str(root / "data"),
                "RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": "",
                "DISCORD_ALLOW_ALL_USERS": "",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.repository = FileRepository(root / "data", self.profiles)
        self.platform = TurnPlatform(self.repository, self.profile)
        self.service = ChannelsService(self.platform)
        self.service.repository.write_owner(
            self.profile,
            {
                "user_id": "user-1",
                "tenant_id": "personal-user-1",
                "organization_id": "",
                "agent_id": "agent-1",
                "bound_at": 1,
            },
        )
        self.service.mcp_grants = _GrantSource()

    @staticmethod
    def _body(**overrides):
        value = {
            "platform": "discord",
            "agent_id": "agent-1",
            "thread_id": "123456789012345678",
            "thread_type": "dm",
            "sender_id": "123456789012345678",
            "sender_name": "Nam",
            "text": "hello there",
            "message_id": "dc-message-1",
        }
        value.update(overrides)
        return value

    async def test_dm_turn_uses_the_agent_default_toolsets(self):
        result = await self.service.run_internal_turn(
            self._body(), "channel-turn-test-token"
        )

        self.assertEqual(result["reply"], "channel reply")
        self.assertEqual(len(self.platform.run_calls), 1)
        _, conversation_id, body = self.platform.run_calls[0]
        self.assertNotIn("toolsets", body)
        threads = self.service.repository.threads(self.profile)
        self.assertIn("discord", threads)
        self.assertEqual(
            threads["discord"]["123456789012345678"]["conversation_id"], conversation_id
        )

    async def test_group_turn_with_a_channel_id_requires_approval(self):
        with self.assertRaises(ServiceError) as raised:
            await self.service.run_internal_turn(
                self._body(
                    thread_id="987654321098765432",
                    thread_type="group",
                    message_id="dc-group-1",
                ),
                "channel-turn-test-token",
            )

        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(raised.exception.code, "channel_group_not_approved")
        self.assertEqual(self.platform.run_calls, [])
        groups = json.loads((self.profile / "zalo" / "groups.json").read_text())
        self.assertEqual(
            groups["platforms"]["discord"]["groups"]["987654321098765432"]["status"],
            "pending",
        )

    async def test_group_turn_is_marked_group_with_a_channel_id(self):
        self.service.repository.write_groups(
            self.profile,
            {
                "schema_version": 1,
                "platforms": {
                    "discord": {
                        "groups": {
                            "987654321098765432": {
                                "status": "approved",
                                "name": "Team",
                            }
                        }
                    }
                },
            },
        )

        result = await self.service.run_internal_turn(
            self._body(
                thread_id="987654321098765432",
                thread_type="group",
                message_id="dc-group-2",
            ),
            "channel-turn-test-token",
        )

        self.assertEqual(result["reply"], "channel reply")
        threads = self.service.repository.threads(self.profile)
        self.assertIn("987654321098765432", threads["discord"])
        self.assertIn("group", threads["discord"]["987654321098765432"]["title"])


class DiscordDelegationTests(unittest.TestCase):
    @staticmethod
    def _source(chat_id: str, chat_type: str):
        return SimpleNamespace(
            platform=SimpleNamespace(value="discord"),
            chat_id=chat_id,
            user_id="123456789012345678",
            user_name="Nam",
            chat_name="Nam",
            chat_type=chat_type,
            message_id="source-message",
        )

    def test_dm_payload_uses_the_user_id(self):
        with mock.patch(
            "hermes_constants.get_hermes_home",
            return_value=SimpleNamespace(name="agent-1"),
        ):
            payload = gateway_cli._delegation_payload(
                "hello", [], self._source("123456789012345678", "dm"), "message-1"
            )

        self.assertEqual(payload["platform"], "discord")
        self.assertEqual(payload["thread_type"], "dm")
        self.assertEqual(payload["thread_id"], "123456789012345678")

    def test_group_and_thread_payloads_keep_the_channel_id(self):
        for chat_type in ("group", "thread"):
            with self.subTest(chat_type=chat_type):
                with mock.patch(
                    "hermes_constants.get_hermes_home",
                    return_value=SimpleNamespace(name="agent-1"),
                ):
                    payload = gateway_cli._delegation_payload(
                        "hello",
                        [],
                        self._source("987654321098765432", chat_type),
                        "message-2",
                    )

                self.assertEqual(payload["platform"], "discord")
                self.assertEqual(payload["thread_type"], "group")
                self.assertEqual(payload["thread_id"], "987654321098765432")

    def test_channels_and_threads_are_approved_per_server(self):
        thread = self._source("111111111111111111", "thread")
        thread.parent_chat_id = "987654321098765432"
        thread.guild_id = "555555555555555555"
        channel = self._source("987654321098765432", "group")
        channel.guild_id = "555555555555555555"
        with mock.patch(
            "hermes_constants.get_hermes_home",
            return_value=SimpleNamespace(name="agent-1"),
        ):
            payload = gateway_cli._delegation_payload("hello", [], thread, "message-3")

        self.assertEqual(payload["thread_id"], "555555555555555555")
        self.assertEqual(gateway_cli._group_id(channel), "555555555555555555")
        thread.guild_id = None
        self.assertEqual(gateway_cli._group_id(thread), "987654321098765432")


class DiscordSkillTests(unittest.TestCase):
    def test_skill_section_documents_discord(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "assets"
            / "skills"
            / "social-channels"
            / "platforms"
            / "discord.md"
        )
        text = path.read_text(encoding="utf-8")

        self.assertIn('platform="discord"', text)
        self.assertIn("2000", text)
        self.assertIn("MEDIA:<path>", text)
        self.assertIn("Message Content", text)
        self.assertIn("Server Members", text)


if __name__ == "__main__":
    unittest.main()
