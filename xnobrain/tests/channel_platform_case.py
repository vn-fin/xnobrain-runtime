"""Shared fixture for the generic Hermes channel platforms added in plan 021.

Each platform test module supplies its own registry descriptor values and
inherits the credential, access, turn, and send-path checks from
``ChannelPlatformCase``.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from xnobrain.handlers.api import APIHandlers
from xnobrain.integrations import gateway_cli, zalo_channels
from xnobrain.integrations.channel_platforms import PLATFORMS
from xnobrain.models.channels import ChannelTurnRequest
from xnobrain.repositories.files import FileRepository
from xnobrain.routes.definition import API_PREFIX
from xnobrain.routes.setup import setup_routes
from xnobrain.services.base import ServiceError
from xnobrain.services.channels import ChannelsService


def read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def configured_profile(config: dict, platform: str) -> dict:
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


class _PlatformKey:
    """Hashable platform-like key with the ``value`` gateway uses."""

    def __init__(self, value: str):
        self.value = value

    def __hash__(self) -> int:
        return hash(self.value)

    def __eq__(self, other: object) -> bool:
        return getattr(other, "value", other) == self.value


class ChannelPlatformCase:
    """Shared contract tests for one generic credential platform."""

    platform = ""
    # field -> value submitted for every required non-secret credential.
    non_secrets: dict[str, str] = {}
    # field -> value submitted for the one write-only secret under test.
    secret_field = ""
    secret_value = ""
    bad_secret_value = ""
    # field -> value for optional credentials this platform also returns.
    optional_non_secrets: dict[str, str] = {}
    sample_thread = ""
    sample_sender = ""
    send_recipient = ""
    standalone_sender = True

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
            yaml.safe_dump(
                {
                    "platforms": {
                        self.platform: {
                            "enabled": False,
                            "extra": {"dm_policy": "disabled"},
                        }
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

    # ── helpers ──────────────────────────────────────────────────────────

    def enable(self, enabled: bool = True) -> dict:
        return self.service.set_platform_channel_enabled("agent-1", enabled, None, self.platform)

    def patch(self, body: dict) -> dict:
        return self.service.patch_platform_channel("agent-1", body, None, self.platform)

    def logout(self) -> dict:
        return self.service.logout_platform_channel("agent-1", None, self.platform)

    def required_body(self) -> dict:
        return {
            **self.non_secrets,
            self.secret_field: self.secret_value,
        }

    def env_text(self) -> str:
        return (self.profile / ".env").read_text(encoding="utf-8")

    def state(self) -> dict:
        for channel in self.service.list_channels("agent-1")["channels"]:
            if channel["platform"] == self.platform:
                return channel
        raise AssertionError(f"{self.platform} is missing from channel state")

    # ── shared tests ─────────────────────────────────────────────────────

    def test_state_exposes_the_accepted_dm_policies(self):
        self.enable()

        self.assertEqual(
            self.state()["dm_policies"], list(PLATFORMS[self.platform].dm_policies)
        )

    def test_enable_without_credentials_reports_needs_token(self):
        state = self.enable()

        self.assertTrue(state["enabled"])
        self.assertEqual(state["status"], "needs_token")
        self.assertFalse(state[f"{self.secret_field}_set"])
        config = read_yaml(self.profile / "config.yaml")
        self.assertTrue(configured_profile(config, self.platform)["enabled"])
        root_config = read_yaml(self.root / "config.yaml")
        self.assertIn("agent-1", root_config["gateway"]["multiplex_profile_allowlist"])
        self.assertIn((111, 10), self.kills)

    def test_required_credentials_enable_and_logout_clears_every_field(self):
        self.enable()
        state = self.patch(self.required_body())

        self.assertEqual(state["status"], "pending_restart")
        self.assertTrue(state[f"{self.secret_field}_set"])
        for field, value in self.non_secrets.items():
            self.assertEqual(state["credentials"][field], value)
        entry = PLATFORMS[self.platform]
        env = self.env_text()
        for item in entry.credentials:
            if item.required:
                self.assertIn(item.env, env)

        logged_out = self.logout()
        self.assertFalse(logged_out["enabled"])
        self.assertEqual(logged_out["status"], "disabled")
        self.assertFalse(logged_out[f"{self.secret_field}_set"])
        remaining = self.profile / ".env"
        env_after = remaining.read_text(encoding="utf-8") if remaining.exists() else ""
        for item in entry.credentials:
            self.assertNotIn(item.env, env_after)

    def test_optional_non_secret_credentials_are_returned(self):
        if not self.optional_non_secrets:
            self.skipTest("platform has no optional non-secret credential")
        self.enable()
        state = self.patch({**self.required_body(), **self.optional_non_secrets})

        for field, value in self.optional_non_secrets.items():
            self.assertEqual(state["credentials"][field], value)

    async def test_secret_is_write_only_and_redacted_from_errors(self):
        self.enable()
        state = self.patch({self.secret_field: self.secret_value})

        self.assertTrue(state[f"{self.secret_field}_set"])
        self.assertNotIn(self.secret_value, json.dumps(state))
        self.assertNotIn(self.secret_value, repr(state))
        self.assertIn(
            PLATFORMS[self.platform].token_operation_prefix,
            __import__(
                "xnobrain.handlers.extension_validation",
                fromlist=["_REDACTED_OPERATION_PREFIXES"],
            )._REDACTED_OPERATION_PREFIXES,
        )

        app = FastAPI()
        setup_routes(app, APIHandlers(mock.Mock(channels=self.service)))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.patch(
                f"{API_PREFIX}/agents/agent-1/channels/{self.platform}",
                json={self.secret_field: self.bad_secret_value},
            )

        self.assertEqual(response.status_code, 422)
        self.assertNotIn(self.bad_secret_value, response.text)

    def test_open_policy_writes_the_adapter_allow_all_flag(self):
        entry = PLATFORMS[self.platform]
        self.enable()
        self.patch({"dm_policy": "open"})
        env = self.env_text()
        self.assertIn(f"{entry.allow_all_env}=true", env)
        self.patch({"dm_policy": "allowlist"})
        self.assertNotIn(entry.allow_all_env, self.env_text())

    def test_allowed_users_and_home_channel_are_written(self):
        entry = PLATFORMS[self.platform]
        self.enable()
        state = self.patch(
            {
                "allowed_users": [self.sample_sender],
                "home_channel": self.send_recipient,
            }
        )
        self.assertEqual(state["allowed_users"], [self.sample_sender])
        self.assertEqual(state["home_channel"], self.send_recipient)
        env = self.env_text()
        for key in entry.allowed_users_envs:
            self.assertIn(key, env)
        self.assertIn(entry.home_channel_env, env)

        with self.assertRaises(ServiceError) as raised:
            self.patch({"allowed_users": ["bad/id"]})
        self.assertEqual(raised.exception.code, "invalid_allowed_users")
        with self.assertRaises(ServiceError) as raised:
            self.patch({"home_channel": "../escape"})
        self.assertEqual(raised.exception.code, "invalid_home_channel")

    def test_turn_request_validates_ids_against_the_platform(self):
        request = ChannelTurnRequest.model_validate(
            {
                "platform": self.platform,
                "agent_id": "agent-1",
                "thread_id": self.sample_thread,
                "thread_type": "dm",
                "sender_id": self.sample_sender,
                "text": "hello",
                "message_id": "message-1",
            }
        )
        self.assertEqual(request.thread_id, self.sample_thread)
        with self.assertRaises(ValidationError):
            ChannelTurnRequest.model_validate(
                {
                    "platform": self.platform,
                    "agent_id": "agent-1",
                    "thread_id": "channel/../../escape",
                    "thread_type": "dm",
                    "sender_id": self.sample_sender,
                    "text": "hello",
                    "message_id": "message-1",
                }
            )

    async def test_send_path_uses_the_registry_or_hermes_dispatcher(self):
        platform = _PlatformKey(self.platform)
        pconfig = SimpleNamespace(enabled=True, extra={})
        config = SimpleNamespace(platforms={platform: pconfig})
        sender = mock.AsyncMock(return_value={"success": True, "message_id": "42"})
        dispatcher = mock.AsyncMock(return_value={"success": True, "message_id": "dispatched"})
        with (
            mock.patch("tools.send_message_tool.prepare_send_message_platforms"),
            mock.patch("tools.send_message_tool._send_to_platform", dispatcher),
            mock.patch(
                "gateway.platform_registry.platform_registry.get",
                return_value=(
                    SimpleNamespace(
                        max_message_length=PLATFORMS[self.platform].max_message_length,
                        standalone_sender_fn=sender,
                    )
                    if self.standalone_sender
                    else None
                ),
            ),
            mock.patch("gateway.config.Platform", return_value=platform),
            mock.patch("gateway.config.load_gateway_config", return_value=config),
        ):
            result = await zalo_channels.send_text(
                self.send_recipient, "hello", platform=self.platform
            )

        if self.standalone_sender:
            self.assertEqual(result, {"message_id": "42"})
            sender.assert_awaited_once_with(pconfig, self.send_recipient, "hello")
            dispatcher.assert_not_awaited()
        else:
            self.assertEqual(result, {"message_id": "dispatched"})
            dispatcher.assert_awaited_once_with(platform, pconfig, self.send_recipient, "hello")
            sender.assert_not_awaited()

    async def test_send_rejects_an_id_outside_the_platform_pattern(self):
        with self.assertRaises(zalo_channels.ChannelDeliveryError) as raised:
            await zalo_channels.send_text("channel/../../escape", "hello", platform=self.platform)
        self.assertEqual(raised.exception.code, "channel_recipient_invalid")
