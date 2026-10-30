"""Tests for structured runtime logging."""

from __future__ import annotations

import datetime
import io
import json
import logging
import unittest
from unittest.mock import patch

from opentelemetry import trace

from xnobrain.logging_config import configure_logging, structured_handler


class LoggingConfigTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()

    def tearDown(self):
        logging.basicConfig(force=True)

    def test_configures_json_with_service_context(self):
        with patch.dict(
            "os.environ", {"DEVELOPMENT_ENVIRONMENT": "local", "SERVICE_NAME": "runtime-test"}
        ):
            configure_logging(self.output)
            logging.getLogger("xnobrain.http").info(
                "HTTP request completed",
                extra={
                    "http_method": "GET",
                    "http_route": "/xnobrain/api/runtime/v1/health",
                    "error": "",
                },
            )

        rendered = json.loads(self.output.getvalue())
        self.assertEqual(rendered["level"], "info")
        self.assertEqual(rendered["message"], "HTTP request completed")
        self.assertEqual(rendered["service_name"], "runtime-test")
        self.assertEqual(rendered["component"], "runtime-api")
        self.assertEqual(rendered["development_environment"], "local")
        self.assertEqual(rendered["http_method"], "GET")
        self.assertEqual(rendered["error"], "")

    def test_lifecycle_metadata_uses_active_span_without_secrets(self):
        span = trace.NonRecordingSpan(
            trace.SpanContext(
                trace_id=int("0123456789abcdef0123456789abcdef", 16),
                span_id=int("0123456789abcdef", 16),
                is_remote=False,
            )
        )
        record = logging.makeLogRecord(
            {
                "name": "xnobrain.rebalance",
                "levelno": logging.WARNING,
                "msg": "Rebalance failed",
                "operation_id": "reb_test",
                "error": "router_probe_failed",
                "error_type": "ConnectionError",
                "lease_token": "SECRET",
                "payload": "PRIVATE",
            }
        )
        with trace.use_span(span):
            structured_handler(self.output).emit(record)
        rendered = json.loads(self.output.getvalue())
        self.assertEqual(rendered["trace_id"], "0123456789abcdef0123456789abcdef")
        self.assertEqual(rendered["span_id"], "0123456789abcdef")
        self.assertEqual(rendered["operation_id"], "reb_test")
        self.assertNotIn("SECRET", self.output.getvalue())
        self.assertNotIn("PRIVATE", self.output.getvalue())

    def test_handler_does_not_copy_arbitrary_record_fields(self):
        handler = structured_handler(self.output)
        record = logging.makeLogRecord(
            {
                "name": "xnobrain.test",
                "levelno": logging.INFO,
                "levelname": "INFO",
                "msg": "safe",
                "authorization": "secret",
            }
        )
        handler.emit(record)

        rendered = json.loads(self.output.getvalue())
        self.assertNotIn("authorization", rendered)

    def test_checkpoint_diagnostics_survive_json_without_error_message_or_target(self):
        from xnobrain.integrations.runtime_update_storage import RuntimeUpdateStorageError
        from xnobrain.services.base import ServiceError
        from xnobrain.services.runtime_updates import RuntimeUpdateService

        error = RuntimeUpdateStorageError(
            "PRIVATE_EXCEPTION_MESSAGE",
            code="runtime_update_unsafe_data",
            diagnostics={
                "storage_check": "symlink_scope",
                "storage_root": "profiles",
                "relative_path": "agent/link",
                "target": "/PRIVATE_EXTERNAL_TARGET",
            },
        )
        with (
            self.assertLogs("xnobrain.services.runtime_updates", level="WARNING") as logs,
            self.assertRaises(ServiceError),
        ):
            RuntimeUpdateService._storage_error(
                error,
                request={
                    "operation_id": "upd_fixture",
                    "generation": 26,
                    "token": "SECRET",
                    "target": {"private": "PRIVATE_TARGET"},
                },
                phase="checkpoint",
            )
        structured_handler(self.output).emit(logs.records[0])
        rendered = json.loads(self.output.getvalue())
        self.assertEqual(rendered["event"], "runtime_update_storage_failure")
        self.assertEqual(rendered["error"], "runtime_update_unsafe_data")
        self.assertEqual(rendered["operation_id"], "upd_fixture")
        self.assertEqual(rendered["fence"], 26)
        self.assertEqual(rendered["storage_check"], "symlink_scope")
        self.assertEqual(rendered["storage_root"], "profiles")
        self.assertEqual(rendered["relative_path"], "agent/link")
        self.assertNotIn("PRIVATE", self.output.getvalue())
        self.assertNotIn("SECRET", self.output.getvalue())

    def test_verification_counts_survive_json_without_manifest_or_credentials(self):
        record = logging.makeLogRecord(
            {
                "name": "xnobrain.services.runtime_updates",
                "levelno": logging.WARNING,
                "msg": "Runtime update verification failed",
                "event": "runtime_update_verification_failure",
                "error": "runtime_update_data_not_preserved",
                "data_preserved": False,
                "installed_identity_matches": True,
                "data_added": 0,
                "data_removed": 0,
                "data_changed": 2,
                "manifest": {"path": "PRIVATE_FILE"},
                "access_token": "SECRET",
            }
        )
        structured_handler(self.output).emit(record)
        rendered = json.loads(self.output.getvalue())
        self.assertEqual(rendered["data_changed"], 2)
        self.assertFalse(rendered["data_preserved"])
        self.assertTrue(rendered["installed_identity_matches"])
        self.assertNotIn("PRIVATE", self.output.getvalue())
        self.assertNotIn("SECRET", self.output.getvalue())

    def test_line_layout_matches_previous_log_contract(self):
        record = logging.makeLogRecord(
            {
                "name": "xnobrain.test",
                "levelno": logging.INFO,
                "msg": "Xin chào %s",
                "args": ("runtime",),
                "operation_id": "op_1",
            }
        )
        structured_handler(self.output).emit(record)

        lines = self.output.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("Xin chào runtime", lines[0])
        rendered = json.loads(lines[0])
        self.assertEqual(
            list(rendered),
            [
                "time",
                "logger",
                "level",
                "message",
                "development_environment",
                "service_name",
                "component",
                "operation_id",
                "error",
            ],
        )
        self.assertEqual(rendered["logger"], "xnobrain.test")
        self.assertIsNone(rendered["error"])
        datetime.datetime.fromisoformat(rendered["time"])

    def test_third_party_otel_warning_is_json_with_safe_error_category(self):
        with patch.dict("os.environ", {"DEVELOPMENT_ENVIRONMENT": "local"}, clear=True):
            configure_logging(self.output)
            logging.getLogger("opentelemetry.exporter.otlp.proto.grpc.exporter").warning(
                "Transient exporter failure with dependency details"
            )

        rendered = json.loads(self.output.getvalue())
        self.assertEqual(rendered["level"], "warning")
        self.assertEqual(rendered["component"], "runtime-api")
        self.assertEqual(rendered["error"], "telemetry_export_unavailable")


if __name__ == "__main__":
    unittest.main()
