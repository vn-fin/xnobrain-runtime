"""Shared, side-effect-free query parameter parsing."""

import time
from datetime import datetime, timezone
from typing import Any


def csv(value: Any) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _clamp_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, parsed))


def _parse_epoch(value: Any) -> float:
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()


def time_range(query: Any) -> dict[str, float]:
    start_value, end_value = query.get("from"), query.get("to")
    now = time.time()
    if start_value or end_value:
        start = _parse_epoch(start_value) if start_value else now - 30 * 86400
        end = _parse_epoch(end_value) if end_value else now
    else:
        days = _clamp_int(query.get("days"), 30, 1, 366)
        end, start = now, now - days * 86400
    if not start < end:
        raise ValueError("range start must be before end")
    return {"start_epoch": start, "end_epoch": end}


def skill_usage_filters(query: Any) -> dict[str, Any]:
    """Parse additive skill-usage context and cursor pagination filters."""
    return {
        "work_context_id": str(query.get("context") or "").strip() or None,
        "cursor": str(query.get("cursor") or "").strip() or None,
        "limit": _clamp_int(query.get("limit"), 100, 1, 100),
    }


def bucket(query: Any) -> str:
    value = str(query.get("bucket") or "day")
    return value if value in {"hour", "day", "week", "month"} else "day"
