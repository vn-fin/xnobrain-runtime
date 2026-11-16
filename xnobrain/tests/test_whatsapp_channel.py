"""WhatsApp personal channel: QR API, settings, pairing, and status."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml
from pydantic import ValidationError

from xnobrain.integrations import channel_tools, hermes_support, whatsapp_channels
from xnobrain.integrations.integration_connect_tools import bind_run as bind_connect
from xnobrain.models.channels import (
    ChannelTurnRequest,
    ChannelWhatsappPatch,
)
from xnobrain.repositories.files import FileRepository
from xnobrain.services.base import ServiceError
from xnobrain.services.channels import ChannelsService
from xnobrain.services.conversation_authority import HostAuthority

PHONE = "84901234567"
PHONE_JID = f"{PHONE}@s.whatsapp.net"
LID_JID = "123456789012345@lid"
GROUP_JID = "120363025246125497@g.us"
BAD_ID = "user@example.com"
PNG = b"\x89PNG\r\n\x1a\nqr"


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


class _FakeStream:
    def __init__(self, process: "_FakeProcess", lines, block):
        self.process = process
        self.lines = list(lines)
        self.block = block
        self.closed = asyncio.Event()

    async def readline(self) -> bytes:
        if self.block:
            await self.closed.wait()
            return b""
        if not self.lines:
            self.process.exit(0)
            return b""
        delay, line = self.lines.pop(0)
        if delay:
            await asyncio.sleep(delay)
        return line


class _FakeProcess:
    def __init__(self, lines, block=False):
        self.returncode = None
        self.terminated = False
        self.killed = False
        self.exited = asyncio.Event()
        self.stdout = _FakeStream(self, lines, block)

    def terminate(self):
        self.terminated = True
        self.exit(-15)

    def kill(self):
        self.killed = True
        self.exit(-9)

    def exit(self, code=0):
        if self.returncode is None:
            self.returncode = code
        self.stdout.closed.set()
        self.exited.set()

    async def wait(self):
        await self.exited.wait()
        return self.returncode


def _line(payload: dict) -> bytes:
    return json.dumps(payload).encode("utf-8") + b"\n"


class WhatsAppChannelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.profiles = root / "profiles"
        self.root = self.profiles / "big-brother"
        self.profile = self.profiles / "agent-1"
        self.second = self.profiles / "agent-2"
        self.root.mkdir(parents=True)
        self.profile.mkdir(parents=True)
        self.second.mkdir(parents=True)
        self.profile.joinpath("config.yaml").write_text(
            "platforms:\n"
            "  zalo:\n"
            "    enabled: false\n"
            "    extra:\n"
            "      dm_policy: disabled\n"
            "  whatsapp:\n"
            "    enabled: false\n"
            "    extra:\n"
            "      dm_policy: disabled\n",
            encoding="utf-8",
        )
        self.root.joinpath("config.yaml").write_text(
            "gateway:\n  multiplex_profiles: false\n  multiplex_profile_allowlist: []\n",
            encoding="utf-8",
        )
        self.environment = mock.patch.dict(
            os.environ,
            {
                "FT_ENABLE_AGENT_CHANNELS": "true",
                "HERMES_ROOT_PROFILE": str(self.root),
                "DATA_DIR": str(root / "data"),
                "RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": "",
                "WHATSAPP_ALLOW_ALL_USERS": "",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.repository = FileRepository(root / "data", self.profiles, root_profile=self.root)
        self.service = ChannelsService(_Platform(self.repository, self.root))
        self.running = {"running": True, "pid": 111, "pending_restart": False}
        self.service._gateway_state = mock.Mock(return_value=self.running)
        self.service._restart_gateway = mock.Mock(return_value=self.running)

    def _enable(self):
        return self.service.set_whatsapp_enabled("agent-1", True)

    def _session(self, profile=None):
        profile = profile or self.profile
        return profile / "platforms" / "whatsapp" / "session"

    def _write_creds(self, profile=None):
        session = self._session(profile)
        session.mkdir(parents=True, exist_ok=True)
        session.joinpath("creds.json").write_text("{}", encoding="utf-8")
        return session

    def _record_for(self, profile=None):
        return self.service._whatsapp_pairing_record(profile or self.profile)

    async def _start_pairing(self, lines, block=False, creds=False, exit_after=0.2):
        process = _FakeProcess(lines, block=block)
        session = self._session()
        if creds:
            self._write_creds()

        async def spawn(session_dir, mode="bot"):
            self.assertEqual(mode, "bot")
            if exit_after is not None:
                asyncio.get_running_loop().call_later(exit_after, process.exit)
            return process

        self.spawn = mock.patch.object(
            whatsapp_channels, "spawn_pairing", side_effect=spawn
        )
        self.spawn.start()
        self.addCleanup(self.spawn.stop)
        record = await self.service._start_whatsapp_pairing(
            self.service._whatsapp_pairing_key(self.profile), session
        )
        self.assertIsNotNone(record)
        return process, record

    def test_enable_writes_config_and_environment(self):
        state = self._enable()

        self.assertTrue(state["enabled"])
        self.assertEqual(state["status"], "needs_login")
        self.assertIsNone(state["history_retention_days"])
        self.assertFalse(state["token_set"])
        config = _read_yaml(self.profile / "config.yaml")
        self.assertTrue(_configured_profile(config, "whatsapp")["enabled"])
        self.assertEqual(
            _configured_profile(config, "whatsapp")["extra"]["dm_policy"], "disabled"
        )
        # Port 3000 is the Runtime API in the shared gateway network namespace.
        self.assertEqual(_configured_profile(config, "whatsapp")["extra"]["bridge_port"], 3011)
        env = (self.profile / ".env").read_text(encoding="utf-8")
        self.assertIn("WHATSAPP_ENABLED=true", env)
        self.assertIn("WHATSAPP_MODE=bot", env)
        self.assertIn("WHATSAPP_DM_POLICY=disabled", env)
        root_config = _read_yaml(self.root / "config.yaml")
        self.assertEqual(root_config["gateway"]["multiplex_profile_allowlist"], ["agent-1"])

        disabled = self.service.set_whatsapp_enabled("agent-1", False)
        self.assertEqual(disabled["status"], "disabled")
        env = (self.profile / ".env").read_text(encoding="utf-8")
        self.assertIn("WHATSAPP_ENABLED=false", env)

    def test_patch_writes_allowlist_home_channel_and_policy(self):
        self._enable()

        state = self.service.patch_whatsapp(
            "agent-1",
            {
                "allowed_users": [PHONE_JID, LID_JID, PHONE_JID, GROUP_JID],
                "home_channel": GROUP_JID,
                "dm_policy": "allowlist",
            },
        )

        self.assertEqual(state["allowed_users"], [PHONE_JID, LID_JID, GROUP_JID])
        self.assertEqual(state["home_channel"], GROUP_JID)
        self.assertEqual(state["dm_policy"], "allowlist")
        env = (self.profile / ".env").read_text(encoding="utf-8")
        self.assertIn(
            f"WHATSAPP_ALLOWED_USERS={PHONE_JID},{LID_JID},{GROUP_JID}", env
        )
        self.assertIn(f"WHATSAPP_HOME_CHANNEL={GROUP_JID}", env)
        self.assertIn("WHATSAPP_DM_POLICY=allowlist", env)
        config = _read_yaml(self.profile / "config.yaml")
        self.assertEqual(
            _configured_profile(config, "whatsapp")["extra"]["dm_policy"], "allowlist"
        )

    def test_invalid_ids_are_refused(self):
        self._enable()
        for value in (BAD_ID, "bad/id", "@g.us"):
            with self.subTest(value=value):
                with self.assertRaises(ServiceError) as raised:
                    self.service.patch_whatsapp("agent-1", {"allowed_users": [value]})
                self.assertEqual(raised.exception.code, "invalid_allowed_users")
                with self.assertRaises(ServiceError) as raised:
                    self.service.patch_whatsapp("agent-1", {"home_channel": value})
                self.assertEqual(raised.exception.code, "invalid_home_channel")
        state = self.service.patch_whatsapp(
            "agent-1", {"allowed_users": [PHONE, PHONE_JID, LID_JID, GROUP_JID]}
        )
        self.assertEqual(state["allowed_users"], [PHONE, PHONE_JID, LID_JID, GROUP_JID])
        with self.assertRaises(ValidationError):
            ChannelWhatsappPatch.model_validate({"home_channel": BAD_ID})
        with self.assertRaises(ValidationError):
            ChannelWhatsappPatch.model_validate({"dm_policy": "open"})

    def test_turn_request_accepts_whatsapp_jids(self):
        for value in (PHONE, PHONE_JID, LID_JID, GROUP_JID):
            with self.subTest(value=value):
                request = ChannelTurnRequest.model_validate(
                    {
                        "platform": "whatsapp",
                        "agent_id": "agent-1",
                        "thread_id": value,
                        "thread_type": "group" if value.endswith("@g.us") else "dm",
                        "sender_id": value,
                        "text": "hello",
                        "message_id": "message-1",
                    }
                )
                self.assertEqual(request.thread_id, value)
        with self.assertRaises(ValidationError):
            ChannelTurnRequest.model_validate(
                {
                    "platform": "whatsapp",
                    "agent_id": "agent-1",
                    "thread_id": BAD_ID,
                    "thread_type": "dm",
                    "sender_id": PHONE_JID,
                    "text": "hello",
                    "message_id": "message-1",
                }
            )

    def test_list_channels_includes_whatsapp(self):
        self._enable()

        listed = self.service.list_channels("agent-1")

        platforms = [channel["platform"] for channel in listed["channels"]]
        self.assertEqual(
            platforms, ["zalo", "zalo_bot", "telegram", "discord", "slack", "whatsapp"]
        )
        whatsapp = listed["channels"][-1]
        self.assertTrue(whatsapp["enabled"])
        self.assertEqual(whatsapp["status"], "needs_login")

    async def test_status_transitions(self):
        config = self.service.repository.config(self.profile)
        disabled = self.service._whatsapp_channel_state(
            self.profile, config, self.running
        )
        self.assertEqual(disabled["status"], "disabled")

        self._enable()
        config = self.service.repository.config(self.profile)
        self.assertEqual(
            self.service._whatsapp_channel_state(self.profile, config, self.running)[
                "status"
            ],
            "needs_login",
        )
        self.assertEqual(
            self.service._whatsapp_channel_state(
                self.profile,
                config,
                {"running": True, "pid": 111, "pending_restart": True},
            )["status"],
            "pending_restart",
        )
        self.assertEqual(
            self.service._whatsapp_channel_state(
                self.profile,
                config,
                {"running": False, "pid": None, "pending_restart": False},
            )["status"],
            "gateway_down",
        )

        self._write_creds()
        self.assertEqual(
            self.service._whatsapp_channel_state(self.profile, config, self.running)[
                "status"
            ],
            "closed",
        )
        self.root.joinpath("gateway_state.json").write_text(
            json.dumps({"platforms": {"whatsapp": {"state": "connected"}}}),
            encoding="utf-8",
        )
        self.assertEqual(
            self.service._whatsapp_channel_state(self.profile, config, self.running)[
                "status"
            ],
            "connected",
        )

        result = await self.service.channel_qr_whatsapp("agent-1")
        self.assertEqual(result["status"], "connected")

    async def test_qr_returns_a_png_then_restarts_after_pairing(self):
        self._enable()
        process, _record = await self._start_pairing(
            [
                (0, _line({"event": "qr", "qr": "2@abc"})),
                (0.5, _line({"event": "connected", "user": {"id": PHONE_JID}})),
            ],
            exit_after=0.9,
        )
        asyncio.get_running_loop().call_later(0.7, self._write_creds)
        with mock.patch.object(whatsapp_channels, "render_qr_png", return_value=PNG):
            result = await self.service.channel_qr_whatsapp("agent-1")

        self.assertEqual(result["status"], "qr")
        self.assertIsNotNone(result["image_base64"])

        for _ in range(50):
            record = self._record_for()
            if record is not None and record.task is None:
                break
            await asyncio.sleep(0.05)
        self.assertTrue(process.terminated or process.returncode is not None)
        self.service._restart_gateway.assert_called()
        self.assertTrue(whatsapp_channels.has_credentials(self._session()))

    async def test_qr_timeout_kills_the_pairing_process(self):
        self._enable()
        process, _record = await self._start_pairing(
            [], block=True, exit_after=None
        )
        with mock.patch.object(
            whatsapp_channels, "PAIRING_TIMEOUT_SECONDS", 0.05
        ):
            result = await self.service.channel_qr_whatsapp("agent-1")

        self.assertEqual(result["status"], "needs_login")
        self.assertTrue(process.terminated)

    async def test_logout_deletes_only_this_profile_session(self):
        self.service.set_whatsapp_enabled("agent-1", True)
        self.service.set_whatsapp_enabled("agent-2", True)
        first = self._write_creds()
        second = self._write_creds(self.second)
        process, _record = await self._start_pairing(
            [], block=True, exit_after=None
        )

        state = await self.service.logout_whatsapp("agent-1")

        self.assertFalse(state["enabled"])
        self.assertFalse(first.exists())
        self.assertTrue(second.joinpath("creds.json").is_file())
        self.assertTrue(process.terminated)
        config = _read_yaml(self.profile / "config.yaml")
        self.assertFalse(_configured_profile(config, "whatsapp")["enabled"])
        second_config = _read_yaml(self.second / "config.yaml")
        self.assertTrue(_configured_profile(second_config, "whatsapp")["enabled"])
        env = (self.profile / ".env").read_text(encoding="utf-8")
        self.assertIn("WHATSAPP_ENABLED=false", env)

    async def test_shutdown_stops_pairing_processes(self):
        self._enable()
        process, _record = await self._start_pairing(
            [], block=True, exit_after=None
        )

        await self.service.shutdown()

        self.assertTrue(process.terminated)
        self.assertIsNone(self._record_for())

    async def test_connect_card_and_send_tool_support_whatsapp(self):
        channel_tools.register_tools(self.profile)
        channels_service = mock.Mock()
        channels_service.connect_target.return_value = Path("/tmp/profile")
        service = mock.Mock()
        authority = HostAuthority(
            subject="user-1",
            tenant_id="tenant-1",
            agent_id="agent-1",
            platform="whatsapp",
        )
        with bind_connect(
            service,
            "session-wa",
            channels_service=channels_service,
            host_authority=authority,
        ) as bound:
            self.assertTrue(bound)
            from xnobrain.integrations import integration_connect_tools

            result = json.loads(
                integration_connect_tools.call(
                    integration_connect_tools.TOOL_NAME,
                    {"kind": "channel", "platform": "whatsapp"},
                    session_id="session-wa",
                )
            )

        self.assertTrue(result["success"])
        request = result["connect_request"]
        self.assertEqual(request["platform"], "whatsapp")
        self.assertEqual(request["display_name"], "WhatsApp")
        self.assertEqual(request["auth_kind"], "qr")
        event = hermes_support._integration_connect_event(result)
        self.assertEqual(event["request"]["platform"], "whatsapp")
        self.assertIn("whatsapp", channel_tools._SCHEMA["function"]["parameters"]["properties"]["platform"]["enum"])

    def test_skill_section_documents_whatsapp(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "assets"
            / "skills"
            / "social-channels"
            / "platforms"
            / "whatsapp.md"
        )
        text = path.read_text(encoding="utf-8")

        self.assertIn('platform="whatsapp"', text)
        self.assertIn("4096", text)
        self.assertIn("MEDIA:<path>", text)
        self.assertIn("@s.whatsapp.net", text)
        self.assertIn("@g.us", text)
        self.assertIn("ban", text)


if __name__ == "__main__":
    unittest.main()
