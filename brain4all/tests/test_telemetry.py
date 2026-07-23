"""Telemetry configuration must remain explicitly opt-in and local-only."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from brain4all.telemetry import local_collector_endpoint, telemetry_enabled


class TelemetryConfigurationTests(unittest.TestCase):
    def test_telemetry_is_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(telemetry_enabled())

    def test_local_collector_is_the_only_accepted_export_target(self):
        with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel-collector:4317"}, clear=True):
            self.assertEqual(local_collector_endpoint(), "http://otel-collector:4317")
        with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": "https://telemetry.example.com"}, clear=True):
            self.assertIsNone(local_collector_endpoint())


if __name__ == "__main__":
    unittest.main()
