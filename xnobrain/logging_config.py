"""Structured JSON logging for the XNOBrain runtime service."""

from __future__ import annotations

import datetime
import json
import logging
import os
import sys
from typing import Any, TextIO

from opentelemetry import trace

SERVICE_NAME = "xnobrain-runtime-services"
DEVELOPMENT_ENVIRONMENT = "development"
_SAFE_RECORD_FIELDS = (
    "event",
    "action",
    "operation_id",
    "workspace_id",
    "agent_id",
    "run_id",
    "fence",
    "phase",
    "error_type",
    "reason_code",
    "storage_check",
    "storage_root",
    "relative_path",
    "data_preserved",
    "installed_identity_matches",
    "data_added",
    "data_removed",
    "data_changed",
    "duration_ms",
    "http_method",
    "http_route",
    "http_status_code",
    "trace_id",
    "span_id",
    "error",
)
_LEVEL_NAMES = {
    logging.DEBUG: "debug",
    logging.INFO: "info",
    logging.WARNING: "warning",
    logging.ERROR: "error",
    logging.CRITICAL: "critical",
}


def _json_default(value: Any) -> Any:
    if type(value).__module__.startswith("numpy") and hasattr(value, "tolist"):
        return value.tolist()
    return str(value)


class StructuredJsonFormatter(logging.Formatter):
    """Render one JSON line per record without copying sensitive payloads.

    The field names and order match the previous jlogger output so log
    pipelines keep parsing it: time, logger, level, message, service context,
    then the allow-listed record fields.
    """

    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "time": datetime.datetime.fromtimestamp(record.created).isoformat(),
            "logger": record.name,
            "level": _LEVEL_NAMES.get(record.levelno, "info"),
            "message": record.getMessage(),
            "development_environment": os.getenv(
                "DEVELOPMENT_ENVIRONMENT",
                DEVELOPMENT_ENVIRONMENT,
            ),
            "service_name": os.getenv("SERVICE_NAME", SERVICE_NAME),
            "component": "runtime-api",
        }
        safe_fields: dict[str, Any] = {
            field: getattr(record, field) for field in _SAFE_RECORD_FIELDS if hasattr(record, field)
        }
        span = trace.get_current_span().get_span_context()
        if span.is_valid:
            safe_fields.setdefault("trace_id", format(span.trace_id, "032x"))
            safe_fields.setdefault("span_id", format(span.span_id, "016x"))
        # Every Runtime record has the same nullable error field. Keep it
        # categorical: third-party exception messages can contain network
        # addresses or provider details and must not be copied as fields.
        if "error" not in safe_fields:
            if record.exc_info and record.exc_info[1]:
                safe_fields["error"] = record.exc_info[1].__class__.__name__
            elif record.levelno >= logging.ERROR:
                safe_fields["error"] = "logged_error"
            elif (
                record.name.startswith("opentelemetry.exporter")
                and record.levelno >= logging.WARNING
            ):
                safe_fields["error"] = "telemetry_export_unavailable"
            else:
                safe_fields["error"] = None
        line.update(safe_fields)
        return json.dumps(line, default=_json_default, ensure_ascii=False, separators=(",", ":"))


def structured_handler(stream: TextIO | None = None) -> logging.Handler:
    """Return a stream handler writing structured JSON lines (stderr by default)."""
    handler = logging.StreamHandler(sys.stderr if stream is None else stream)
    handler.setFormatter(StructuredJsonFormatter())
    return handler


def configure_logging(stream: TextIO | None = None) -> None:
    """Configure structured JSON output before the runtime serves."""
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        handlers=[structured_handler(stream)],
        force=True,
    )
