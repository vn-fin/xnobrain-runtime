"""JSON logging for the local runtime process."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per log line with stable runtime metadata."""

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        try:
            parsed = json.loads(message)
        except (TypeError, json.JSONDecodeError):
            parsed = None

        payload = parsed if isinstance(parsed, dict) else {"message": message}
        payload["time"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        payload["development_environment"] = os.getenv("DEVELOPMENT_ENVIRONMENT", "dev")
        payload["service_name"] = os.getenv("SERVICE_NAME", "runtime")
        payload.setdefault("level", record.levelname.lower())
        payload.setdefault("logger", record.name)
        return json.dumps(payload, separators=(",", ":"), default=str)


def configure_logging() -> None:
    """Configure the root logger before the local runtime begins serving."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        handlers=[handler],
        force=True,
    )
