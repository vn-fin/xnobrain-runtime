"""Feishu / Lark channel: credentials, transport, groups, turns, and send."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from xnobrain.integrations import gateway_cli
from xnobrain.tests.channel_platform_case import ChannelPlatformCase


class FeishuChannelTests(ChannelPlatformCase, unittest.IsolatedAsyncioTestCase):
    platform = "feishu"
    non_secrets = {"app_id": "cli_test123"}
    secret_field = "app_secret"
    secret_value = "feishu-secret-123"
    bad_secret_value = "SECRETLEAK!"
    optional_non_secrets = {"domain": "lark"}
    sample_thread = "oc_chat123"
    sample_sender = "ou_user123"
    send_recipient = "oc_chat123"
    standalone_sender = True

    def test_domain_is_returned_and_websocket_is_pinned(self):
        self.enable()
        self.assertIn("FEISHU_CONNECTION_MODE=websocket", self.env_text())

        state = self.patch({**self.required_body(), "domain": "lark"})

        self.assertEqual(state["credentials"]["domain"], "lark")
        env = self.env_text()
        self.assertIn("FEISHU_DOMAIN=lark", env)
        self.assertIn("FEISHU_CONNECTION_MODE=websocket", env)

    def test_group_intake_wrapper_keeps_disabled_and_mention_gating(self):
        from plugins.platforms.feishu.adapter import FeishuAdapter

        original = FeishuAdapter._admit
        had_marker = hasattr(FeishuAdapter, "_xnobrain_group_intake_wrapper")
        original_admit = lambda self, sender, message: "group_policy_rejected"
        mentioned = SimpleNamespace(chat_id="oc_chat123")
        unmentioned = SimpleNamespace(chat_id="oc_chat123")
        adapter = SimpleNamespace(
            _group_rules={},
            _default_group_policy="allowlist",
            _group_policy="allowlist",
            _require_mention_for=lambda chat_id: True,
            _mentions_self=lambda message: message is mentioned,
        )
        try:
            FeishuAdapter._admit = original_admit
            gateway_cli._install_feishu_group_intake_wrapper()

            self.assertIsNone(FeishuAdapter._admit(adapter, None, mentioned))
            self.assertEqual(
                FeishuAdapter._admit(adapter, None, unmentioned),
                "group_policy_rejected",
            )

            adapter._default_group_policy = "disabled"
            self.assertEqual(
                FeishuAdapter._admit(adapter, None, mentioned),
                "group_policy_rejected",
            )
        finally:
            FeishuAdapter._admit = original
            if had_marker:
                FeishuAdapter._xnobrain_group_intake_wrapper = True
            else:
                delattr(FeishuAdapter, "_xnobrain_group_intake_wrapper")


if __name__ == "__main__":
    unittest.main()
