"""Structured JSON logging for the XNOBrain runtime service."""

from __future__ import annotations

import logging
import os
from typing import Any

import jlogger as jlog


SERVICE_NAME = "xnobrain-runtime-services"
DEVELOPMENT_ENVIRONMENT = "development"
_SAFE_RECORD_FIELDS = (
    "duration_ms",
    "http_method",
    "http_route",
    "http_status_code",
    "trace_id",
    "span_id",
    "error",
)


class JLoggerHandler(logging.Handler):
    """Forward stdlib records to jlogger without copying sensitive payloads."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            logger = jlog.get_logger(record.name).with_values(
                development_environment=os.getenv(
                    "DEVELOPMENT_ENVIRONMENT",
                    DEVELOPMENT_ENVIRONMENT,
                ),
                service_name=os.getenv("SERVICE_NAME", SERVICE_NAME),
                component="runtime-api",
            )
            event_factory = {
                logging.DEBUG: logger.debug,
                logging.INFO: logger.info,
                logging.WARNING: logger.warning,
                logging.ERROR: logger.error,
                logging.CRITICAL: logger.critical,
            }.get(record.levelno, logger.info)
            event = event_factory()
            safe_fields: dict[str, Any] = {
                field: getattr(record, field)
                for field in _SAFE_RECORD_FIELDS
                if hasattr(record, field)
            }
            # Every Runtime record has the same nullable error field. Keep it
            # categorical: third-party exception messages can contain network
            # addresses or provider details and must not be copied as fields.
            if "error" not in safe_fields:
                if record.exc_info and record.exc_info[1]:
                    safe_fields["error"] = record.exc_info[1].__class__.__name__
                elif record.levelno >= logging.ERROR:
                    safe_fields["error"] = "logged_error"
                elif record.name.startswith("opentelemetry.exporter") and record.levelno >= logging.WARNING:
                    safe_fields["error"] = "telemetry_export_unavailable"
                else:
                    safe_fields["error"] = None
            event.with_dict(safe_fields)
            event.msg(record.getMessage())
        except Exception:
            self.handleError(record)


def configure_logging() -> None:
    """Configure jlogger-backed JSON output before the runtime serves."""
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    jlog.set_level(level)
    jlog.set_time_format(jlog.TimeFormat.RFC3339)
    logging.basicConfig(
        level=level,
        handlers=[JLoggerHandler()],
        force=True,
    )
