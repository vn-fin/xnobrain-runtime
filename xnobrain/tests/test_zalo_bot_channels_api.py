"""Zalo Bot channel product routes, write-only token, and multiplex rules."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from xnobrain.handlers.api import APIHandlers
from xnobrain.models.channels import ChannelZaloBotPatch
from xnobrain.repositories.files import FileRepository
from xnobrain.routes.definition import API_PREFIX
from xnobrain.routes.setup import setup_routes
from xnobrain.services.base import ServiceError
from xnobrain.services.channels import ChannelsService

TOKEN = "123456789012345:AbCdEf_-1234567890"
SECOND_TOKEN = "987654321098765:ZyXwVu_-0987654321"
BAD_TOKEN = "not-a-valid-token-value"


class HandlerStub:
    async def dispatch(self, request, body):  # pragma: no cover - route is not called
        raise AssertionError("disabled feature route was called")


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


class ZaloBotFeatureGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_flag_removes_bot_channel_routes(self):
        with mock.patch.dict(os.environ, {"FT_ENABLE_AGENT_CHANNELS": "false"}, clear=True):
            app = FastAPI()
            setup_routes(app, HandlerStub())
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            updated = await client.put(
                f"{API_PREFIX}/agents/agent-1/channels/zalo_bot",
                json={"enabled": True},
            )
            patched = await client.patch(
                f"{API_PREFIX}/agents/agent-1/channels/zalo_bot",
                json={"bot_token": TOKEN},
            )
        self.assertEqual(updated.status_code, 404)
        self.assertEqual(patched.status_code, 404)

    async def test_enabled_flag_registers_bot_channel_routes(self):
        with mock.patch.dict(os.environ, {"FT_ENABLE_AGENT_CHANNELS": "true"}, clear=True):
            app = FastAPI()
            setup_routes(app, HandlerStub())
        paths = {(route.path, tuple(sorted(route.methods))) for route in app.routes}
        for method, path in (
            ("PUT", f"{API_PREFIX}/agents/{{agent_id}}/channels/zalo_bot"),
            ("PATCH", f"{API_PREFIX}/agents/{{agent_id}}/channels/zalo_bot"),
            ("POST", f"{API_PREFIX}/agents/{{agent_id}}/channels/zalo_bot/logout"),
        ):
            self.assertTrue(any(item[0] == path and method in item[1] for item in paths), path)

    def test_service_fails_closed_when_disabled(self):
        with mock.patch.dict(os.environ, {"FT_ENABLE_AGENT_CHANNELS": "false"}, clear=True):
            service = ChannelsService(SimpleNamespace(repository=None))
            with self.assertRaises(ServiceError) as raised:
                service.patch_zalo_bot("agent-1", {"dm_policy": "allowlist"})
        self.assertEqual(raised.exception.status, 404)
        self.assertEqual(raised.exception.code, "feature_disabled")


class ZaloBotChannelTests(unittest.IsolatedAsyncioTestCase):
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

    def _enable_bot(self, profile: Path, token: str | None = TOKEN) -> None:
        path = profile / "config.yaml"
        config = _read_yaml(path) if path.exists() else {}
        platforms = config.setdefault("platforms", {})
        bot = platforms.setdefault("zalo_bot", {})
        bot["enabled"] = True
        bot.setdefault("extra", {}).setdefault("dm_policy", "disabled")
        path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        env = profile / ".env"
        lines = []
        if env.exists():
            lines = [
                line
                for line in env.read_text(encoding="utf-8").splitlines()
                if not line.startswith("ZALO_BOT_TOKEN=")
            ]
        if token is not None:
            lines.append(f"ZALO_BOT_TOKEN={token}")
        env.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    @staticmethod
    def _write_bot_state(
        profile: Path,
        state: str = "connected",
        updated_at: float | None = None,
        pid: int = 4321,
    ) -> Path:
        path = profile / "zalo_bot_state.json"
        path.write_text(
            json.dumps(
                {
                    "state": state,
                    "updated_at": time.time() if updated_at is None else updated_at,
                    "pid": pid,
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_enable_without_token_reports_needs_token(self):
        state = self.service.set_zalo_bot_enabled("agent-1", True)

        self.assertTrue(state["enabled"])
        self.assertFalse(state["token_set"])
        self.assertEqual(state["status"], "needs_token")
        config = _read_yaml(self.profile / "config.yaml")
        self.assertTrue(_configured_profile(config, "zalo_bot")["enabled"])
        self.assertEqual(_configured_profile(config, "zalo_bot")["extra"]["dm_policy"], "disabled")
        root_config = _read_yaml(self.root / "config.yaml")
        self.assertTrue(root_config["gateway"]["multiplex_profiles"])
        self.assertEqual(root_config["gateway"]["multiplex_profile_allowlist"], ["agent-1"])
        self.assertIn((111, 10), self.kills)

        disabled = self.service.set_zalo_bot_enabled("agent-1", False)
        self.assertEqual(disabled["status"], "disabled")
        root_config = _read_yaml(self.root / "config.yaml")
        self.assertFalse(root_config["gateway"]["multiplex_profiles"])
        self.assertEqual(root_config["gateway"]["multiplex_profile_allowlist"], [])

    def test_token_is_write_only_and_absent_from_logs_and_snapshots(self):
        self.service.set_zalo_bot_enabled("agent-1", True)
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
            state = self.service.patch_zalo_bot(
                "agent-1", {"bot_token": TOKEN, "dm_policy": "allowlist"}
            )
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous_level)

        self.assertTrue(state["token_set"])
        self.assertEqual(state["status"], "pending_restart")
        self.assertNotIn(TOKEN, json.dumps(state))
        self.assertNotIn(TOKEN, repr(state))
        self.assertNotIn(TOKEN, "\n".join(records))
        for snapshot in (self.profile / "snapshots" / "config" / "config").glob("*.yaml"):
            self.assertNotIn(TOKEN.encode(), snapshot.read_bytes())
        env = (self.profile / ".env").read_text(encoding="utf-8")
        self.assertIn(f"ZALO_BOT_TOKEN={TOKEN}", env)

        listed = self.service.list_channels("agent-1")
        self.assertNotIn(TOKEN, json.dumps(listed))

    def test_token_can_be_cleared_with_null(self):
        self.service.set_zalo_bot_enabled("agent-1", True)
        self.service.patch_zalo_bot("agent-1", {"bot_token": TOKEN})

        state = self.service.patch_zalo_bot("agent-1", {"bot_token": None})

        self.assertFalse(state["token_set"])
        self.assertEqual(state["status"], "needs_token")
        env = (self.profile / ".env").read_text(encoding="utf-8")
        self.assertNotIn("ZALO_BOT_TOKEN", env)

    def test_token_used_by_another_agent_is_rejected(self):
        second = self.profiles / "agent-2"
        second.mkdir()
        self.service.patch_zalo_bot("agent-1", {"bot_token": TOKEN})

        with self.assertRaises(ServiceError) as raised:
            self.service.patch_zalo_bot("agent-2", {"bot_token": TOKEN})

        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(raised.exception.code, "bot_token_in_use")
        self.assertNotIn(TOKEN, str(raised.exception))
        self.assertIsNone(self.service.repository.env(second).get("ZALO_BOT_TOKEN"))

    def test_same_agent_may_resubmit_its_own_token(self):
        self.service.patch_zalo_bot("agent-1", {"bot_token": TOKEN})

        state = self.service.patch_zalo_bot("agent-1", {"bot_token": TOKEN})

        self.assertTrue(state["token_set"])
        env = (self.profile / ".env").read_text(encoding="utf-8")
        self.assertIn(f"ZALO_BOT_TOKEN={TOKEN}", env)

    def test_token_on_the_root_profile_blocks_another_agent(self):
        self.service.patch_zalo_bot("big-brother", {"bot_token": TOKEN})

        with self.assertRaises(ServiceError) as raised:
            self.service.patch_zalo_bot("agent-1", {"bot_token": TOKEN})

        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(raised.exception.code, "bot_token_in_use")
        self.assertIsNone(self.service.repository.env(self.profile).get("ZALO_BOT_TOKEN"))

    def test_logout_clears_token_and_disables_the_channel(self):
        self.service.set_zalo_bot_enabled("agent-1", True)
        self.service.patch_zalo_bot("agent-1", {"bot_token": TOKEN})

        state = self.service.logout_zalo_bot("agent-1")

        self.assertFalse(state["enabled"])
        self.assertFalse(state["token_set"])
        self.assertEqual(state["status"], "disabled")
        env = (self.profile / ".env").read_text(encoding="utf-8")
        self.assertNotIn("ZALO_BOT_TOKEN", env)
        config = _read_yaml(self.profile / "config.yaml")
        self.assertFalse(_configured_profile(config, "zalo_bot")["enabled"])
        root_config = _read_yaml(self.root / "config.yaml")
        self.assertEqual(root_config["gateway"]["multiplex_profile_allowlist"], [])

        repeated = self.service.logout_zalo_bot("agent-1")
        self.assertFalse(repeated["enabled"])
        self.assertEqual(repeated["status"], "disabled")

    def test_allowed_users_and_home_channel_accept_bot_ids(self):
        state = self.service.patch_zalo_bot(
            "agent-1",
            {
                "allowed_users": ["U_1", "bot-2", "user3", "U_1"],
                "home_channel": "U_1",
            },
        )

        self.assertEqual(state["allowed_users"], ["U_1", "bot-2", "user3"])
        self.assertEqual(state["home_channel"], "U_1")
        env = (self.profile / ".env").read_text(encoding="utf-8")
        self.assertIn("ZALO_BOT_ALLOWED_USERS=U_1,bot-2,user3", env)
        self.assertIn("ZALO_BOT_HOME_CHANNEL=U_1", env)

        with self.assertRaises(ServiceError) as raised:
            self.service.patch_zalo_bot("agent-1", {"allowed_users": ["bad id!"]})
        self.assertEqual(raised.exception.code, "invalid_allowed_users")
        with self.assertRaises(ServiceError) as raised:
            self.service.patch_zalo_bot("agent-1", {"home_channel": "../escape"})
        self.assertEqual(raised.exception.code, "invalid_home_channel")

        cleared = self.service.patch_zalo_bot(
            "agent-1", {"allowed_users": [], "home_channel": None}
        )
        self.assertEqual(cleared["allowed_users"], [])
        self.assertIsNone(cleared["home_channel"])

    def test_dm_policy_open_is_rejected(self):
        with self.assertRaises(ServiceError) as raised:
            self.service.patch_zalo_bot("agent-1", {"dm_policy": "open"})
        self.assertEqual(raised.exception.code, "invalid_dm_policy")
        with self.assertRaises(ValidationError):
            ChannelZaloBotPatch.model_validate({"dm_policy": "open"})

    def test_list_channels_returns_both_platforms(self):
        self.service.set_zalo_bot_enabled("agent-1", True)

        listed = self.service.list_channels("agent-1")

        self.assertEqual(
            [channel["platform"] for channel in listed["channels"]],
            ["zalo", "zalo_bot"],
        )
        self.assertFalse(listed["channels"][0]["token_set"])
        self.assertTrue(listed["channels"][1]["enabled"])

    def test_bot_state_includes_server_artifact_metadata(self):
        artifact = {
            "plugin": "zalo_bot",
            "artifact_digest": "sha256:" + "a" * 64,
        }
        with mock.patch(
            "xnobrain.services.channels.builtin_platform_metadata",
            return_value=artifact,
        ):
            state = self.service.list_channels("agent-1")["channels"][1]
        self.assertEqual(state["plugin_artifact"], artifact)
        self.assertEqual(state["token_set"], False)
        self.assertNotIn("path", state["plugin_artifact"])

    def test_multiplex_allowlist_can_be_driven_by_zalo_bot_only(self):
        self.service.set_zalo_bot_enabled("big-brother", True)
        self.service.set_zalo_bot_enabled("agent-1", True)

        root_config = _read_yaml(self.root / "config.yaml")
        self.assertTrue(root_config["gateway"]["multiplex_profiles"])
        self.assertEqual(root_config["gateway"]["multiplex_profile_allowlist"], ["agent-1"])
        self.assertNotIn("big-brother", root_config["gateway"]["multiplex_profile_allowlist"])

    def test_env_file_is_private(self):
        self.profile.joinpath(".env").write_text("UNRELATED=value\n", encoding="utf-8")
        os.chmod(self.profile / ".env", 0o644)

        self.service.patch_zalo_bot("agent-1", {"bot_token": TOKEN})

        mode = (self.profile / ".env").stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_fresh_connected_profile_state_reports_connected(self):
        self._enable_bot(self.profile)
        self._write_bot_state(self.profile)

        state = self.service.list_channels("agent-1")["channels"][1]

        self.assertEqual(state["status"], "connected")

    def test_stale_connected_profile_state_reports_configured(self):
        self._enable_bot(self.profile)
        self._write_bot_state(self.profile, updated_at=time.time() - 91)

        state = self.service.list_channels("agent-1")["channels"][1]

        self.assertEqual(state["status"], "configured")

    def test_missing_garbage_or_symlinked_state_reports_configured(self):
        self._enable_bot(self.profile)
        state = self.service.list_channels("agent-1")["channels"][1]
        self.assertEqual(state["status"], "configured")

        path = self.profile / "zalo_bot_state.json"
        path.write_text("{not-json", encoding="utf-8")
        state = self.service.list_channels("agent-1")["channels"][1]
        self.assertEqual(state["status"], "configured")

        path.unlink()
        target = self.root / "outside-zalo-bot-state.json"
        target.write_text(
            json.dumps({"state": "connected", "updated_at": time.time(), "pid": 1}),
            encoding="utf-8",
        )
        os.symlink(target, path)
        state = self.service.list_channels("agent-1")["channels"][1]
        self.assertEqual(state["status"], "configured")

    def test_multiplex_profiles_use_their_own_state_file(self):
        second = self.profiles / "agent-2"
        second.mkdir()
        self._enable_bot(self.profile, TOKEN)
        self._enable_bot(second, SECOND_TOKEN)
        root_config = _read_yaml(self.root / "config.yaml")
        root_config["gateway"]["multiplex_profiles"] = True
        root_config["gateway"]["multiplex_profile_allowlist"] = ["agent-1", "agent-2"]
        (self.root / "config.yaml").write_text(
            yaml.safe_dump(root_config, sort_keys=False), encoding="utf-8"
        )
        # Only profile B's plugin reports connected.
        self._write_bot_state(second)

        first_state = self.service.list_channels("agent-1")["channels"][1]
        second_state = self.service.list_channels("agent-2")["channels"][1]

        self.assertEqual(first_state["status"], "configured")
        self.assertEqual(second_state["status"], "connected")

    def test_disabled_precedes_a_connected_state(self):
        self._write_bot_state(self.profile)

        state = self.service.list_channels("agent-1")["channels"][1]

        self.assertEqual(state["status"], "disabled")

    def test_needs_token_precedes_a_connected_state(self):
        self._enable_bot(self.profile, token=None)
        self._write_bot_state(self.profile)

        state = self.service.list_channels("agent-1")["channels"][1]

        self.assertEqual(state["status"], "needs_token")

    def test_gateway_down_precedes_a_connected_state(self):
        self._enable_bot(self.profile)
        self._write_bot_state(self.profile)
        (self.root / "gateway.pid").unlink()

        state = self.service.list_channels("agent-1")["channels"][1]

        self.assertEqual(state["status"], "gateway_down")

    def test_pending_restart_precedes_a_connected_state(self):
        self._enable_bot(self.profile)
        self._write_bot_state(self.profile)
        self.service.repository.mark_gateway_restart(111)

        state = self.service.list_channels("agent-1")["channels"][1]

        self.assertEqual(state["status"], "pending_restart")

    async def test_invalid_token_returns_422_without_echoing_it(self):
        app = FastAPI()
        setup_routes(app, APIHandlers(mock.Mock(channels=self.service)))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.patch(
                f"{API_PREFIX}/agents/agent-1/channels/zalo_bot",
                json={"bot_token": BAD_TOKEN, "dm_policy": "allowlist"},
            )
            enabled_response = await client.put(
                f"{API_PREFIX}/agents/agent-1/channels/zalo_bot",
                json={"enabled": True},
            )
            valid_response = await client.patch(
                f"{API_PREFIX}/agents/agent-1/channels/zalo_bot",
                json={"bot_token": TOKEN},
            )

        self.assertEqual(response.status_code, 422)
        self.assertNotIn(BAD_TOKEN, response.text)
        self.assertNotIn(TOKEN, valid_response.text)
        self.assertEqual(enabled_response.status_code, 200)
        self.assertEqual(valid_response.status_code, 200)
        self.assertTrue(valid_response.json()["data"]["token_set"])

    async def test_duplicate_token_error_never_echoes_the_token(self):
        second = self.profiles / "agent-2"
        second.mkdir()
        self.service.patch_zalo_bot("agent-1", {"bot_token": TOKEN})
        app = FastAPI()
        setup_routes(app, APIHandlers(mock.Mock(channels=self.service)))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.patch(
                f"{API_PREFIX}/agents/agent-2/channels/zalo_bot",
                json={"bot_token": TOKEN},
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "bot_token_in_use")
        self.assertNotIn(TOKEN, response.text)

    async def test_open_dm_policy_is_rejected_by_the_api(self):
        app = FastAPI()
        setup_routes(app, APIHandlers(mock.Mock(channels=self.service)))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.patch(
                f"{API_PREFIX}/agents/agent-1/channels/zalo_bot",
                json={"dm_policy": "open"},
            )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "invalid_request")

    async def test_bot_ids_are_validated_by_the_api(self):
        app = FastAPI()
        setup_routes(app, APIHandlers(mock.Mock(channels=self.service)))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            allowed = await client.patch(
                f"{API_PREFIX}/agents/agent-1/channels/zalo_bot",
                json={"allowed_users": ["bad id!"]},
            )
            home = await client.patch(
                f"{API_PREFIX}/agents/agent-1/channels/zalo_bot",
                json={"home_channel": "../escape"},
            )
            valid = await client.patch(
                f"{API_PREFIX}/agents/agent-1/channels/zalo_bot",
                json={"allowed_users": ["U_1"], "home_channel": "U_1"},
            )

        self.assertEqual(allowed.status_code, 422)
        self.assertEqual(home.status_code, 422)
        self.assertEqual(valid.status_code, 200)
        self.assertEqual(valid.json()["data"]["allowed_users"], ["U_1"])


if __name__ == "__main__":
    unittest.main()
