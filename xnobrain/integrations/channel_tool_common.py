"""Shared, content-safe helpers for run-scoped channel tools.

The helpers here are pure: they never read files, call the network, or log.
They shape stored external messages for model-facing tool results and enforce
the channel-thread scope shared by the Zalo tool modules.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

MAX_HISTORY_LIMIT = 200
DEFAULT_RETENTION_DAYS = 30
MIN_RETENTION_DAYS = 1
MAX_RETENTION_DAYS = 365

_TRUST = "untrusted_external"


def error(code: str, **extra: Any) -> str:
    """Return the shared tool error envelope."""
    payload = {"success": False, "error": {"code": code, **extra}}
    return json.dumps(payload, ensure_ascii=False)


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)


def _timestamp(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _external_message(record: Mapping[str, Any], text_limit: int) -> dict[str, Any]:
    text = _text(record.get("text"))
    if text_limit >= 0 and len(text) > text_limit:
        text = text[:text_limit]
    message = {
        "trust": _TRUST,
        "message_id": _text(record.get("message_id")),
        "sender_id": _text(record.get("sender_id")),
        "sender_name": _text(record.get("sender_name")),
        "text": text,
        "ts": _timestamp(record.get("ts")),
        "type": _text(record.get("type")),
        "direction": _text(record.get("direction")),
    }
    return message


def _encoded_size(messages: list[dict[str, Any]]) -> int:
    return len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))


def external_messages(
    records: Iterable[Mapping[str, Any]] | None,
    *,
    limit: int = MAX_HISTORY_LIMIT,
    text_limit: int = 2000,
    max_bytes: int = 64 * 1024,
) -> list[dict[str, Any]]:
    """Shape stored messages as untrusted external data with hard caps.

    Records are expected oldest-first, so the newest ``limit`` entries are kept
    and the oldest entries are dropped first when the encoded result exceeds
    ``max_bytes``.
    """
    if not records or limit <= 0:
        return []
    selected = list(records)[-int(limit) :]
    messages = [_external_message(record, text_limit) for record in selected]
    while messages and _encoded_size(messages) > max_bytes:
        messages.pop(0)
    return messages


def channel_origin(
    repository: Any,
    profile: Any,
    conversation_id: str,
) -> tuple[str, str] | None:
    """Return the platform/thread bound to a channel conversation, if any."""
    conversation = str(conversation_id or "")
    if not conversation:
        return None
    threads = repository.threads(profile)
    for platform, records in threads.items():
        for thread_id, record in records.items():
            if str(record.get("conversation_id") or "") == conversation:
                return str(platform), str(thread_id)
    return None


def ensure_thread_in_scope(
    origin: tuple[str, str] | None,
    platform: str,
    thread_id: str,
) -> str | None:
    """Return an error code when a channel-originated run reads another thread."""
    if origin is None:
        return None
    if (str(platform), str(thread_id)) != origin:
        return "channel_thread_out_of_scope"
    return None
