import unittest

from xnobrain.services.public_text import public_error_message


class PublicErrorMessageTests(unittest.TestCase):
    def test_removes_upstream_runtime_branding_and_misleading_offline_claim(self):
        message = public_error_message(
            "API call failed after 3 retries: Hermes can't reach the model provider. "
            "You may be offline. Check your internet connection and try again."
        )
        self.assertNotIn("Hermes", message)
        self.assertNotIn("offline", message)
        self.assertIn("Agent could not reach the model provider", message)

    def test_removes_branding_from_other_public_errors(self):
        self.assertEqual(
            public_error_message("Hermes Agent failed to start"),
            "Agent failed to start",
        )
