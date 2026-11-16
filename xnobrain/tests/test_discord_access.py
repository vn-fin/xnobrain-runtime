"""Channel dm_policy open, Discord roles, and the Discord gateway access wrapper."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml
from pydantic import ValidationError

from xnobrain.integrations import gateway_cli
from xnobrain.models.channels import (
    ChannelDiscordPatch,
    ChannelSlackPatch,
    ChannelTelegramPatch,
    ChannelWhatsappPatch,
    ChannelZaloBotPatch,
    ChannelZaloPatch,
)
from xnobrain.repositories.files import FileRepository
from xnobrain.services.base import ServiceError
from xnobrain.services.channels import ChannelsService
from xnobrain.tests.test_channel_turn import _GrantSource
from xnobrain.tests.test_channel_turn import _Platform as _TurnPlatform
from xnobrain.tests.test_gateway_group_approval import (
    SEEN_ENDPOINT,
    _ClientFactory,
    _gateway_module,
    _Response,
)

TOKEN = "discord-access-test-token"
USER_ID = "444444444444444444"
ROLE_ONE = "333333333333333333"
CHANNEL_ONE = "111111111111111111"

# (service method, ALLOW_ALL env key) for every channel platform.
OPEN_PLATFORMS = (
    ("zalo", "patch_channel", "ZALO_ALLOW_ALL_USERS"),
    ("zalo_bot", "patch_zalo_bot", "ZALO_BOT_ALLOW_ALL_USERS"),
    ("telegram", "patch_telegram", "TELEGRAM_ALLOW_ALL_USERS"),
    ("discord", "patch_discord", "DISCORD_ALLOW_ALL_USERS"),
    ("slack", "patch_slack", "SLACK_ALLOW_ALL_USERS"),
    ("whatsapp", "patch_whatsapp", "WHATSAPP_ALLOW_ALL_USERS"),
)

PATCH_MODELS = (
    ChannelZaloPatch,
    ChannelZaloBotPatch,
    ChannelTelegramPatch,
    ChannelDiscordPatch,
    ChannelSlackPatch,
    ChannelWhatsappPatch,
)


class _Platform(_TurnPlatform):
    """Turn platform plus the profile metadata channel writes need."""

    def __init__(self, repository: FileRepository, profile: Path, root: Path):
        super().__init__(repository, profile)
        self.config = SimpleNamespace(root_profile=root)

    def _snapshot_agent(self, agent_id, kind, target, content):
        return self.repository.snapshot(agent_id, kind, target, content)


def _discord_adapter_module():
    class FakeDiscordAdapter:
        _xnobrain_access_wrapper = False

        def __init__(self, extra: dict | None = None):
            self.config = SimpleNamespace(extra=extra or {})
            self.original = False

        def _is_allowed_user(
            self,
            user_id,
            author=None,
            *,
            guild=None,
            is_dm=False,
            channel_ids=None,
        ):
            return self.original

    module = types.ModuleType("plugins.platforms.discord.adapter")
    module.DiscordAdapter = FakeDiscordAdapter
    return module, FakeDiscordAdapter


class DiscordAccessFixture(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.profiles = root / "profiles"
        self.root = self.profiles / "big-brother"
        self.profile = self.profiles / "agent-1"
        self.root.mkdir(parents=True)
        self.profile.mkdir(parents=True)
        self.pairing_root = mock.patch(
            "hermes_constants.get_default_hermes_root",
            return_value=self.profiles.parent,
        )
        self.pairing_root.start()
        self.addCleanup(self.pairing_root.stop)
        for target in (
            "gateway.pairing.get_default_hermes_root",
            "xnobrain.integrations.hermes_pairings.get_default_hermes_root",
        ):
            patch = mock.patch(target, return_value=self.profiles.parent)
            patch.start()
            self.addCleanup(patch.stop)
        self.profile.joinpath("config.yaml").write_text(
            yaml.safe_dump(
                {
                    "platforms": {
                        "discord": {
                            "enabled": True,
                            "extra": {"dm_policy": "pairing"},
                        },
                        "telegram": {
                            "enabled": True,
                            "extra": {"dm_policy": "pairing"},
                        },
                    }
                }
            ),
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
                "RUNTIME_INTERNAL_SERVICE_TOKEN": TOKEN,
                "HERMES_ROOT_PROFILE": str(self.root),
                "DATA_DIR": str(root / "data"),
                "RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": "",
                "DISCORD_ALLOW_ALL_USERS": "",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.repository = FileRepository(
            root / "data", self.profiles, root_profile=self.root
        )
        self.platform = _Platform(self.repository, self.profile, self.root)
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
        self.kill = mock.patch("os.kill", return_value=None)
        self.kill.start()
        self.addCleanup(self.kill.stop)

    def write_env(self, values: dict[str, str]) -> None:
        text = "".join(f"{key}={value}\n" for key, value in values.items())
        self.profile.joinpath(".env").write_text(text, encoding="utf-8")

    def env_text(self) -> str:
        path = self.profile.joinpath(".env")
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def config(self) -> dict:
        return yaml.safe_load(
            self.profile.joinpath("config.yaml").read_text(encoding="utf-8")
        ) or {}

    def set_limit(self, platform: str, limit: int, groups: dict | None = None) -> None:
        self.service.repository.write_groups(
            self.profile,
            {
                "schema_version": 1,
                "platforms": {
                    platform: {"daily_limit": limit, "groups": groups or {}}
                },
            },
        )

    def usage(self, platform: str = "discord") -> dict:
        path = self.profile / "zalo" / "group_usage.json"
        if not path.exists():
            return {}
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return (value.get("platforms") or {}).get(platform) or {}

    @staticmethod
    def body(**overrides):
        value = {
            "platform": "discord",
            "agent_id": "agent-1",
            "thread_id": USER_ID,
            "thread_type": "dm",
            "sender_id": USER_ID,
            "sender_name": "Nam",
            "text": "hello there",
            "message_id": "dc-message-1",
        }
        value.update(overrides)
        return value

    def test_every_patch_model_accepts_the_open_policy(self):
        for model in PATCH_MODELS:
            with self.subTest(model=model.__name__):
                patch = model.model_validate({"dm_policy": "open"})
                self.assertEqual(patch.dm_policy, "open")

    def test_open_writes_config_and_allow_all_env_for_every_platform(self):
        for platform, method, env_key in OPEN_PLATFORMS:
            with self.subTest(platform=platform):
                state = getattr(self.service, method)(
                    "agent-1", {"dm_policy": "open"}
                )

                extra = self.config()["platforms"][platform]["extra"]
                self.assertEqual(extra["dm_policy"], "open")
                self.assertEqual(state["dm_policy"], "open")
                self.assertIn(f"{env_key}=true", self.env_text())

                changed = getattr(self.service, method)(
                    "agent-1", {"dm_policy": "pairing"}
                )
                extra = self.config()["platforms"][platform]["extra"]
                self.assertEqual(extra["dm_policy"], "pairing")
                self.assertEqual(changed["dm_policy"], "pairing")
                self.assertNotIn(f"{env_key}=", self.env_text())

    def test_invalid_discord_roles_are_rejected(self):
        for payload in (
            {"allowed_roles": ["not-a-snowflake"]},
            {"allowed_roles": [str(index) for index in range(101)]},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    ChannelDiscordPatch.model_validate(payload)
                with self.assertRaises(ServiceError) as raised:
                    self.service.patch_discord("agent-1", payload)
                self.assertEqual(raised.exception.status, 422)

    def test_discord_roles_round_trip_in_env_and_state(self):
        state = self.service.patch_discord(
            "agent-1", {"allowed_roles": [ROLE_ONE, ROLE_ONE]}
        )

        self.assertIn(f"DISCORD_ALLOWED_ROLES={ROLE_ONE}", self.env_text())
        self.assertEqual(state["allowed_roles"], [ROLE_ONE])

        cleared = self.service.patch_discord("agent-1", {"allowed_roles": []})

        self.assertNotIn("DISCORD_ALLOWED_ROLES", self.env_text())
        self.assertEqual(cleared["allowed_roles"], [])

    async def test_open_dm_guest_is_counted_and_refused_at_the_limit(self):
        self.service.patch_discord("agent-1", {"dm_policy": "open"})
        self.set_limit("discord", 1)

        first = await self.service.run_internal_turn(self.body(), TOKEN)
        self.assertEqual(first["reply"], "channel reply")

        with self.assertRaises(ServiceError) as raised:
            await self.service.run_internal_turn(
                self.body(message_id="dc-message-2"), TOKEN
            )

        self.assertEqual(raised.exception.status, 429)
        self.assertEqual(raised.exception.code, "channel_group_limit_reached")
        self.assertEqual(len(self.platform.run_calls), 1)
        counts = self.usage()["counts"]
        self.assertEqual(counts["@dm"][USER_ID], 1)

    async def test_trusted_dm_is_never_counted(self):
        from gateway.pairing import PairingStore

        paired_user = "555555555555555555"
        store = PairingStore(profile="agent-1")
        code = store.generate_code("discord", paired_user, "Paired")
        self.assertIsNotNone(code)
        self.assertIsNotNone(store.approve_code("discord", code))
        self.write_env(
            {"DISCORD_ALLOWED_USERS": USER_ID, "DISCORD_ALLOW_ALL_USERS": "true"}
        )
        self.service.patch_discord("agent-1", {"dm_policy": "open"})
        self.set_limit("discord", 1)

        await self.service.run_internal_turn(self.body(), TOKEN)
        await self.service.run_internal_turn(
            self.body(message_id="dc-message-2"), TOKEN
        )
        await self.service.run_internal_turn(
            self.body(sender_id=paired_user, message_id="dc-message-3"), TOKEN
        )
        await self.service.run_internal_turn(
            self.body(sender_id=paired_user, message_id="dc-message-4"), TOKEN
        )

        self.assertNotIn("@dm", self.usage().get("counts") or {})

    async def test_zero_limit_is_unlimited_for_open_dm_guests(self):
        self.service.patch_discord("agent-1", {"dm_policy": "open"})
        self.set_limit("discord", 0)

        for index in range(5):
            result = await self.service.run_internal_turn(
                self.body(message_id=f"dc-message-{index}"), TOKEN
            )
            self.assertEqual(result["reply"], "channel reply")

        self.assertNotIn("@dm", self.usage().get("counts") or {})

    async def test_duplicate_message_id_is_counted_once(self):
        self.service.patch_discord("agent-1", {"dm_policy": "open"})
        self.set_limit("discord", 1)

        first = await self.service.run_internal_turn(self.body(), TOKEN)
        second = await self.service.run_internal_turn(self.body(), TOKEN)

        self.assertEqual(first["run_id"], second["run_id"])
        usage = self.usage()
        self.assertEqual(usage["counts"]["@dm"][USER_ID], 1)
        self.assertEqual(usage["message_ids"][f"@dm:{USER_ID}"], ["dc-message-1"])

        with self.assertRaises(ServiceError) as raised:
            await self.service.run_internal_turn(
                self.body(message_id="dc-message-2"), TOKEN
            )
        self.assertEqual(raised.exception.code, "channel_group_limit_reached")

    async def test_run_body_has_no_toolsets_for_any_sender(self):
        self.set_limit("discord", 20)

        self.write_env({"DISCORD_ALLOW_ALL_USERS": "true"})
        self.service.patch_discord("agent-1", {"dm_policy": "open"})
        await self.service.run_internal_turn(
            self.body(sender_id=USER_ID, message_id="dc-open"), TOKEN
        )

        self.write_env({"DISCORD_ALLOWED_USERS": USER_ID})
        await self.service.run_internal_turn(
            self.body(sender_id=USER_ID, message_id="dc-trusted"), TOKEN
        )

        self.set_limit(
            "discord",
            20,
            groups={CHANNEL_ONE: {"status": "approved", "name": "Team"}},
        )
        await self.service.run_internal_turn(
            self.body(
                sender_id="666666666666666666",
                thread_id=CHANNEL_ONE,
                thread_type="group",
                message_id="dc-group",
            ),
            TOKEN,
        )

        for _agent_id, _conversation_id, run_body in self.platform.run_calls:
            self.assertNotIn("toolsets", run_body)

    async def test_paired_sender_keeps_the_full_toolset(self):
        from gateway.pairing import PairingStore

        store = PairingStore(profile="agent-1")
        code = store.generate_code("discord", USER_ID, "Nam")
        self.assertIsNotNone(code)
        self.assertIsNotNone(store.approve_code("discord", code))

        result = await self.service.run_internal_turn(self.body(), TOKEN)

        self.assertEqual(result["reply"], "channel reply")
        run_body = self.platform.run_calls[0][2]
        self.assertNotIn("toolsets", run_body)

    async def test_approved_discord_channel_runs_with_normal_tools(self):
        self.set_limit(
            "discord",
            20,
            groups={CHANNEL_ONE: {"status": "approved", "name": "Team"}},
        )

        result = await self.service.run_internal_turn(
            self.body(
                thread_id=CHANNEL_ONE,
                thread_type="group",
                message_id="dc-group-1",
            ),
            TOKEN,
        )

        self.assertEqual(result["reply"], "channel reply")
        self.assertNotIn("toolsets", self.platform.run_calls[0][2])

    async def test_unapproved_discord_channel_is_recorded_for_approval(self):
        with self.assertRaises(ServiceError) as raised:
            await self.service.run_internal_turn(
                self.body(
                    thread_id=CHANNEL_ONE,
                    thread_type="group",
                    message_id="dc-group-2",
                ),
                TOKEN,
            )

        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(raised.exception.code, "channel_group_not_approved")
        groups = yaml.safe_load(
            (self.profile / "zalo" / "groups.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            groups["platforms"]["discord"]["groups"][CHANNEL_ONE]["status"],
            "pending",
        )

    async def test_gateway_dm_limit_notice(self):
        self.set_limit("discord", 2)
        module, runner_class = _gateway_module()
        patches = [
            mock.patch.dict(sys.modules, {"gateway.run": module}),
            mock.patch.dict(
                os.environ,
                {
                    "RUNTIME_GATEWAY_CHANNEL_DELEGATION": "true",
                    "RUNTIME_INTERNAL_SERVICE_TOKEN": TOKEN,
                    "RUNTIME_INTERNAL_URL": "http://runtime:3000",
                },
            ),
            mock.patch(
                "hermes_constants.get_hermes_home",
                return_value=SimpleNamespace(name="agent-1"),
            ),
            mock.patch(
                "hermes_constants.get_process_hermes_home", return_value=self.profile
            ),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        gateway_cli._GROUP_LIMIT_NOTICE_STATE.clear()
        response = _Response(
            429,
            {
                "success": False,
                "error": {"code": "channel_group_limit_reached"},
                "status_code": 429,
            },
        )
        factory = _ClientFactory(response=response)
        with mock.patch("httpx.AsyncClient", factory):
            gateway_cli.install_runtime_channel_delegation()
            runner = runner_class()
            source = SimpleNamespace(
                platform=SimpleNamespace(value="discord"),
                chat_id=USER_ID,
                user_id=USER_ID,
                user_name="Nam",
                chat_name="Nam",
                chat_type="dm",
                message_id="source-message",
            )
            result = await runner._run_agent_inner(
                message="hello",
                context_prompt="",
                history=[],
                source=source,
                session_id="session-1",
                event_message_id="message-1",
            )

        self.assertIn("2", result["final_response"])
        self.assertIn("bot turns", result["final_response"])
        self.assertNotIn("in this group", result["final_response"])
        self.assertNotIn("trong nhóm này", result["final_response"])
        self.assertEqual(runner_class.native_calls, [])

    def test_discord_wrapper_relaxes_only_pairing_open_dms_and_guild_calls(self):
        module, adapter_class = _discord_adapter_module()
        packages = {
            "plugins": types.ModuleType("plugins"),
            "plugins.platforms": types.ModuleType("plugins.platforms"),
            "plugins.platforms.discord": types.ModuleType(
                "plugins.platforms.discord"
            ),
        }
        for package in packages.values():
            package.__path__ = []
        packages["plugins.platforms.discord.adapter"] = module
        with mock.patch.dict(sys.modules, packages):
            gateway_cli.install_gateway_discord_access()
            gateway_cli.install_gateway_discord_access()

        pairing = adapter_class({"dm_policy": "pairing"})
        self.assertTrue(pairing._is_allowed_user(USER_ID, is_dm=True))
        allowlist = adapter_class({"dm_policy": "allowlist"})
        self.assertFalse(allowlist._is_allowed_user(USER_ID, is_dm=True))
        guild = adapter_class({"dm_policy": "allowlist"})
        self.assertTrue(
            guild._is_allowed_user(USER_ID, is_dm=False, channel_ids={CHANNEL_ONE})
        )
        self.assertFalse(guild._is_allowed_user(USER_ID, is_dm=False))
        roles = adapter_class({"dm_policy": "allowlist"})
        roles._allowed_role_ids = {"42"}
        self.assertFalse(
            roles._is_allowed_user(USER_ID, is_dm=False, channel_ids={CHANNEL_ONE})
        )
        allowed = adapter_class({"dm_policy": "allowlist"})
        allowed.original = True
        self.assertTrue(allowed._is_allowed_user(USER_ID, is_dm=True))

    def test_zalo_bot_open_wrapper_relaxes_only_the_open_policy(self):
        class FakeZaloBotAdapter:
            _xnobrain_open_access_wrapper = False

            def __init__(self, extra: dict):
                self.config = SimpleNamespace(extra=extra)

            def _passes_allowlist(self, user_id):
                return False

        module = types.ModuleType("plugins.platforms.zalo_bot.adapter")
        module.ZaloAdapter = FakeZaloBotAdapter
        packages = {
            "plugins": types.ModuleType("plugins"),
            "plugins.platforms": types.ModuleType("plugins.platforms"),
            "plugins.platforms.zalo_bot": types.ModuleType(
                "plugins.platforms.zalo_bot"
            ),
            "plugins.platforms.zalo_bot.adapter": module,
        }
        for package in packages.values():
            package.__path__ = []
        with mock.patch.dict(sys.modules, packages):
            gateway_cli.install_gateway_zalo_bot_open_access()

        self.assertTrue(
            FakeZaloBotAdapter({"dm_policy": "open"})._passes_allowlist(USER_ID)
        )
        self.assertFalse(
            FakeZaloBotAdapter({"dm_policy": "pairing"})._passes_allowlist(USER_ID)
        )

    async def test_discord_guild_source_is_reported_for_owner_approval(self):
        module, runner_class = _gateway_module()
        patches = [
            mock.patch.dict(sys.modules, {"gateway.run": module}),
            mock.patch.dict(
                os.environ,
                {
                    "RUNTIME_INTERNAL_SERVICE_TOKEN": TOKEN,
                    "RUNTIME_INTERNAL_URL": "http://runtime:3000",
                },
            ),
            mock.patch(
                "hermes_constants.get_process_hermes_home", return_value=self.profile
            ),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        gateway_cli._GROUP_APPROVED_CACHE.clear()
        gateway_cli._GROUP_SEEN_STATE.clear()
        gateway_cli.install_gateway_group_approval()
        runner = runner_class()
        source = SimpleNamespace(
            platform=SimpleNamespace(value="discord"),
            chat_id=CHANNEL_ONE,
            user_id=USER_ID,
            user_name="Nam",
            chat_name="Nam",
            chat_type="group",
            message_id="source-message",
        )
        factory = _ClientFactory()

        with mock.patch("httpx.AsyncClient", factory):
            self.assertFalse(runner._is_user_authorized(source))
            await asyncio.sleep(0.05)

        self.assertEqual(len(factory.calls), 1)
        call = factory.calls[0]
        self.assertEqual(call["url"], SEEN_ENDPOINT)
        self.assertEqual(call["json"]["platform"], "discord")
        self.assertEqual(call["json"]["agent_id"], "agent-1")
        self.assertEqual(call["json"]["group_id"], CHANNEL_ONE)


if __name__ == "__main__":
    unittest.main()
