"""Correlation survives disabled exporters without logging client payloads."""

import io
import json
import logging
import time
import unittest

from xnobrain.logging_config import structured_handler
from xnobrain.upload_timing import UploadTiming, relay_trace_headers

TRACE = "00-57c654efb4b514dd64d9290039ed6812-afef774624ff90b7-01"
UPLOAD = "5e3266f9d35a43fbb9808e7a28182ef2"


class UploadTimingTests(unittest.TestCase):
    def test_correlation_without_exporter_and_bounded_metadata(self):
        logger = logging.getLogger("xnobrain.upload_timing")
        output = io.StringIO()
        handler = structured_handler(output)
        previous_level = logger.level
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        self.addCleanup(logger.setLevel, previous_level)
        headers = {"traceparent": TRACE, "authorization": "secret", "baggage": "private"}
        timing = UploadTiming(UPLOAD, "632", headers)
        timing.record("runtime_body", time.monotonic() - 0.02, failed=True)
        event = json.loads(output.getvalue())
        self.assertEqual(event["trace_id"], TRACE.split("-")[1])
        self.assertEqual(event["part_number"], 632)
        self.assertTrue(event["failed"])
        self.assertGreaterEqual(event["duration_ms"], 20)
        self.assertNotIn("secret", output.getvalue())
        self.assertNotIn("private", output.getvalue())
        output.seek(0)
        output.truncate()
        UploadTiming("private", "632", headers).record("runtime_body", time.monotonic())
        UploadTiming(UPLOAD, "632\nsecret", headers).record("runtime_body", time.monotonic())
        self.assertEqual(output.getvalue(), "")

    def test_only_valid_w3c_context_is_relayed(self):
        self.assertEqual(
            relay_trace_headers({"traceparent": TRACE, "baggage": "secret"}), {"traceparent": TRACE}
        )
        self.assertEqual(relay_trace_headers({"traceparent": "invalid-secret"}), {})
