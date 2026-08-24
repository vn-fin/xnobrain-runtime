"""Read-only usage aggregation over Hermes profile ledgers.

This adapter never writes runtime state. It opens only profile ``state.db``
files with ``?mode=ro``. Central router usage and organization attribution are
owned by Control and the router's PostgreSQL store, not by Runtime.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
import sqlite3
from pathlib import Path
from typing import Any


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
    Series bucket timestamps are RFC3339 values at the start of each bucket in
    UTC.
    Every query is bounded by the half-open ``(start, end]`` window. Any missing
    column, lock, or corruption degrades to zeroes rather than raising.
    """
    zone = timezone.utc
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
        series: dict[str, dict[str, Any]] = {}
        for row in conn.execute(
            """
            SELECT started_at,
                   COALESCE(input_tokens,0)       AS input_tokens,
                   COALESCE(output_tokens,0)      AS output_tokens,
                   COALESCE(estimated_cost_usd,0) AS estimated_cost_usd,
                   COALESCE(actual_cost_usd,0)    AS actual_cost_usd
            FROM sessions WHERE started_at > ? AND started_at <= ?
            """,
            win,
        ).fetchall():
            label = epoch_bucket(float(row["started_at"]), bucket, zone)
            bucket_row = series.setdefault(
                label,
                {
                    "bucket": label, "input_tokens": 0, "output_tokens": 0,
                    "estimated_cost_usd": 0.0, "actual_cost_usd": 0.0,
                    "sessions": 0,
                },
            )
            bucket_row["input_tokens"] += int(row["input_tokens"] or 0)
            bucket_row["output_tokens"] += int(row["output_tokens"] or 0)
            bucket_row["estimated_cost_usd"] += float(row["estimated_cost_usd"] or 0)
            bucket_row["actual_cost_usd"] += float(row["actual_cost_usd"] or 0)
            bucket_row["sessions"] += 1
        series_rows = [series[key] for key in sorted(series)]
        return {"totals": totals, "by_model": by_model, "series": series_rows}
    except sqlite3.Error:
        return empty
    finally:
        conn.close()


def period_spend(
    profile_dir: Path, *, since_epoch: float, until_epoch: float, cost_basis: str,
) -> float:
    """Single read-only SUM of the chosen cost column for an inclusive window.

    Used for budget evaluation. Returns 0.0 on any error.
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
            f"SELECT COALESCE(SUM({column}),0) AS spend "
            "FROM sessions WHERE started_at >= ? AND started_at <= ?",
            (since_epoch, until_epoch),
        ).fetchone()
        return float(row["spend"] if row is not None else 0.0)
    except sqlite3.Error:
        return 0.0
    finally:
        conn.close()


def profile_model_usage(
    profile_dir: Path, *, since_epoch: float, until_epoch: float,
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    """Read detailed per-model usage used for agent cost attribution."""
    db = profile_dir / "state.db"
    if not db.is_file():
        return []
    try:
        conn = _open_ro(db)
    except sqlite3.Error:
        return []
    try:
        if _table_exists(conn, "session_model_usage"):
            return [dict(row) for row in conn.execute(
                """
                SELECT COALESCE(u.model,'unknown') AS model,
                       COALESCE(SUM(u.input_tokens),0) AS input_tokens,
                       COALESCE(SUM(u.output_tokens),0) AS output_tokens,
                       COALESCE(SUM(u.cache_read_tokens),0) AS cache_read_tokens,
                       COALESCE(SUM(u.cache_write_tokens),0) AS cache_write_tokens,
                       COALESCE(SUM(u.reasoning_tokens),0) AS reasoning_tokens,
                       COALESCE(SUM(u.estimated_cost_usd),0) AS estimated_cost_usd,
                       COALESCE(SUM(u.actual_cost_usd),0) AS actual_cost_usd
                FROM session_model_usage u
                JOIN sessions s ON s.id = u.session_id
                WHERE COALESCE(u.last_seen, s.started_at) > ?
                  AND COALESCE(u.first_seen, s.started_at) <= ?
                  AND (? IS NULL OR u.session_id = ?)
                GROUP BY u.model
                """,
                (since_epoch, until_epoch, session_id, session_id),
            ).fetchall()]
        return [dict(row) for row in conn.execute(
            """
            SELECT COALESCE(model,'unknown') AS model,
                   COALESCE(SUM(input_tokens),0) AS input_tokens,
                   COALESCE(SUM(output_tokens),0) AS output_tokens,
                   COALESCE(SUM(cache_read_tokens),0) AS cache_read_tokens,
                   COALESCE(SUM(cache_write_tokens),0) AS cache_write_tokens,
                   COALESCE(SUM(reasoning_tokens),0) AS reasoning_tokens,
                   COALESCE(SUM(estimated_cost_usd),0) AS estimated_cost_usd,
                   COALESCE(SUM(actual_cost_usd),0) AS actual_cost_usd
            FROM sessions WHERE started_at > ? AND started_at <= ?
              AND (? IS NULL OR id = ?)
            GROUP BY model
            """,
            (since_epoch, until_epoch, session_id, session_id),
        ).fetchall()]
    except sqlite3.Error:
        return []
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


def bucket_start_iso(value: datetime, bucket: str, zone: tzinfo) -> str:
    local = value.astimezone(zone)
    if bucket == "hour":
        local = datetime(
            local.year, local.month, local.day, local.hour,
            tzinfo=local.tzinfo, fold=local.fold,
        )
    elif bucket == "month":
        local = datetime(local.year, local.month, 1, tzinfo=local.tzinfo)
    elif bucket == "week":
        local = local - timedelta(days=local.weekday())
        local = datetime(local.year, local.month, local.day, tzinfo=local.tzinfo)
    else:
        local = datetime(local.year, local.month, local.day, tzinfo=local.tzinfo)
    return local.isoformat().replace("+00:00", "Z")


def epoch_bucket(value: float, bucket: str, zone: tzinfo) -> str:
    return bucket_start_iso(datetime.fromtimestamp(value, tz=timezone.utc), bucket, zone)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None
