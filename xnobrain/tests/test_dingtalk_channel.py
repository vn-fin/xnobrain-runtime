"""DingTalk channel: generic credential API, access policy, turns, and send."""

from __future__ import annotations

import unittest

from xnobrain.tests.channel_platform_case import ChannelPlatformCase


class DingtalkChannelTests(ChannelPlatformCase, unittest.IsolatedAsyncioTestCase):
    platform = "dingtalk"
    non_secrets = {"client_id": "ding-client-123"}
    secret_field = "client_secret"
    secret_value = "ding-secret-123"
    bad_secret_value = "SECRETLEAK!"
    sample_thread = "cid-123=="
    sample_sender = "staff-123"
    send_recipient = "cid-123=="
    standalone_sender = True

    def test_stream_mode_writes_no_webhook_url(self):
        self.enable()
        self.patch(self.required_body())
        env = self.env_text()

        self.assertIn("DINGTALK_CLIENT_ID=ding-client-123", env)
        self.assertIn("DINGTALK_CLIENT_SECRET=ding-secret-123", env)
        self.assertNotIn("DINGTALK_WEBHOOK_URL", env)


if __name__ == "__main__":
    unittest.main()
