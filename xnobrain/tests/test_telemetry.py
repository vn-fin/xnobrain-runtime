"""Telemetry configuration remains explicit and collector-independent."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from xnobrain.telemetry import collector_endpoint, telemetry_enabled


class TelemetryConfigurationTests(unittest.TestCase):
    def test_telemetry_is_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(telemetry_enabled())

    def test_configured_collector_endpoint_is_accepted(self):
        with patch.dict(
            os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel-collector:4317"}, clear=True
        ):
            self.assertEqual(collector_endpoint(), "http://otel-collector:4317")
        with patch.dict(
            os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": "https://telemetry.example.com"}, clear=True
        ):
            self.assertEqual(collector_endpoint(), "https://telemetry.example.com")


if __name__ == "__main__":
    unittest.main()
