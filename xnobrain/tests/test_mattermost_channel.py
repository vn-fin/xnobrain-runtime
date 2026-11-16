"""Mattermost channel: generic credential API, access policy, turns, and send."""

from __future__ import annotations

import unittest

from xnobrain.tests.channel_platform_case import ChannelPlatformCase


class MattermostChannelTests(ChannelPlatformCase, unittest.IsolatedAsyncioTestCase):
    platform = "mattermost"
    non_secrets = {"url": "https://mm.example.com"}
    secret_field = "token"
    secret_value = "abc123def456ghi789jkl012mn"
    bad_secret_value = "SECRETLEAK!"
    sample_thread = "3uo8dkh1p7g1mfk49ear5fzs5c"
    sample_sender = "8fk2jd9s0a7bncm1xqw4tp6r3e"
    send_recipient = "3uo8dkh1p7g1mfk49ear5fzs5c"
    standalone_sender = True


if __name__ == "__main__":
    unittest.main()
