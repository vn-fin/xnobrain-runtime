"""Durable bindings between native tasks and owner-routed team executions."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .kanban_support import _module

TERMINAL = frozenset({"completed", "failed", "cancelled"})


def ensure_schema(conn: Any) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS xnobrain_personal_task_executions (
            task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
            team_id TEXT NOT NULL,
            team_revision TEXT NOT NULL,
            source_revision TEXT,
            admission_key TEXT,
            execution_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            cancel_requested INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            receipt TEXT,
            creation_complete INTEGER NOT NULL DEFAULT 1,
            creation_intent TEXT,
            updated_at INTEGER NOT NULL DEFAULT (unixepoch())
        )
    """)
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(xnobrain_personal_task_executions)")
    }
    for name, declaration in (
        ("creation_complete", "INTEGER NOT NULL DEFAULT 1"),
        ("creation_intent", "TEXT"),
    ):
        if name not in columns:
            conn.execute(
                f"ALTER TABLE xnobrain_personal_task_executions ADD COLUMN {name} {declaration}"
            )


def binding(conn: Any, task_id: str) -> dict[str, Any] | None:
    ensure_schema(conn)
    row = conn.execute(
        "SELECT * FROM xnobrain_personal_task_executions WHERE task_id = ?", (task_id,)
    ).fetchone()
    return dict(row) if row is not None else None


def bind(
    conn: Any, task_id: str, team_id: str, team_revision: str, *, creation_intent=None
) -> None:
    ensure_schema(conn)
    with _module().write_txn(conn, allow_nested=True):
        current = binding(conn, task_id)
        if current is not None:
            if (current["team_id"], current["team_revision"]) != (team_id, team_revision):
                raise ValueError("task is already bound to a different team definition")
            return
        conn.execute(
            "INSERT INTO xnobrain_personal_task_executions "
            "(task_id, team_id, team_revision, creation_complete, creation_intent) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                task_id,
                team_id,
                team_revision,
                int(creation_intent is None),
                json.dumps(creation_intent) if creation_intent is not None else None,
            ),
        )


def finish_creation(conn: Any, task_id: str) -> None:
    with _module().write_txn(conn):
        conn.execute(
            "UPDATE xnobrain_personal_task_executions SET creation_complete = 1, "
            "creation_intent = NULL, updated_at = unixepoch() WHERE task_id = ?",
            (task_id,),
        )


def copy_binding(conn: Any, template_id: str, occurrence_id: str) -> None:
    current = binding(conn, template_id)
    if current is not None:
        bind(conn, occurrence_id, current["team_id"], current["team_revision"])


def prepare(conn: Any, task_id: str, board: str, revision: str) -> dict[str, Any]:
    """Persist a stable admission identity before any remote request."""
    key = hashlib.sha256(f"personal-task:{board}:{task_id}".encode()).hexdigest()
    with _module().write_txn(conn):
        conn.execute(
            "UPDATE xnobrain_personal_task_executions "
            "SET source_revision = ?, admission_key = ?, updated_at = unixepoch() "
            "WHERE task_id = ? AND admission_key IS NULL",
            (revision, key, task_id),
        )
    return binding(conn, task_id)


def request_cancel(conn: Any, task_id: str) -> None:
    with _module().write_txn(conn):
        conn.execute(
            "UPDATE xnobrain_personal_task_executions "
            "SET cancel_requested = 1, updated_at = unixepoch() WHERE task_id = ?",
            (task_id,),
        )


def record(conn: Any, task_id: str, receipt: dict[str, Any]) -> None:
    """Store the already redacted public receipt; never the execution capability."""
    safe = {
        key: receipt[key]
        for key in (
            "id",
            "status",
            "revision",
            "steps",
            "synthesis",
            "orchestrator",
            "orchestrator_summary",
            "result",
        )
        if key in receipt
    }
    encoded = json.dumps(safe, ensure_ascii=False)
    if len(encoded.encode()) > 1_000_000:
        raise ValueError("execution receipt exceeds its bound")
    with _module().write_txn(conn):
        current = binding(conn, task_id)
        if current is None:
            raise ValueError("task execution binding is missing")
        if current["execution_id"] and current["execution_id"] != receipt["id"]:
            raise ValueError("task execution identity changed")
        previous = json.loads(current["receipt"] or "{}")
        if (current["status"] in TERMINAL and receipt["status"] not in TERMINAL) or int(
            previous.get("revision", 0)
        ) > int(receipt.get("revision", 0)):
            return
        conn.execute(
            "UPDATE xnobrain_personal_task_executions "
            "SET execution_id = ?, status = ?, receipt = ?, error = NULL, "
            "updated_at = unixepoch() WHERE task_id = ?",
            (receipt["id"], receipt["status"], encoded, task_id),
        )


def record_error(conn: Any, task_id: str, *, denied: bool = False) -> None:
    with _module().write_txn(conn):
        conn.execute(
            "UPDATE xnobrain_personal_task_executions SET error = ?, "
            "updated_at = unixepoch() WHERE task_id = ?",
            (
                "Execution is no longer authorized"
                if denied
                else "Execution service is temporarily unavailable",
                task_id,
            ),
        )


def actionable(conn: Any) -> list[dict[str, Any]]:
    ensure_schema(conn)
    return [
        dict(row)
        for row in conn.execute(
            "SELECT e.* FROM xnobrain_personal_task_executions e "
            "JOIN tasks t ON t.id = e.task_id WHERE e.creation_complete = 0 OR t.status = 'ready' "
            "OR (e.admission_key IS NOT NULL AND e.status NOT IN ('completed','failed','cancelled')) "
            "ORDER BY e.updated_at, e.task_id LIMIT 100"
        ).fetchall()
    ]


def board_has_unresolved_work(conn: Any) -> bool:
    ensure_schema(conn)
    return (
        conn.execute(
            "SELECT 1 FROM xnobrain_personal_task_executions e JOIN tasks t ON t.id = e.task_id "
            "WHERE e.creation_complete = 0 OR t.status = 'ready' OR "
            "(e.admission_key IS NOT NULL AND e.status NOT IN ('completed','failed','cancelled')) LIMIT 1"
        ).fetchone()
        is not None
    )


def in_flight(current: dict[str, Any] | None) -> bool:
    return bool(current and current["admission_key"] and current["status"] not in TERMINAL)
