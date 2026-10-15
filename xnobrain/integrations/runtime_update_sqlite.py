"""Bounded, read-only SQLite evidence for a drained Runtime checkpoint.

Transactions include committed WAL pages. Physical page order, free pages and
SHM locks are not user data; schema, row identities and typed values are.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()


def _value(value):
    if isinstance(value, bytes):
        return ["blob", value.hex()]
    if isinstance(value, float):
        return ["float", value.hex()]
    return [type(value).__name__, value]


def snapshot(path: Path, *, max_bytes: int, hermes_state: bool = False) -> dict:
    """Return hashes only; fail closed on corrupt, busy or oversized databases."""
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    deadline = time.monotonic() + 30
    connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        pages = connection.execute("PRAGMA page_count").fetchone()[0]
        page_size = connection.execute("PRAGMA page_size").fetchone()[0]
        if pages * page_size > max_bytes:
            raise ValueError("SQLite evidence exceeds limit")
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise ValueError("SQLite integrity check failed")
        schema = connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name"
        ).fetchall()
        result = {
            "schema": _hash(schema),
            "user_version": connection.execute("PRAGMA user_version").fetchone()[0],
            "application_id": connection.execute("PRAGMA application_id").fetchone()[0],
            "tables": {},
        }
        budget = 0
        for kind, name, _, _sql in schema:
            if kind != "table":
                continue
            quoted = '"' + name.replace('"', '""') + '"'
            info = connection.execute(f"PRAGMA table_info({quoted})").fetchall()
            columns = [row[1] for row in info]
            marker_table = (
                hermes_state
                and name == "state_meta"
                and columns == ["key", "value"]
                and info[0][2].upper() == "TEXT"
                and info[0][5] == 1
                and info[1][2].upper() == "TEXT"
                and info[1][5] == 0
            )
            # Preserve implicit row identities too. WITHOUT ROWID/virtual tables
            # lacking rowid are covered by their explicit columns and schema.
            rowid = next((key for key in ("rowid", "_rowid_", "oid") if key not in columns), None)
            try:
                cursor = connection.execute(f"SELECT {rowid}, * FROM {quoted}") if rowid else None
            except sqlite3.OperationalError:
                cursor = None
            has_rowid = cursor is not None
            if cursor is None:
                cursor = connection.execute(f"SELECT * FROM {quoted}")
            hashes = []
            for row in cursor:
                values = row[1:] if has_rowid else row
                # One reviewed Hermes initialization marker, recorded separately.
                # Comparison permits only absent -> '1', never arbitrary edits.
                if marker_table and values[0] == "fts_storage_version":
                    if "fts_storage_version" in result:
                        raise ValueError("duplicate SQLite initialization marker")
                    result["fts_storage_version"] = _hash([_value(v) for v in values])
                    continue
                encoded = [_value(value) for value in row]
                budget += len(json.dumps(encoded)) + 64
                if budget > max_bytes or time.monotonic() > deadline:
                    raise ValueError("SQLite evidence exceeds limit")
                hashes.append(_hash(encoded))
            result["tables"][name] = _hash(sorted(hashes))
        return result
    finally:
        connection.close()


def preserved(before: dict, after: dict) -> bool:
    if before == after:
        return True
    marker = "fts_storage_version"
    return (
        marker not in before
        and after.get(marker) == _hash([_value(marker), _value("1")])
        and before == {key: value for key, value in after.items() if key != marker}
    )
