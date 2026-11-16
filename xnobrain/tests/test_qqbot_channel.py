"""QQ Bot channel: generic credential API, group intake, turns, and send path."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from xnobrain.integrations import gateway_cli
from xnobrain.tests.channel_platform_case import ChannelPlatformCase


class QqbotChannelTests(ChannelPlatformCase, unittest.IsolatedAsyncioTestCase):
    platform = "qqbot"
    non_secrets = {"app_id": "app-123"}
    secret_field = "client_secret"
    secret_value = "qq-client-secret-123"
    bad_secret_value = "SECRETLEAK!"
    sample_thread = "user-1"
    sample_sender = "user-1"
    send_recipient = "user-1"
    standalone_sender = False

    def test_builtin_adapter_counts_as_a_registered_platform(self):
        from xnobrain.integrations import zalo_channels

        self.assertTrue(zalo_channels.platform_plugin_registered("qqbot"))

    def test_group_intake_wrapper_admits_groups_for_owner_approval(self):
        from gateway.platforms.qqbot.adapter import QQAdapter

        original = QQAdapter._is_group_allowed
        had_marker = hasattr(QQAdapter, "_xnobrain_group_intake_wrapper")
        adapter = SimpleNamespace(_group_policy="pairing")
        try:
            gateway_cli._install_adapter_group_wrapper(QQAdapter)
            self.assertTrue(QQAdapter._is_group_allowed(adapter, "g-1", "u-1"))
            adapter._group_policy = "disabled"
            self.assertFalse(QQAdapter._is_group_allowed(adapter, "g-1", "u-1"))
        finally:
            QQAdapter._is_group_allowed = original
            if had_marker:
                QQAdapter._xnobrain_group_intake_wrapper = True
            else:
                delattr(QQAdapter, "_xnobrain_group_intake_wrapper")


if __name__ == "__main__":
    unittest.main()
