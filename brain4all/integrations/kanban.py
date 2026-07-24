"""Small adapter around the Hermes Kanban database.

Brain4All leaves the native task, run, and event schemas untouched. Its
schedule metadata lives in a namespaced extension table created here, at the
only boundary where the platform service reaches into Hermes' Kanban
implementation. The import is lazy so the rest of the API can still be
imported by tooling and tests that do not install the Hermes runtime.
"""

from __future__ import annotations

from contextlib import contextmanager
import asyncio
import inspect
import json
import logging
from pathlib import Path
import re
import time
from typing import Any, Iterator

_log = logging.getLogger("brain4all.kanban")


class KanbanUnavailable(RuntimeError):
    """Raised when the Hermes Kanban package is not available."""


def _module():
    try:
        from hermes_cli import kanban_db
    except Exception as exc:  # pragma: no cover - depends on deployment image
        raise KanbanUnavailable(
            "Kanban is unavailable; install the required local runtime"
        ) from exc
    return kanban_db


@contextmanager
def connection(board: str = "default") -> Iterator[Any]:
    kb = _module()
    try:
        with kb.connect_closing(board=board) as conn:
            yield conn
    except AttributeError:
        # Older pinned Hermes revisions exposed connect() but not the safer
        # connect_closing().  Keep the fallback narrow and close explicitly.
        conn = kb.connect(board=board)
        try:
            yield conn
        finally:
            conn.close()


def board_slug(value: str | None) -> str:
    kb = _module()
    slug = str(value or "default").strip().lower()
    normalizer = getattr(kb, "_normalize_board_slug", None)
    if normalizer is not None:
        normalized = normalizer(slug)
        if normalized:
            return normalized
    if not slug or len(slug) > 64 or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for ch in slug):
        raise ValueError("invalid board slug")
    return slug


def list_boards(*, include_archived: bool = False) -> list[dict[str, Any]]:
    return list(_module().list_boards(include_archived=include_archived))


def create_board(slug: str, **fields: Any) -> dict[str, Any]:
    return dict(_module().create_board(board_slug(slug), **fields))


def write_board_metadata(slug: str, **fields: Any) -> dict[str, Any]:
    return dict(_module().write_board_metadata(board_slug(slug), **fields))


def remove_board(slug: str, *, archive: bool = True) -> dict[str, Any]:
    return dict(_module().remove_board(board_slug(slug), archive=archive))


def current_board() -> str:
    return str(_module().get_current_board())


def select_board(slug: str) -> str:
    normalized = board_slug(slug)
    kb = _module()
    if not getattr(kb, "board_exists", lambda _slug: False)(normalized):
        raise ValueError(f"board {normalized!r} does not exist")
    kb.set_current_board(normalized)
    return normalized


def task_dependencies(conn: Any, task_id: str) -> list[str]:
    kb = _module()
    return [str(item) for item in kb.parent_ids(conn, task_id)]


def task_children(conn: Any, task_id: str) -> list[str]:
    kb = _module()
    return [str(item) for item in kb.child_ids(conn, task_id)]


def task_events(conn: Any, task_id: str) -> list[Any]:
    return list(_module().list_events(conn, task_id))


def task_comments(conn: Any, task_id: str) -> list[Any]:
    return list(_module().list_comments(conn, task_id))


def task_runs(conn: Any, task_id: str) -> list[Any]:
    return list(_module().list_runs(conn, task_id))


def task_attachments(conn: Any, task_id: str) -> list[Any]:
    return list(_module().list_attachments(conn, task_id))


def ensure_schedule_schema(conn: Any) -> None:
    """Install Brain4All scheduling metadata beside native Kanban tasks.

    Native task, event, and run tables remain untouched. This extension table
    is deliberately small and keyed by the native task ID so it can be removed
    without migrating the upstream schema.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS brain4all_task_schedules (
            task_id TEXT PRIMARY KEY,
            recurrence TEXT NOT NULL CHECK (recurrence IN ('once', 'interval')),
            next_run_at INTEGER,
            interval_seconds INTEGER,
            timezone TEXT NOT NULL DEFAULT 'Etc/UTC',
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
            occurrence_count INTEGER NOT NULL DEFAULT 0,
            last_run_at INTEGER,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_brain4all_schedules_due "
        "ON brain4all_task_schedules(enabled, next_run_at)"
    )


def task_schedule(conn: Any, task_id: str) -> dict[str, Any] | None:
    ensure_schedule_schema(conn)
    row = conn.execute(
        "SELECT task_id, recurrence, next_run_at, interval_seconds, timezone, "
        "enabled, occurrence_count, last_run_at, created_at, updated_at "
        "FROM brain4all_task_schedules WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def put_task_schedule(
    conn: Any,
    task_id: str,
    *,
    recurrence: str,
    next_run_at: int,
    interval_seconds: int | None,
    timezone_name: str,
) -> dict[str, Any]:
    if recurrence not in {"once", "interval"}:
        raise ValueError("schedule recurrence is invalid")
    if int(next_run_at) <= int(time.time()):
        raise ValueError("scheduled time must be in the future")
    if recurrence == "interval" and (interval_seconds is None or int(interval_seconds) < 60):
        raise ValueError("repeat interval must be at least one minute")
    now = int(time.time())
    ensure_schedule_schema(conn)
    with _module().write_txn(conn):
        task = _module().get_task(conn, task_id)
        if task is None:
            raise ValueError("task not found")
        conn.execute(
            """
            INSERT INTO brain4all_task_schedules
                (task_id, recurrence, next_run_at, interval_seconds, timezone,
                 enabled, occurrence_count, last_run_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, 0, NULL, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                recurrence = excluded.recurrence,
                next_run_at = excluded.next_run_at,
                interval_seconds = excluded.interval_seconds,
                timezone = excluded.timezone,
                enabled = 1,
                updated_at = excluded.updated_at
            """,
            (
                task_id,
                recurrence,
                int(next_run_at),
                int(interval_seconds) if interval_seconds is not None else None,
                timezone_name,
                now,
                now,
            ),
        )
        conn.execute(
            "INSERT INTO task_events (task_id, kind, payload, created_at) "
            "VALUES (?, 'schedule_set', ?, unixepoch())",
            (
                task_id,
                json.dumps({
                    "recurrence": recurrence,
                    "next_run_at": int(next_run_at),
                    "timezone": timezone_name,
                }),
            ),
        )
    schedule = task_schedule(conn, task_id)
    assert schedule is not None
    return schedule


def set_task_schedule_enabled(conn: Any, task_id: str, enabled: bool) -> dict[str, Any]:
    ensure_schedule_schema(conn)
    now = int(time.time())
    with _module().write_txn(conn):
        changed = conn.execute(
            "UPDATE brain4all_task_schedules SET enabled = ?, updated_at = ? "
            "WHERE task_id = ?",
            (1 if enabled else 0, now, task_id),
        )
        if changed.rowcount != 1:
            raise ValueError("task schedule not found")
        conn.execute(
            "INSERT INTO task_events (task_id, kind, payload, created_at) "
            "VALUES (?, ?, NULL, unixepoch())",
            (task_id, "schedule_resumed" if enabled else "schedule_paused"),
        )
    schedule = task_schedule(conn, task_id)
    assert schedule is not None
    return schedule


def run_task_schedule_now(conn: Any, task_id: str, *, board: str) -> list[str]:
    ensure_schedule_schema(conn)
    schedule = task_schedule(conn, task_id)
    if schedule is None:
        raise ValueError("task schedule not found")
    task = _module().get_task(conn, task_id)
    if task is None:
        raise ValueError("task not found")
    if str(task.status) == "archived":
        raise ValueError("archived schedules cannot run")
    if str(schedule["recurrence"]) == "once" and str(task.status) != "scheduled":
        raise ValueError("one-time schedule has already run")
    now = int(time.time())
    with _module().write_txn(conn):
        changed = conn.execute(
            "UPDATE brain4all_task_schedules "
            "SET enabled = 1, next_run_at = ?, updated_at = ? WHERE task_id = ?",
            (now, now, task_id),
        )
        if changed.rowcount != 1:
            raise ValueError("task schedule not found")
    return release_due_schedules(conn, board=board, now=now)


def _append_schedule_event(
    conn: Any,
    task_id: str,
    *,
    occurrence_id: str,
    scheduled_for: int,
) -> None:
    with _module().write_txn(conn):
        conn.execute(
            "INSERT INTO task_events (task_id, kind, payload, created_at) "
            "VALUES (?, 'schedule_fired', ?, unixepoch())",
            (
                task_id,
                json.dumps({
                    "occurrence_task_id": occurrence_id,
                    "scheduled_for": scheduled_for,
                }),
            ),
        )


def release_due_schedules(conn: Any, *, board: str, now: int | None = None) -> list[str]:
    """Release one-shot tasks and materialize recurring task occurrences."""
    kb = _module()
    current = int(time.time()) if now is None else int(now)
    ensure_schedule_schema(conn)
    due = conn.execute(
        "SELECT task_id, recurrence, next_run_at, interval_seconds "
        "FROM brain4all_task_schedules "
        "WHERE enabled = 1 AND next_run_at IS NOT NULL AND next_run_at <= ? "
        "ORDER BY next_run_at, task_id LIMIT 100",
        (current,),
    ).fetchall()
    released: list[str] = []
    for row in due:
        task_id = str(row["task_id"])
        scheduled_for = int(row["next_run_at"])
        task = kb.get_task(conn, task_id)
        if task is None or str(task.status) == "archived":
            with kb.write_txn(conn):
                conn.execute(
                    "UPDATE brain4all_task_schedules "
                    "SET enabled = 0, next_run_at = NULL, updated_at = ? "
                    "WHERE task_id = ? AND next_run_at = ?",
                    (current, task_id, scheduled_for),
                )
            continue

        if str(row["recurrence"]) == "once":
            if str(task.status) == "scheduled" and not kb.unblock_task(conn, task_id):
                continue
            with kb.write_txn(conn):
                changed = conn.execute(
                    "UPDATE brain4all_task_schedules "
                    "SET enabled = 0, next_run_at = NULL, last_run_at = ?, "
                    "occurrence_count = occurrence_count + 1, updated_at = ? "
                    "WHERE task_id = ? AND next_run_at = ?",
                    (current, current, task_id, scheduled_for),
                )
            if changed.rowcount == 1:
                _append_schedule_event(
                    conn,
                    task_id,
                    occurrence_id=task_id,
                    scheduled_for=scheduled_for,
                )
                released.append(task_id)
            continue

        interval = int(row["interval_seconds"] or 0)
        if interval < 60 or str(task.status) != "scheduled":
            continue
        occurrence_id = create_task(
            conn,
            title=str(task.title),
            body=str(task.body or ""),
            assignee=getattr(task, "assignee", None),
            created_by="brain4all-schedule",
            priority=int(getattr(task, "priority", 0) or 0),
            parents=task_dependencies(conn, task_id),
            idempotency_key=f"schedule:{task_id}:{scheduled_for}",
            workspace_kind=str(getattr(task, "workspace_kind", "scratch")),
            workspace_path=getattr(task, "workspace_path", None),
            skills=list(getattr(task, "skills", None) or []),
            model_override=getattr(task, "model_override", None),
            provider_override=getattr(task, "provider_override", None),
            goal_mode=bool(getattr(task, "goal_mode", False)),
            initial_status="running",
            board=board,
        )
        next_run = scheduled_for + interval
        while next_run <= current:
            next_run += interval
        with kb.write_txn(conn):
            changed = conn.execute(
                "UPDATE brain4all_task_schedules "
                "SET next_run_at = ?, last_run_at = ?, "
                "occurrence_count = occurrence_count + 1, updated_at = ? "
                "WHERE task_id = ? AND next_run_at = ?",
                (next_run, current, current, task_id, scheduled_for),
            )
        if changed.rowcount == 1:
            _append_schedule_event(
                conn,
                task_id,
                occurrence_id=occurrence_id,
                scheduled_for=scheduled_for,
            )
            released.append(occurrence_id)
    return released


def create_task(conn: Any, **fields: Any) -> str:
    """Create through the installed runtime's supported public signature.

    Hermes releases have added optional execution overrides over time. Avoid
    sending absent optional values to older releases, while rejecting an
    explicit override that the installed runtime cannot honor.
    """
    creator = _module().create_task
    supported = set(inspect.signature(creator).parameters)
    unsupported_requested = [
        name
        for name in ("model_override", "provider_override")
        if fields.get(name) is not None and name not in supported
    ]
    if unsupported_requested:
        labels = ", ".join(name.replace("_", " ") for name in unsupported_requested)
        raise ValueError(f"the installed runtime does not support {labels}")
    compatible = {
        name: value
        for name, value in fields.items()
        if name in supported
    }
    return str(creator(conn, **compatible))


def update_task_fields(
    conn: Any,
    task_id: str,
    *,
    title: str | None = None,
    body: str | None = None,
    priority: int | None = None,
    skills: list[str] | None = None,
) -> bool:
    """Update fields used by the official dashboard before execution starts.

    The installed task package has public create/assign operations but no
    equivalent field-edit helper. Keep this compatibility shim in the single
    integration boundary, use its transaction helper, and append the same
    durable ``edited`` event as the bundled dashboard.
    """
    kb = _module()
    cleaned_skills: list[str] | None = None
    if skills is not None:
        cleaned_skills = []
        seen: set[str] = set()
        known_toolsets = set(getattr(kb, "KNOWN_TOOLSET_NAMES", ()))
        for raw in skills:
            name = str(raw or "").strip()
            if not name:
                continue
            if "," in name:
                raise ValueError(f"skill name cannot contain comma: {name!r}")
            if name.casefold() in known_toolsets:
                raise ValueError(f"{name!r} is a toolset name, not a skill name")
            if name not in seen:
                seen.add(name)
                cleaned_skills.append(name)
    with kb.write_txn(conn):
        task = kb.get_task(conn, task_id)
        if task is None:
            return False
        if str(task.status) not in {"triage", "todo", "ready", "scheduled"}:
            raise RuntimeError("Task details can only be edited before the task starts")
        sets: list[str] = []
        values: list[Any] = []
        if title is not None:
            title = title.strip()
            if not title:
                raise ValueError("title is required")
            sets.append("title = ?")
            values.append(title)
        if body is not None:
            body = body.strip()
            if not body:
                raise ValueError("description is required")
            sets.append("body = ?")
            values.append(body)
        if priority is not None:
            sets.append("priority = ?")
            values.append(int(priority))
        if cleaned_skills is not None:
            sets.append("skills = ?")
            values.append(json.dumps(cleaned_skills))
        if not sets:
            return True
        values.extend([task_id, str(task.status)])
        changed = conn.execute(
            f"UPDATE tasks SET {', '.join(sets)} WHERE id = ? AND status = ?",
            tuple(values),
        )
        if changed.rowcount != 1:
            raise RuntimeError("Task changed before the edit could be saved")
        conn.execute(
            "INSERT INTO task_events (task_id, kind, payload, created_at) "
            "VALUES (?, 'edited', ?, unixepoch())",
            (
                task_id,
                json.dumps({
                    "fields": [
                        field
                        for field, present in (
                            ("title", title is not None),
                            ("description", body is not None),
                            ("priority", priority is not None),
                            ("skills", cleaned_skills is not None),
                        )
                        if present
                    ],
                }),
            ),
        )
    return True


def update_task_workspace(
    conn: Any,
    task_id: str,
    *,
    workspace_kind: str,
    workspace_path: str | None,
) -> bool:
    """Change the execution workspace before a task starts."""
    kb = _module()
    if workspace_kind not in {"scratch", "dir", "worktree"}:
        raise ValueError("workspace kind is invalid")
    with kb.write_txn(conn):
        task = kb.get_task(conn, task_id)
        if task is None:
            return False
        if str(task.status) not in {"triage", "todo", "ready", "scheduled"}:
            raise ValueError("A task workspace can only change before it starts")
        changed = conn.execute(
            "UPDATE tasks SET workspace_kind = ?, workspace_path = ? "
            "WHERE id = ? AND status = ?",
            (workspace_kind, workspace_path, task_id, str(task.status)),
        )
        if changed.rowcount != 1:
            raise ValueError("The task changed before its workspace could be saved")
        conn.execute(
            "INSERT INTO task_events (task_id, kind, payload, created_at) "
            "VALUES (?, 'workspace_changed', ?, unixepoch())",
            (task_id, json.dumps({"workspace_kind": workspace_kind})),
        )
    return True


_SESSION_LINE = re.compile(r"^\s*Session:\s*([A-Za-z0-9_.:-]+)\s*$")
_TOOL_ACTIVITY_LINE = re.compile(
    r"^\s*┊\s*(?:[^A-Za-z0-9]+)?(?:preparing\s+)?([A-Za-z][A-Za-z0-9_-]*)"
    r"(?:\s+.*?\s+|\s+)(\d+(?:\.\d+)?)s(?:\s+.*)?$"
)


def safe_worker_activity(task_id: str, *, board: str, limit: int = 80) -> dict[str, Any]:
    """Return bounded worker progress without prompts, arguments, or output."""
    kb = _module()
    content = kb.read_worker_log(task_id, tail_bytes=200_000, board=board) or ""
    path = kb.worker_log_path(task_id, board=board)
    session_id: str | None = None
    entries: list[dict[str, Any]] = []
    for line in content.splitlines():
        session = _SESSION_LINE.match(line)
        if session:
            session_id = session.group(1)
            continue
        activity = _TOOL_ACTIVITY_LINE.match(line)
        if activity:
            entries.append({
                "kind": "tool",
                "name": activity.group(1),
                "duration_seconds": float(activity.group(2)),
            })
    return {
        "exists": bool(content),
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "session_id": session_id,
        "entries": entries[-max(1, min(int(limit), 200)):],
    }


def attachment_path(attachment: Any) -> Path:
    return Path(str(attachment.stored_path)).resolve()


async def dispatcher_loop(*, interval_seconds: float = 15.0) -> None:
    """Run Hermes' supported dispatcher tick inside the host FastAPI process.

    The worker spawning, claiming, recovery, and board lock all remain in
    Hermes ``dispatch_once``. Brain4All only supplies the host lifecycle; this
    is intentionally not a second worker process or cron scheduler.
    """
    while True:
        try:
            kb = _module()
            for board in kb.list_boards(include_archived=False):
                slug = str(board.get("slug") or "default")
                with connection(slug) as conn:
                    release_due_schedules(conn, board=slug)
                    kb.dispatch_once(conn, board=slug)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # keep local manual Kanban usable if worker runtime is degraded
            _log.warning("Kanban dispatcher tick failed: %s", type(exc).__name__)
        await asyncio.sleep(max(1.0, float(interval_seconds)))
