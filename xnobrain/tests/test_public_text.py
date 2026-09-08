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

    def test_fresh_integration_import_and_stream_error_normalization(self):
        import subprocess
        import sys
        import textwrap

        script = textwrap.dedent("""
            import json
            import xnobrain.integrations.cron_timezone
            from xnobrain.integrations.conversation_stream import (
                ConversationStreamMixin,
                public_error_message,
            )

            assert public_error_message(None, "Synthetic fallback") == "Synthetic fallback"
            payload = ConversationStreamMixin()._chat_sse_error("Hermes Agent failed")
            assert b"Hermes" not in payload
            assert b"Agent failed" in payload
        """)
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True,
            timeout=30, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
