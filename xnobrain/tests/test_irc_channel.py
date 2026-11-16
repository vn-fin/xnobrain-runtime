"""IRC channel: generic credential API, access policy, turns, and send path."""

from __future__ import annotations

import unittest

from xnobrain.tests.channel_platform_case import ChannelPlatformCase


class IrcChannelTests(ChannelPlatformCase, unittest.IsolatedAsyncioTestCase):
    platform = "irc"
    non_secrets = {
        "server": "irc.example.com",
        "channel": "#hermes",
        "nickname": "hermes-bot",
    }
    secret_field = "server_password"
    secret_value = "server-pass-123"
    bad_secret_value = "SECRETLEAK\x01"
    sample_thread = "#hermes"
    sample_sender = "alice"
    send_recipient = "#hermes"
    standalone_sender = True

    def test_group_models_accept_channel_ids(self):
        from xnobrain.models.channels import (
            ChannelGroupPending,
            ChannelGroupRequest,
            ChannelGroupSeenRequest,
        )

        self.assertEqual(ChannelGroupRequest(group_id="#hermes").group_id, "#hermes")
        self.assertEqual(ChannelGroupPending(group_id="#hermes").group_id, "#hermes")
        seen = ChannelGroupSeenRequest(platform="irc", agent_id="agent-1", group_id="#hermes")
        self.assertEqual(seen.group_id, "#hermes")


if __name__ == "__main__":
    unittest.main()
