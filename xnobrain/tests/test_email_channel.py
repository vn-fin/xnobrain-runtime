"""Email channel: generic credential API, access policy, turns, and send path."""

from __future__ import annotations

import unittest
from unittest import mock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from xnobrain.handlers.api import APIHandlers
from xnobrain.integrations import gateway_cli
from xnobrain.routes.definition import API_PREFIX
from xnobrain.routes.setup import setup_routes
from xnobrain.services.base import ServiceError
from xnobrain.tests.channel_platform_case import ChannelPlatformCase


class EmailChannelTests(ChannelPlatformCase, unittest.IsolatedAsyncioTestCase):
    platform = "email"
    non_secrets = {
        "address": "agent@example.com",
        "imap_host": "imap.example.com",
        "smtp_host": "smtp.example.com",
    }
    secret_field = "password"
    secret_value = "abcd efgh ijkl mnop"
    bad_secret_value = "SECRETLEAK\x01"
    sample_thread = "user@example.com"
    sample_sender = "sender@example.com"
    send_recipient = "user@example.com"
    standalone_sender = True

    def test_pairing_is_rejected_for_email(self):
        self.enable()

        with self.assertRaises(ServiceError) as raised:
            self.patch({"dm_policy": "pairing"})

        self.assertEqual(raised.exception.status, 422)
        self.assertEqual(raised.exception.code, "invalid_dm_policy")
        self.assertEqual(self.state()["dm_policies"], ["disabled", "allowlist", "open"])

    async def test_pairing_patch_returns_the_existing_422_shape(self):
        self.enable()
        app = FastAPI()
        setup_routes(app, APIHandlers(mock.Mock(channels=self.service)))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.patch(
                f"{API_PREFIX}/agents/agent-1/channels/email",
                json={"dm_policy": "pairing"},
            )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "invalid_dm_policy")

    def test_email_has_no_env_mutation_wrapper(self):
        for name in (
            "install_gateway_email_access",
            "_dispatch_email_with_sender",
            "_csv_with",
        ):
            self.assertFalse(hasattr(gateway_cli, name), name)

    def test_open_policy_writes_email_allow_all(self):
        self.enable()
        self.patch({"dm_policy": "open"})

        self.assertIn("EMAIL_ALLOW_ALL_USERS=true", self.env_text())
        self.assertEqual(self.state()["dm_policies"], ["disabled", "allowlist", "open"])


if __name__ == "__main__":
    unittest.main()
