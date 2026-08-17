"""Read-only usage aggregation over Hermes and OmniRoute SQLite ledgers.

This adapter never writes runtime state. It opens SQLite files with ``?mode=ro``:
profile ``state.db`` files provide current-agent attribution, while OmniRoute's
``usageHistory`` is the durable workspace ledger that survives conversation and
agent deletion. It holds no policy and does no HTTP.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from pathlib import Path
from typing import Any


# strftime formats per granularity (UTC). ``week`` is handled by the service:
# it asks for ``day`` grain here and folds into ISO weeks in Python.
_BUCKET_FMT = {"hour": "%Y-%m-%dT%H", "day": "%Y-%m-%d", "month": "%Y-%m"}
_ROUTER_PREFIXES = {
    "claude": "cc",
    "codex": "cx",
    "antigravity": "ag",
    "openai": "openai",
    "anthropic": "anthropic",
    "gemini": "gemini",
    "opencode-go": "ocg",
    "opencode": "oc",
}
_PROVIDER_BY_PREFIX = {
    "cc": "claude",
    "cx": "codex",
    "ag": "antigravity",
    "oc": "opencode",
    "ocg": "opencode-go",
    "ocz": "opencode",
}


def _open_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    return conn


def aggregate_profile(
    profile_dir: Path,
    *,
    start_epoch: float,
    end_epoch: float,
    bucket: str = "day",
) -> dict[str, Any]:
    """Return ``{"totals", "by_model", "series"}`` for one profile's ``state.db``.

    ``bucket`` is one of ``hour|day|month`` (the service folds ``week`` -> ``day``).
    ``fmt`` comes from a fixed dict, never user input, so interpolating it is safe.
    Every query is bounded by the half-open ``(start, end]`` window. Any missing
    column, lock, or corruption degrades to zeroes rather than raising.
    """
    fmt = _BUCKET_FMT.get(bucket, _BUCKET_FMT["day"])
    win = (start_epoch, end_epoch)
    empty: dict[str, Any] = {"totals": _zero_totals(), "by_model": [], "series": []}
    db = profile_dir / "state.db"
    if not db.is_file():
        return empty
    try:
        conn = _open_ro(db)
    except sqlite3.Error:
        return empty
    try:
        totals = dict(conn.execute(
            """
            SELECT COALESCE(SUM(input_tokens),0)        AS input_tokens,
                   COALESCE(SUM(output_tokens),0)       AS output_tokens,
                   COALESCE(SUM(cache_read_tokens),0)   AS cache_read_tokens,
                   COALESCE(SUM(cache_write_tokens),0)  AS cache_write_tokens,
                   COALESCE(SUM(reasoning_tokens),0)    AS reasoning_tokens,
                   COALESCE(SUM(estimated_cost_usd),0)  AS estimated_cost_usd,
                   COALESCE(SUM(actual_cost_usd),0)     AS actual_cost_usd,
                   COUNT(*)                             AS sessions,
                   COALESCE(SUM(api_call_count),0)      AS api_calls
            FROM sessions WHERE started_at > ? AND started_at <= ?
            """,
            win,
        ).fetchone())
        by_model = [dict(row) for row in conn.execute(
            """
            SELECT COALESCE(model,'unknown')            AS model,
                   COALESCE(billing_provider,'')        AS provider,
                   COALESCE(SUM(input_tokens),0)        AS input_tokens,
                   COALESCE(SUM(output_tokens),0)       AS output_tokens,
                   COALESCE(SUM(estimated_cost_usd),0)  AS estimated_cost_usd,
                   COALESCE(SUM(actual_cost_usd),0)     AS actual_cost_usd,
                   COUNT(*)                             AS sessions
            FROM sessions WHERE started_at > ? AND started_at <= ?
            GROUP BY model, billing_provider
            """,
            win,
        ).fetchall()]
        series = [dict(row) for row in conn.execute(
            f"""
            SELECT strftime('{fmt}', started_at, 'unixepoch') AS bucket,
                   COALESCE(SUM(input_tokens),0)        AS input_tokens,
                   COALESCE(SUM(output_tokens),0)       AS output_tokens,
                   COALESCE(SUM(estimated_cost_usd),0)  AS estimated_cost_usd,
                   COALESCE(SUM(actual_cost_usd),0)     AS actual_cost_usd,
                   COUNT(*)                             AS sessions
            FROM sessions WHERE started_at > ? AND started_at <= ?
            GROUP BY bucket ORDER BY bucket
            """,
            win,
        ).fetchall()]
        return {"totals": totals, "by_model": by_model, "series": series}
    except sqlite3.Error:
        return empty
    finally:
        conn.close()


def aggregate_router_usage(
    data_dir: Path,
    *,
    start_epoch: float,
    end_epoch: float,
    bucket: str = "day",
) -> dict[str, Any]:
    """Aggregate OmniRoute's durable ``usageHistory`` ledger.

    The table is owned by OmniRoute and contains no XNOBrain agent identifier.
    Consequently this function deliberately returns workspace totals, provider
    and model breakdowns, and request status only; agent attribution continues
    to come from profile databases.
    """
    empty: dict[str, Any] = {
        "available": False,
        "totals": _zero_totals(),
        "by_model": [],
        "by_provider": [],
        "series": [],
        "request_status": {
            "total": 0, "successful": 0, "failed": 0, "success_rate": 0.0,
        },
    }
    db = data_dir / "db" / "data.sqlite"
    if not db.is_file():
        return empty
    try:
        conn = _open_ro(db)
    except sqlite3.Error:
        return empty
    start_iso = _epoch_iso(start_epoch)
    end_iso = _epoch_iso(end_epoch)
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='usageHistory'"
        ).fetchone()
        if exists is None:
            return empty
        node_prefixes = _provider_node_prefixes(conn)
        rows = conn.execute(
            """
            SELECT timestamp, COALESCE(provider,'unknown') AS provider,
                   COALESCE(model,'unknown') AS model,
                   COALESCE(promptTokens,0) AS prompt_tokens,
                   COALESCE(completionTokens,0) AS completion_tokens,
                   COALESCE(cost,0) AS cost,
                   COALESCE(status,'') AS status,
                   COALESCE(tokens,'') AS tokens
            FROM usageHistory
            WHERE timestamp > ? AND timestamp <= ?
            """,
            (start_iso, end_iso),
        ).fetchall()
    except sqlite3.Error:
        return empty
    finally:
        conn.close()

    totals = _zero_totals()
    models: dict[tuple[str, str], dict[str, Any]] = {}
    providers: dict[str, dict[str, Any]] = {}
    series: dict[str, dict[str, Any]] = {}
    successful = 0
    for row in rows:
        input_tokens = int(row["prompt_tokens"] or 0)
        output_tokens = int(row["completion_tokens"] or 0)
        cost = float(row["cost"] or 0)
        token_meta = _token_metadata(str(row["tokens"] or ""))
        cache_read = int(
            token_meta.get("cachedTokens")
            or token_meta.get("cached_tokens")
            or token_meta.get("cache_read_tokens")
            or 0
        )
        cache_write = int(
            token_meta.get("cacheCreationTokens")
            or token_meta.get("cache_creation_tokens")
            or token_meta.get("cache_creation_input_tokens")
            or token_meta.get("cache_write_tokens")
            or 0
        )
        reasoning = int(
            token_meta.get("reasoningTokens")
            or token_meta.get("reasoning_tokens")
            or 0
        )
        totals["input_tokens"] += input_tokens
        totals["output_tokens"] += output_tokens
        totals["cache_read_tokens"] += cache_read
        totals["cache_write_tokens"] += cache_write
        totals["reasoning_tokens"] += reasoning
        totals["estimated_cost_usd"] += cost
        totals["api_calls"] += 1

        raw_provider = str(row["provider"] or "unknown")
        prefix = node_prefixes.get(raw_provider) or _ROUTER_PREFIXES.get(raw_provider, "")
        provider = _PROVIDER_BY_PREFIX.get(prefix, prefix or raw_provider)
        model = _qualified_router_model(str(row["model"] or "unknown"), prefix)
        model_row = models.setdefault(
            (model, provider),
            {
                "model": model, "provider": provider, "input_tokens": 0,
                "output_tokens": 0, "estimated_cost_usd": 0.0,
                "actual_cost_usd": 0.0, "sessions": 0,
            },
        )
        _add_router_row(model_row, input_tokens, output_tokens, cost)
        provider_row = providers.setdefault(
            provider,
            {
                "provider": provider, "input_tokens": 0, "output_tokens": 0,
                "estimated_cost_usd": 0.0, "actual_cost_usd": 0.0,
                "sessions": 0,
            },
        )
        _add_router_row(provider_row, input_tokens, output_tokens, cost)

        label = _timestamp_bucket(str(row["timestamp"] or ""), bucket)
        if label:
            bucket_row = series.setdefault(
                label,
                {
                    "bucket": label, "input_tokens": 0, "output_tokens": 0,
                    "estimated_cost_usd": 0.0, "actual_cost_usd": 0.0,
                    "sessions": 0,
                },
            )
            _add_router_row(bucket_row, input_tokens, output_tokens, cost)

        if _successful_status(str(row["status"] or "")):
            successful += 1

    request_total = len(rows)
    failed = request_total - successful
    return {
        "available": True,
        "totals": totals,
        "by_model": sorted(
            models.values(),
            key=lambda item: -(item["input_tokens"] + item["output_tokens"]),
        ),
        "by_provider": sorted(
            providers.values(),
            key=lambda item: -(item["input_tokens"] + item["output_tokens"]),
        ),
        "series": [series[key] for key in sorted(series)],
        "request_status": {
            "total": request_total,
            "successful": successful,
            "failed": failed,
            "success_rate": round(successful / request_total * 100, 1)
            if request_total else 0.0,
        },
    }


def period_spend(profile_dir: Path, *, since_epoch: float, cost_basis: str) -> float:
    """Single read-only SUM of the chosen cost column since ``since_epoch``.

    Used for advisory budget evaluation. Returns 0.0 on any error.
    """
    column = "actual_cost_usd" if cost_basis == "actual" else "estimated_cost_usd"
    db = profile_dir / "state.db"
    if not db.is_file():
        return 0.0
    try:
        conn = _open_ro(db)
    except sqlite3.Error:
        return 0.0
    try:
        row = conn.execute(
            f"SELECT COALESCE(SUM({column}),0) AS spend FROM sessions WHERE started_at > ?",
            (since_epoch,),
        ).fetchone()
        return float(row["spend"] if row is not None else 0.0)
    except sqlite3.Error:
        return 0.0
    finally:
        conn.close()


def _zero_totals() -> dict[str, Any]:
    return {
        "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
        "cache_write_tokens": 0, "reasoning_tokens": 0, "estimated_cost_usd": 0.0,
        "actual_cost_usd": 0.0, "sessions": 0, "api_calls": 0,
    }


def _epoch_iso(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _token_metadata(value: str) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _provider_node_prefixes(conn: sqlite3.Connection) -> dict[str, str]:
    """Map OmniRoute's UUID-backed custom provider IDs to stable prefixes."""
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='providerNodes'"
        ).fetchone()
        if exists is None:
            return {}
        rows = conn.execute("SELECT id, data FROM providerNodes").fetchall()
    except sqlite3.Error:
        return {}
    result: dict[str, str] = {}
    for row in rows:
        data = _token_metadata(str(row["data"] or ""))
        prefix = str(data.get("prefix") or "").strip()
        if prefix:
            result[str(row["id"] or "")] = prefix
    return result


def _qualified_router_model(model: str, prefix: str) -> str:
    normalized = model.strip() or "unknown"
    if not prefix or "/" in normalized or normalized == "unknown":
        return normalized
    return f"{prefix}/{normalized}"


def _timestamp_bucket(value: str, bucket: str) -> str:
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    stamp = stamp.astimezone(timezone.utc)
    if bucket == "hour":
        return stamp.strftime("%Y-%m-%dT%H")
    if bucket == "month":
        return stamp.strftime("%Y-%m")
    return stamp.strftime("%Y-%m-%d")


def _add_router_row(
    row: dict[str, Any], input_tokens: int, output_tokens: int, cost: float,
) -> None:
    row["input_tokens"] += input_tokens
    row["output_tokens"] += output_tokens
    row["estimated_cost_usd"] += cost
    # The existing merge helpers use ``sessions`` as the count field. For
    # OmniRoute rows it represents requests; the public UI labels it accordingly.
    row["sessions"] += 1


def _successful_status(value: str) -> bool:
    normalized = value.strip().lower()
    if not normalized:
        return True
    return normalized in {"ok", "success", "successful", "completed", "complete", "200"}
