"""Yuanbao channel: generic credential API, group intake, turns, and send path."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from xnobrain.integrations import gateway_cli
from xnobrain.tests.channel_platform_case import ChannelPlatformCase


class YuanbaoChannelTests(ChannelPlatformCase, unittest.IsolatedAsyncioTestCase):
    platform = "yuanbao"
    non_secrets = {"app_id": "app-123"}
    secret_field = "app_secret"
    secret_value = "yuanbao-app-secret-123"
    bad_secret_value = "SECRETLEAK!"
    optional_non_secrets = {
        "ws_url": "wss://api.yuanbao.example.com/ws",
        "api_domain": "https://api.yuanbao.example.com",
        "bot_id": "bot-123",
    }
    sample_thread = "direct:user123"
    sample_sender = "user123"
    send_recipient = "direct:user123"
    standalone_sender = False

    def test_builtin_adapter_counts_as_a_registered_platform(self):
        from xnobrain.integrations import zalo_channels

        self.assertTrue(zalo_channels.platform_plugin_registered("yuanbao"))

    def test_group_models_accept_group_prefixed_ids(self):
        from xnobrain.models.channels import (
            ChannelGroupApproved,
            ChannelGroupRequest,
            ChannelGroupSeenRequest,
        )

        self.assertEqual(ChannelGroupRequest(group_id="group:grp456").group_id, "group:grp456")
        self.assertEqual(ChannelGroupApproved(group_id="group:grp456").group_id, "group:grp456")
        seen = ChannelGroupSeenRequest(
            platform="yuanbao", agent_id="agent-1", group_id="group:grp456"
        )
        self.assertEqual(seen.group_id, "group:grp456")

    def test_dm_allowlist_writes_both_adapter_keys(self):
        self.enable()
        self.patch({"allowed_users": [self.sample_sender]})
        env = self.env_text()
        self.assertIn(f"YUANBAO_ALLOWED_USERS={self.sample_sender}", env)
        self.assertIn(f"YUANBAO_DM_ALLOW_FROM={self.sample_sender}", env)

    async def test_group_intake_wrapper_admits_groups_for_owner_approval(self):
        from gateway.platforms.yuanbao import AccessGuardMiddleware, AccessPolicy

        original = AccessGuardMiddleware.handle
        had_marker = hasattr(AccessGuardMiddleware, "_xnobrain_group_intake_wrapper")
        policy = AccessPolicy(
            dm_policy="pairing",
            dm_allow_from=[],
            group_policy="pairing",
            group_allow_from=[],
        )
        ctx = SimpleNamespace(
            chat_type="group",
            group_code="g-1",
            adapter=SimpleNamespace(_access_policy=policy, name="Yuanbao"),
        )
        next_fn = mock.AsyncMock()
        try:
            gateway_cli._install_yuanbao_group_intake_wrapper()
            await AccessGuardMiddleware().handle(ctx, next_fn)
            next_fn.assert_awaited_once()

            policy._group_policy = "disabled"
            next_fn.reset_mock()
            await AccessGuardMiddleware().handle(ctx, next_fn)
            next_fn.assert_not_awaited()

            # DM intake and the home-channel check keep the original policy.
            policy._group_policy = "pairing"
            self.assertFalse(AccessPolicy.is_group_allowed(policy, "g-1"))
            self.assertFalse(policy.is_dm_intake_allowed(""))
        finally:
            AccessGuardMiddleware.handle = original
            if had_marker:
                AccessGuardMiddleware._xnobrain_group_intake_wrapper = True
            else:
                delattr(AccessGuardMiddleware, "_xnobrain_group_intake_wrapper")


if __name__ == "__main__":
    unittest.main()
