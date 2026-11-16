"""Matrix channel: generic credential API, access policy, turns, and send."""

from __future__ import annotations

import unittest

from xnobrain.tests.channel_platform_case import ChannelPlatformCase


class MatrixChannelTests(ChannelPlatformCase, unittest.IsolatedAsyncioTestCase):
    platform = "matrix"
    non_secrets = {"homeserver": "https://matrix.example.com"}
    secret_field = "access_token"
    secret_value = "syt_abcdefghijklmnopqrstuvwxyz"
    bad_secret_value = "not a token!"
    sample_thread = "!room:matrix.example.com"
    sample_sender = "@alice:matrix.example.com"
    send_recipient = "!room:matrix.example.com"
    standalone_sender = True

    def test_token_auth_leaves_password_and_e2ee_unset(self):
        self.enable()
        state = self.patch(self.required_body())
        env = self.env_text()

        self.assertEqual(state["credentials"]["homeserver"], "https://matrix.example.com")
        self.assertIn("MATRIX_ACCESS_TOKEN=", env)
        self.assertNotIn("MATRIX_PASSWORD", env)
        self.assertNotIn("MATRIX_USER_ID", env)
        self.assertNotIn("MATRIX_E2EE_MODE", env)
        self.assertNotIn("MATRIX_ENCRYPTION", env)


if __name__ == "__main__":
    unittest.main()
