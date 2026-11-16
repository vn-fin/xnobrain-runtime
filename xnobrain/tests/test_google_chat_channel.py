"""Google Chat channel: Pub/Sub credentials, access policy, turns, and send."""

from __future__ import annotations

import json
import unittest

from xnobrain.services.base import ServiceError
from xnobrain.tests.channel_platform_case import ChannelPlatformCase

SERVICE_ACCOUNT = """{
  "type": "service_account",
  "project_id": "demo-project",
  "private_key": "-----BEGIN PRIVATE KEY-----\\nMIIB\\n-----END PRIVATE KEY-----\\n",
  "client_email": "bot@demo-project.iam.gserviceaccount.com"
}"""


class GoogleChatChannelTests(ChannelPlatformCase, unittest.IsolatedAsyncioTestCase):
    platform = "google_chat"
    non_secrets = {
        "project_id": "demo-project",
        "subscription_name": "projects/demo-project/subscriptions/hermes-events",
    }
    secret_field = "service_account_json"
    secret_value = SERVICE_ACCOUNT
    bad_secret_value = '{"type": "service_account"'
    sample_thread = "spaces/AAAA"
    sample_sender = "user@example.com"
    send_recipient = "spaces/AAAA"
    standalone_sender = True

    def test_service_account_json_is_compacted_on_one_env_line(self):
        self.enable()
        state = self.patch(self.required_body())
        env = self.env_text()
        line = next(
            item
            for item in env.splitlines()
            if item.startswith("GOOGLE_CHAT_SERVICE_ACCOUNT_JSON=")
        )
        raw = line.split("=", 1)[1]

        self.assertNotIn("\n", raw)
        parsed = json.loads(raw)
        self.assertEqual(parsed["project_id"], "demo-project")
        self.assertEqual(parsed["client_email"], "bot@demo-project.iam.gserviceaccount.com")
        self.assertTrue(state["service_account_json_set"])
        self.assertNotIn("PRIVATE KEY", json.dumps(state))
        self.assertEqual(state["credentials"]["project_id"], "demo-project")
        self.assertEqual(
            state["credentials"]["subscription_name"],
            "projects/demo-project/subscriptions/hermes-events",
        )

    def test_invalid_service_account_json_is_rejected_without_echo(self):
        self.enable()
        with self.assertRaises(ServiceError) as raised:
            self.patch({"service_account_json": self.bad_secret_value})
        self.assertEqual(raised.exception.status, 422)
        self.assertNotIn(self.bad_secret_value, str(raised.exception))

    def test_no_http_events_url_is_written(self):
        self.enable()
        self.patch(self.required_body())
        self.assertNotIn("GOOGLE_CHAT_HTTP_EVENTS_URL", self.env_text())


if __name__ == "__main__":
    unittest.main()
