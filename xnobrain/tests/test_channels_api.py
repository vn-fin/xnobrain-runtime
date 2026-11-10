"""Agent channel product routes, feature gating, and profile writes."""

from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from xnobrain.feature_flags import FEATURE_AGENT_CHANNELS, enabled
from xnobrain.repositories.files import FileRepository
from xnobrain.routes.definition import API_PREFIX
from xnobrain.routes.setup import setup_routes
from xnobrain.services.base import ServiceError
from xnobrain.services.channels import ChannelsService


class HandlerStub:
    async def dispatch(self, request, body):  # pragma: no cover - route is not called
        raise AssertionError("disabled feature route was called")


def _read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


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


class ChannelFeatureGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_flag_removes_every_channel_route(self):
        with mock.patch.dict(
            os.environ, {"FT_ENABLE_AGENT_CHANNELS": "false"}, clear=True
        ):
            app = FastAPI()
            setup_routes(app, HandlerStub())
        self.assertFalse(enabled(FEATURE_AGENT_CHANNELS))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            listed = await client.get(f"{API_PREFIX}/agents/agent-1/channels")
            enabled_response = await client.put(
                f"{API_PREFIX}/agents/agent-1/channels/zalo", json={"enabled": True}
            )
            pairings = await client.get(
                f"{API_PREFIX}/agents/agent-1/channels/zalo/pairings"
            )
            approve = await client.post(
                f"{API_PREFIX}/agents/agent-1/channels/zalo/pairings/approve",
                json={"request_id": "0123456789abcdef"},
            )
        self.assertEqual(listed.status_code, 404)
        self.assertEqual(enabled_response.status_code, 404)
        self.assertEqual(pairings.status_code, 404)
        self.assertEqual(approve.status_code, 404)

    async def test_enabled_flag_registers_the_channel_routes(self):
        with mock.patch.dict(
            os.environ, {"FT_ENABLE_AGENT_CHANNELS": "true"}, clear=True
        ):
            app = FastAPI()
            setup_routes(app, HandlerStub())
        paths = {(route.path, tuple(sorted(route.methods))) for route in app.routes}
        for path in (
            f"{API_PREFIX}/agents/{{agent_id}}/channels",
            f"{API_PREFIX}/agents/{{agent_id}}/channels/zalo",
            f"{API_PREFIX}/agents/{{agent_id}}/channels/zalo/qr",
            f"{API_PREFIX}/agents/{{agent_id}}/channels/zalo/logout",
            f"{API_PREFIX}/agents/{{agent_id}}/channels/{{platform}}/pairings",
            f"{API_PREFIX}/agents/{{agent_id}}/channels/{{platform}}/pairings/approve",
            f"{API_PREFIX}/agents/{{agent_id}}/channels/{{platform}}/pairings/deny",
            f"{API_PREFIX}/agents/{{agent_id}}/channels/{{platform}}/pairings/revoke",
            f"{API_PREFIX}/internal/channels/messages",
        ):
            self.assertTrue(any(item[0] == path for item in paths), path)

    def test_service_fails_closed_when_disabled(self):
        with mock.patch.dict(
            os.environ, {"FT_ENABLE_AGENT_CHANNELS": "false"}, clear=True
        ):
            service = ChannelsService(SimpleNamespace(repository=None))
            with self.assertRaises(ServiceError) as raised:
                service.list_channels("agent-1")
        self.assertEqual(raised.exception.status, 404)
        self.assertEqual(raised.exception.code, "feature_disabled")


class ChannelWriteTests(unittest.IsolatedAsyncioTestCase):
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
            "      dm_policy: disabled\n",
            encoding="utf-8",
        )
        self.root.joinpath("config.yaml").write_text(
            "gateway:\n"
            "  multiplex_profiles: false\n"
            "  multiplex_profile_allowlist: []\n",
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
        self.repository = FileRepository(
            root / "data", self.profiles, root_profile=self.root
        )
        self.service = ChannelsService(_Platform(self.repository, self.root))
        self.kills: list[tuple[int, int]] = []

        def kill(pid, sig):
            self.kills.append((pid, sig))
            return None

        self.kill = mock.patch("os.kill", side_effect=kill)
        self.kill.start()
        self.addCleanup(self.kill.stop)

    def test_enable_writes_config_snapshot_and_multiplex_allowlist(self):
        state = self.service.set_channel_enabled("agent-1", True)

        self.assertTrue(state["enabled"])
        self.assertEqual(state["status"], "pending_restart")
        config = _read_yaml(self.profile / "config.yaml")
        self.assertTrue(config["platforms"]["zalo"]["enabled"])
        self.assertEqual(config["platforms"]["zalo"]["extra"]["dm_policy"], "disabled")
        root_config = _read_yaml(self.root / "config.yaml")
        self.assertTrue(root_config["gateway"]["multiplex_profiles"])
        self.assertEqual(root_config["gateway"]["multiplex_profile_allowlist"], ["agent-1"])
        self.assertTrue(
            list((self.profile / "snapshots" / "config" / "config").glob("*.yaml"))
        )
        self.assertIn((111, 10), self.kills)
        restart = self.service.repository.gateway_restart()
        self.assertEqual(restart["pid"], 111)

        self.root.joinpath("gateway.pid").write_text('{"pid": 222}', encoding="utf-8")
        with mock.patch(
            "xnobrain.integrations.zalo_channels.bridge_health", return_value=None
        ):
            listed = self.service.list_channels("agent-1")
        self.assertFalse(listed["gateway"]["pending_restart"])
        self.assertEqual(listed["gateway"]["pid"], 222)
        self.assertIsNone(self.service.repository.gateway_restart())

    def test_disable_clears_multiplex_and_never_allows_the_root_profile(self):
        self.service.set_channel_enabled("agent-1", True)
        state = self.service.set_channel_enabled("agent-1", False)

        self.assertFalse(state["enabled"])
        self.assertEqual(state["status"], "disabled")
        root_config = _read_yaml(self.root / "config.yaml")
        self.assertFalse(root_config["gateway"]["multiplex_profiles"])
        self.assertEqual(root_config["gateway"]["multiplex_profile_allowlist"], [])

    def test_root_can_own_the_channel_without_multiplex(self):
        state = self.service.set_channel_enabled("big-brother", True)

        self.assertTrue(state["enabled"])
        root_config = _read_yaml(self.root / "config.yaml")
        self.assertTrue(root_config["platforms"]["zalo"]["enabled"])
        self.assertFalse(root_config["gateway"]["multiplex_profiles"])
        self.assertEqual(root_config["gateway"]["multiplex_profile_allowlist"], [])
        self.assertEqual(self.service._bridge_port(self.root, root_config), 3110)

        self.service.set_channel_enabled("agent-1", True)
        synced = _read_yaml(self.root / "config.yaml")
        self.assertTrue(synced["gateway"]["multiplex_profiles"])
        self.assertEqual(synced["gateway"]["multiplex_profile_allowlist"], ["agent-1"])
        self.assertTrue(synced["platforms"]["zalo"]["enabled"])

    async def test_root_qr_and_logout_use_the_root_profile(self):
        self.service.set_channel_enabled("big-brother", True)
        self.root.joinpath("gateway.pid").write_text('{"pid": 222}', encoding="utf-8")
        directory = self.root / "zalo"
        directory.mkdir()
        png = b"\x89PNG\r\n\x1a\nroot"
        (directory / "qr.png").write_bytes(png)
        with mock.patch(
            "xnobrain.integrations.zalo_channels.bridge_health",
            side_effect=[
                {"status": "needs_login"},
                {"status": "qr"},
            ],
        ), mock.patch(
            "xnobrain.integrations.zalo_channels.bridge_start_login",
            return_value={"status": "connecting"},
        ):
            result = await self.service.channel_qr("big-brother")

        self.assertEqual(result["status"], "qr")
        self.assertEqual(base64.b64decode(result["image_base64"]), png)

        session = directory / "session.json"
        session.write_text("{}", encoding="utf-8")
        self.service.logout_channel("big-brother")
        self.assertFalse(session.exists())

    def test_multiplex_allowlist_excludes_an_enabled_root_profile(self):
        root_config = _read_yaml(self.root / "config.yaml")
        root_config["platforms"] = {"zalo": {"enabled": True}}
        self.root.joinpath("config.yaml").write_text(
            yaml.safe_dump(root_config, sort_keys=False), encoding="utf-8"
        )

        self.service.set_channel_enabled("agent-1", True)

        synced = _read_yaml(self.root / "config.yaml")
        self.assertEqual(synced["gateway"]["multiplex_profile_allowlist"], ["agent-1"])

    def test_patch_writes_env_and_config_atomically(self):
        self.profile.joinpath(".env").write_text(
            "UNRELATED=value\nZALO_ALLOWED_USERS=999\n", encoding="utf-8"
        )
        state = self.service.patch_channel(
            "agent-1",
            {
                "allowed_users": ["111", "222", "111"],
                "dm_policy": "allowlist",
                "home_channel": "111",
            },
        )

        self.assertEqual(state["allowed_users"], ["111", "222"])
        self.assertEqual(state["dm_policy"], "allowlist")
        self.assertEqual(state["home_channel"], "111")
        env = (self.profile / ".env").read_text(encoding="utf-8")
        self.assertIn("UNRELATED=value", env)
        self.assertIn("ZALO_ALLOWED_USERS=111,222", env)
        self.assertIn("ZALO_HOME_CHANNEL=111", env)
        config = _read_yaml(self.profile / "config.yaml")
        self.assertEqual(config["platforms"]["zalo"]["extra"]["dm_policy"], "allowlist")
        self.assertTrue(
            list((self.profile / "snapshots" / "config" / "config").glob("*.yaml"))
        )

        cleared = self.service.patch_channel(
            "agent-1", {"allowed_users": [], "home_channel": None}
        )
        self.assertEqual(cleared["allowed_users"], [])
        self.assertIsNone(cleared["home_channel"])
        env = (self.profile / ".env").read_text(encoding="utf-8")
        self.assertNotIn("ZALO_ALLOWED_USERS", env)
        self.assertNotIn("ZALO_HOME_CHANNEL", env)

    def test_patch_rejects_invalid_policy_values(self):
        with self.assertRaises(ServiceError) as raised:
            self.service.patch_channel("agent-1", {"allowed_users": ["not-an-id"]})
        self.assertEqual(raised.exception.code, "invalid_allowed_users")
        with self.assertRaises(ServiceError) as raised:
            self.service.patch_channel("agent-1", {"home_channel": "../escape"})
        self.assertEqual(raised.exception.code, "invalid_home_channel")
        with self.assertRaises(ServiceError) as raised:
            self.service.patch_channel("agent-1", {"dm_policy": "open"})
        self.assertEqual(raised.exception.code, "invalid_dm_policy")

    async def test_qr_starts_one_login_and_returns_the_png(self):
        self.service.set_channel_enabled("agent-1", True)
        self.root.joinpath("gateway.pid").write_text('{"pid": 222}', encoding="utf-8")
        directory = self.profile / "zalo"
        directory.mkdir()
        png = b"\x89PNG\r\n\x1a\nchannel"
        (directory / "qr.png").write_bytes(png)
        with mock.patch(
            "xnobrain.integrations.zalo_channels.bridge_health",
            side_effect=[
                {"status": "needs_login"},
                {"status": "qr", "ownId": "42"},
            ],
        ), mock.patch(
            "xnobrain.integrations.zalo_channels.bridge_start_login",
            return_value={"status": "connecting"},
        ) as login:
            result = await self.service.channel_qr("agent-1")

        login.assert_called_once()
        self.assertEqual(result["status"], "qr")
        self.assertEqual(base64.b64decode(result["image_base64"]), png)

    async def test_qr_does_not_login_when_the_channel_is_disabled(self):
        with self.assertRaises(ServiceError) as raised:
            await self.service.channel_qr("agent-1")
        self.assertEqual(raised.exception.code, "channel_disabled")

    def test_logout_deletes_the_session_and_restarts_the_gateway(self):
        self.service.set_channel_enabled("agent-1", True)
        session = self.profile / "zalo" / "session.json"
        session.parent.mkdir(exist_ok=True)
        session.write_text("{}", encoding="utf-8")

        state = self.service.logout_channel("agent-1")

        self.assertFalse(session.exists())
        self.assertEqual(state["status"], "pending_restart")
        self.assertIn((111, 10), self.kills)

    def test_no_live_pid_reports_gateway_down(self):
        self.service.set_channel_enabled("agent-1", True)
        self.root.joinpath("gateway.pid").write_text('{"pid": 333}', encoding="utf-8")

        with mock.patch("os.kill", side_effect=ProcessLookupError):
            listed = self.service.list_channels("agent-1")

        self.assertFalse(listed["gateway"]["running"])
        self.assertEqual(listed["channels"][0]["status"], "gateway_down")

    def test_gateway_pid_accepts_json_and_plain_integer_records(self):
        self.assertEqual(self.service.repository.gateway_pid(self.root), 111)
        self.root.joinpath("gateway.pid").write_text("444\n", encoding="utf-8")
        self.assertEqual(self.service.repository.gateway_pid(self.root), 444)

    def _write_gateway_state(self, **overrides):
        record = {
            "kind": "hermes-gateway",
            "pid": 111,
            "gateway_state": "running",
        }
        record.update(overrides)
        self.root.joinpath("gateway_state.json").write_text(
            json.dumps(record), encoding="utf-8"
        )

    @staticmethod
    def _current_start_time():
        stat = Path(f"/proc/{os.getpid()}/stat").read_text(encoding="utf-8")
        return int(stat[stat.rindex(")") + 1 :].split()[19])

    def test_gateway_pid_file_wins_over_gateway_state(self):
        self._write_gateway_state(pid=222)

        gateway = self.service._gateway_state()

        self.assertTrue(gateway["running"])
        self.assertEqual(gateway["pid"], 111)

    def test_missing_pid_file_uses_a_running_gateway_state(self):
        self.root.joinpath("gateway.pid").unlink()
        self._write_gateway_state(pid=222, gateway_state="running")

        gateway = self.service._gateway_state()

        self.assertTrue(gateway["running"])
        self.assertEqual(gateway["pid"], 222)

    def test_gateway_state_stopped_is_not_running(self):
        self.root.joinpath("gateway.pid").unlink()
        self._write_gateway_state(pid=222, gateway_state="stopped")

        gateway = self.service._gateway_state()

        self.assertFalse(gateway["running"])
        self.assertIsNone(gateway["pid"])

    def test_gateway_state_wrong_kind_is_not_running(self):
        self.root.joinpath("gateway.pid").unlink()
        self._write_gateway_state(pid=222, kind="gateway")

        gateway = self.service._gateway_state()

        self.assertFalse(gateway["running"])
        self.assertIsNone(gateway["pid"])

    def test_gateway_state_start_time_mismatch_is_not_running(self):
        pid = os.getpid()
        self.root.joinpath("gateway.pid").unlink()
        self._write_gateway_state(
            pid=pid,
            start_time=self._current_start_time() + 1,
        )

        gateway = self.service._gateway_state()

        self.assertFalse(gateway["running"])
        self.assertIsNone(gateway["pid"])

    def test_symlinked_gateway_state_is_ignored(self):
        target = self.root / "other-state.json"
        target.write_text(
            json.dumps(
                {
                    "kind": "hermes-gateway",
                    "pid": 222,
                    "gateway_state": "running",
                }
            ),
            encoding="utf-8",
        )
        self.root.joinpath("gateway.pid").unlink()
        os.symlink(target, self.root / "gateway_state.json")

        gateway = self.service._gateway_state()

        self.assertFalse(gateway["running"])
        self.assertIsNone(gateway["pid"])


if __name__ == "__main__":
    unittest.main()
