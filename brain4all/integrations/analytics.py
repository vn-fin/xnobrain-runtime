"""Read-only usage aggregation over each agent's Hermes ``state.db``.

This adapter never writes Hermes state. It opens each profile's ``state.db`` with
``?mode=ro`` and aggregates the accounting columns Brain4All's own schema
guarantees (see ``brain4all/integrations/hermes.py`` ``_ensure_session_schema``).
It holds no policy and does no HTTP.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


# strftime formats per granularity (UTC). ``week`` is handled by the service:
# it asks for ``day`` grain here and folds into ISO weeks in Python.
_BUCKET_FMT = {"hour": "%Y-%m-%dT%H", "day": "%Y-%m-%d", "month": "%Y-%m"}


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
