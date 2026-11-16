"""WeCom channel: generic credential API, group intake, turns, and send path."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from xnobrain.integrations import gateway_cli
from xnobrain.tests.channel_platform_case import ChannelPlatformCase


class WecomChannelTests(ChannelPlatformCase, unittest.IsolatedAsyncioTestCase):
    platform = "wecom"
    non_secrets = {"bot_id": "bot-123"}
    secret_field = "secret"
    secret_value = "wecom-secret-123"
    bad_secret_value = "SECRETLEAK!"
    sample_thread = "user-1"
    sample_sender = "user-1"
    send_recipient = "user-1"
    standalone_sender = True

    def test_group_intake_wrapper_admits_groups_for_owner_approval(self):
        from plugins.platforms.wecom.adapter import WeComAdapter

        original = WeComAdapter._is_group_allowed
        had_marker = hasattr(WeComAdapter, "_xnobrain_group_intake_wrapper")
        adapter = SimpleNamespace(_group_policy="pairing")
        try:
            gateway_cli._install_adapter_group_wrapper(WeComAdapter)
            self.assertTrue(WeComAdapter._is_group_allowed(adapter, "g-1", "u-1"))
            adapter._group_policy = "disabled"
            self.assertFalse(WeComAdapter._is_group_allowed(adapter, "g-1", "u-1"))
        finally:
            WeComAdapter._is_group_allowed = original
            if had_marker:
                WeComAdapter._xnobrain_group_intake_wrapper = True
            else:
                delattr(WeComAdapter, "_xnobrain_group_intake_wrapper")


if __name__ == "__main__":
    unittest.main()
