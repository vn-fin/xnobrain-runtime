"""Kanban task scheduling."""

from .kanban_support import (
    Any,
    _log,
    _module,
    asyncio,
    connection,
    inspect,
    json,
    time,
)
from .kanban_tasks import create_task, task_dependencies

def ensure_schedule_schema(conn: Any) -> None:
    """Install XNOBrain scheduling metadata beside native Kanban tasks.

    Native task, event, and run tables remain untouched. This extension table
    is deliberately small and keyed by the native task ID so it can be removed
    without migrating the upstream schema.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS xnobrain_task_schedules (
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
        "CREATE INDEX IF NOT EXISTS idx_xnobrain_schedules_due "
        "ON xnobrain_task_schedules(enabled, next_run_at)"
    )


def task_schedule(conn: Any, task_id: str) -> dict[str, Any] | None:
    ensure_schedule_schema(conn)
    row = conn.execute(
        "SELECT task_id, recurrence, next_run_at, interval_seconds, timezone, "
        "enabled, occurrence_count, last_run_at, created_at, updated_at "
        "FROM xnobrain_task_schedules WHERE task_id = ?",
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
            INSERT INTO xnobrain_task_schedules
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
            "UPDATE xnobrain_task_schedules SET enabled = ?, updated_at = ? "
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
            "UPDATE xnobrain_task_schedules "
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
        "FROM xnobrain_task_schedules "
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
                    "UPDATE xnobrain_task_schedules "
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
                    "UPDATE xnobrain_task_schedules "
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
            created_by="xnobrain-schedule",
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
                "UPDATE xnobrain_task_schedules "
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


async def dispatcher_loop(*, interval_seconds: float = 15.0, on_tick=None) -> None:
    """Run Hermes' supported dispatcher tick inside the host FastAPI process.

    The worker spawning, claiming, recovery, and board lock all remain in
    Hermes ``dispatch_once``. XNOBrain only supplies the host lifecycle; this
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
            if on_tick is not None:
                result = on_tick()
                if inspect.isawaitable(result):
                    await result
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # keep local manual Kanban usable if worker runtime is degraded
            _log.warning("Kanban dispatcher tick failed: %s", type(exc).__name__)
        await asyncio.sleep(max(1.0, float(interval_seconds)))
