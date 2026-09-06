"""Tests for structured runtime logging."""

from __future__ import annotations

import io
import json
import logging
import unittest
from unittest.mock import patch

import jlogger as jlog

from xnobrain.logging_config import SERVICE_NAME, JLoggerHandler, configure_logging


class LoggingConfigTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        jlog.set_output(self.output)

    def tearDown(self):
        import sys
        jlog.set_output(sys.stderr)

    def test_configures_jlogger_json_with_service_context(self):
        with patch.dict("os.environ", {"DEVELOPMENT_ENVIRONMENT": "local", "SERVICE_NAME": "runtime-test"}):
            configure_logging()
            logging.getLogger("xnobrain.http").info(
                "HTTP request completed",
                extra={"http_method": "GET", "http_route": "/xnobrain/api/runtime/v1/health", "error": ""},
            )

        rendered = json.loads(self.output.getvalue())
        self.assertEqual(rendered["level"], "info")
        self.assertEqual(rendered["message"], "HTTP request completed")
        self.assertEqual(rendered["service_name"], "runtime-test")
        self.assertEqual(rendered["component"], "runtime-api")
        self.assertEqual(rendered["development_environment"], "local")
        self.assertEqual(rendered["http_method"], "GET")
        self.assertEqual(rendered["error"], "")

    def test_handler_does_not_copy_arbitrary_record_fields(self):
        handler = JLoggerHandler()
        record = logging.makeLogRecord({
            "name": "xnobrain.test",
            "levelno": logging.INFO,
            "levelname": "INFO",
            "msg": "safe",
            "authorization": "secret",
        })
        handler.emit(record)

        rendered = json.loads(self.output.getvalue())
        self.assertNotIn("authorization", rendered)

    def test_third_party_otel_warning_is_json_with_safe_error_category(self):
        with patch.dict("os.environ", {"DEVELOPMENT_ENVIRONMENT": "local"}, clear=True):
            configure_logging()
            logging.getLogger("opentelemetry.exporter.otlp.proto.grpc.exporter").warning(
                "Transient exporter failure with dependency details"
            )

        rendered = json.loads(self.output.getvalue())
        self.assertEqual(rendered["level"], "warning")
        self.assertEqual(rendered["component"], "runtime-api")
        self.assertEqual(rendered["error"], "telemetry_export_unavailable")



if __name__ == "__main__":
    unittest.main()
