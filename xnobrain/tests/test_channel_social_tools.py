"""Per-agent social-tools switch: storage, API, run gate, and owner grant."""

from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from xnobrain.handlers.api import APIHandlers
from xnobrain.integrations import conversation_runner
from xnobrain.repositories.channels import social_tools_enabled
from xnobrain.repositories.files import FileRepository
from xnobrain.routes.definition import API_PREFIX
from xnobrain.routes.setup import setup_routes
from xnobrain.services.base import ServiceError
from xnobrain.services.channels import ChannelsService
from xnobrain.trusted_context import (
    TRUSTED_SIGNATURE_HEADER,
    TRUSTED_SUBJECT_HEADER,
    TRUSTED_TENANT_HEADER,
    TrustedRequestContext,
    principal_signature,
)

TOKEN = "channel-social-tools-token"
TENANT = "personal-user-1"


def _turn_body(**overrides):
    value = {
        "platform": "zalo",
        "agent_id": "agent-1",
        "thread_id": "123456",
        "thread_type": "dm",
        "sender_id": "789",
        "sender_name": "Nam",
        "text": "hello there",
        "message_id": "message-1",
        "ts": 1_700_000_000,
    }
    value.update(overrides)
    return value


class _Platform:
    def __init__(self, repository: FileRepository, root: Path):
        self.repository = repository
        self.root = root
        self.config = SimpleNamespace(root_profile=root)
        self.agents = SimpleNamespace()

    def _conversation_profile(self, agent_id: str) -> Path:
        return self.repository.profile_path(agent_id)

    def _snapshot_agent(self, agent_id, kind, target, content):
        return self.repository.snapshot(agent_id, kind, target, content)


class _NoGrant:
    async def grant(self, scope, *, agent_id, platform, user_id, tenant_id, now=None):
        return ""


class ChannelSocialToolsStorageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.profiles = root / "profiles"
        self.root = self.profiles / "root"
        self.profile = self.profiles / "agent-1"
        self.root.mkdir(parents=True)
        self.profile.mkdir(parents=True)
        self.profile.joinpath("config.yaml").write_text(
            "platforms:\n  zalo:\n    enabled: false\n", encoding="utf-8"
        )
        self.environment = mock.patch.dict(
            os.environ,
            {
                "FT_ENABLE_AGENT_CHANNELS": "true",
                "RUNTIME_INTERNAL_SERVICE_TOKEN": TOKEN,
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

    def _headers(self, subject: str = "user:owner") -> dict[str, str]:
        return {
            TRUSTED_SUBJECT_HEADER: subject,
            TRUSTED_TENANT_HEADER: TENANT,
            TRUSTED_SIGNATURE_HEADER: principal_signature(TOKEN, subject, TENANT),
        }

    def _client(self) -> AsyncClient:
        app = FastAPI()
        setup_routes(app, APIHandlers(mock.Mock(channels=self.service)))
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    def test_missing_file_means_off(self):
        self.assertFalse(social_tools_enabled(self.profile))

    def test_missing_file_uses_the_default_but_a_written_switch_wins(self):
        # Big Brother passes default=True; a stored "off" still turns it off.
        self.assertTrue(social_tools_enabled(self.profile, default=True))
        self.service.repository.write_social_tools(self.profile, False)
        self.assertFalse(social_tools_enabled(self.profile, default=True))
        snapshot = self.service.list_channels("agent-1")
        self.assertFalse(snapshot["social_tools"])

    async def test_put_on_and_off_persists_and_returns_the_snapshot(self):
        path = f"{API_PREFIX}/agents/agent-1/channels/social_tools"
        state_file = self.profile / ".xnobrain" / "channels" / "social_tools.json"
        async with self._client() as client:
            enabled = await client.put(
                path, json={"enabled": True}, headers=self._headers()
            )
            self.assertEqual(enabled.status_code, 200, enabled.text)
            self.assertTrue(enabled.json()["data"]["social_tools"])
            self.assertTrue(social_tools_enabled(self.profile))
            self.assertEqual(state_file.stat().st_mode & 0o777, 0o600)

            listed = await client.get(f"{API_PREFIX}/agents/agent-1/channels")
            self.assertTrue(listed.json()["data"]["social_tools"])

            disabled = await client.put(
                path, json={"enabled": False}, headers=self._headers()
            )
            self.assertEqual(disabled.status_code, 200, disabled.text)
            self.assertFalse(disabled.json()["data"]["social_tools"])
            self.assertFalse(social_tools_enabled(self.profile))

        trusted = TrustedRequestContext("user:owner", TENANT)
        self.service.set_social_tools("agent-1", True, trusted)
        self.assertTrue(self.service.list_channels("agent-1")["social_tools"])
        self.service.set_social_tools("agent-1", False, trusted)
        self.assertFalse(self.service.list_channels("agent-1")["social_tools"])

    async def test_only_the_owner_can_change_the_switch(self):
        path = f"{API_PREFIX}/agents/agent-1/channels/social_tools"
        async with self._client() as client:
            owner = await client.put(
                path, json={"enabled": True}, headers=self._headers()
            )
            self.assertEqual(owner.status_code, 200, owner.text)

            other = await client.put(
                path, json={"enabled": False}, headers=self._headers("user:other")
            )
            self.assertEqual(other.status_code, 403, other.text)
            self.assertTrue(social_tools_enabled(self.profile))

            anonymous = await client.put(path, json={"enabled": False})
            self.assertEqual(anonymous.status_code, 401, anonymous.text)
            self.assertTrue(social_tools_enabled(self.profile))

    async def test_body_forbids_extra_fields(self):
        path = f"{API_PREFIX}/agents/agent-1/channels/social_tools"
        async with self._client() as client:
            response = await client.put(
                path,
                json={"enabled": True, "extra": True},
                headers=self._headers(),
            )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertFalse(social_tools_enabled(self.profile))


class ChannelSocialToolsBindGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.profiles = root / "profiles"
        self.root = self.profiles / "root"
        self.profile = self.profiles / "agent-1"
        self.root.mkdir(parents=True)
        self.profile.mkdir(parents=True)
        self.profile.joinpath("config.yaml").write_text(
            "platforms:\n  zalo:\n    enabled: true\n    extra:\n      dm_policy: allowlist\n",
            encoding="utf-8",
        )
        self.environment = mock.patch.dict(
            os.environ,
            {
                "FT_ENABLE_AGENT_CHANNELS": "true",
                "RUNTIME_INTERNAL_SERVICE_TOKEN": TOKEN,
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

    @staticmethod
    def _plugin_enabled():
        return mock.patch.multiple(
            "xnobrain.integrations.zalo_channels",
            profile_enables_platform=mock.Mock(return_value=True),
            platform_plugin_registered=mock.Mock(return_value=True),
            zalo_plugin_registered=mock.Mock(return_value=True),
        )

    @staticmethod
    def _agent():
        return SimpleNamespace(
            tools=[],
            valid_tool_names=set(),
            enabled_toolsets=["hermes-api-server"],
        )

    async def test_off_binds_no_social_tools(self):
        from xnobrain.integrations import channel_tools, zalo_bot_tools

        self.assertFalse(social_tools_enabled(self.profile))
        manager = SimpleNamespace(channels_service=self.service)
        with self._plugin_enabled():
            with ExitStack() as stack:
                conversation_runner._bind_social_channel_tools(
                    stack,
                    manager,
                    self.profile,
                    {"name": "agent-1"},
                    "conv-off",
                    "run-off",
                    None,
                )
            self.assertIsNone(channel_tools.run_binding("conv-off")[0])

        agent = self._agent()
        channel_tools.install_tools(agent, "conv-off", self.profile)
        zalo_bot_tools.install_tools(agent, "conv-off", self.profile)
        self.assertEqual(agent.tools, [])
        self.assertEqual(agent.valid_tool_names, set())

    async def test_on_binds_channel_zalo_and_zalo_bot_tools(self):
        from xnobrain.integrations import channel_tools, zalo_bot_tools

        self.service.repository.write_social_tools(self.profile, True)
        manager = SimpleNamespace(channels_service=self.service)
        with self._plugin_enabled():
            with ExitStack() as stack:
                conversation_runner._bind_social_channel_tools(
                    stack,
                    manager,
                    self.profile,
                    {"name": "agent-1"},
                    "conv-on",
                    "run-on",
                    None,
                )
                binding, platforms = channel_tools.run_binding("conv-on")
                self.assertIsNotNone(binding)
                self.assertIn("zalo", platforms)
                self.assertIn("zalo_bot", platforms)

                agent = self._agent()
                channel_tools.install_tools(agent, "conv-on", self.profile)
                zalo_bot_tools.install_tools(agent, "conv-on", self.profile)
                names = {
                    tool["function"]["name"] for tool in agent.tools
                }
                self.assertIn("send_channel_message", names)
                self.assertIn("zalo_status", names)
                self.assertIn("zalo_bot_status", names)

    async def test_channel_turn_still_requires_the_per_run_grant(self):
        self.profile.joinpath("config.yaml").write_text(
            yaml.safe_dump(
                {
                    "platforms": {
                        "zalo": {
                            "enabled": True,
                            "extra": {"dm_policy": "allowlist"},
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        self.service.repository.write_owner(
            self.profile,
            {
                "user_id": "user-1",
                "tenant_id": TENANT,
                "organization_id": "",
                "agent_id": "agent-1",
                "bound_at": 1,
            },
        )
        self.service.mcp_grants = _NoGrant()

        with self.assertRaises(ServiceError) as raised:
            await self.service.run_internal_turn(_turn_body(), TOKEN)

        self.assertEqual(raised.exception.status, 503)
        self.assertEqual(raised.exception.code, "channel_mcp_grant_unavailable")


if __name__ == "__main__":
    unittest.main()
