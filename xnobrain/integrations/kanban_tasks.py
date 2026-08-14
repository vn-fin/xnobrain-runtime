"""Kanban task operations."""

from .kanban_support import (
    Any,
    Path,
    _module,
    inspect,
    json,
    re,
)

_SESSION_LINE = re.compile(r"^\s*Session:\s*([A-Za-z0-9_.:-]+)\s*$")
_TOOL_ACTIVITY_LINE = re.compile(
    r"^\s*┊\s*(?:[^A-Za-z0-9]+)?(?:preparing\s+)?([A-Za-z][A-Za-z0-9_-]*)"
    r"(?:\s+.*?\s+|\s+)(\d+(?:\.\d+)?)s(?:\s+.*)?$"
)

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


def team_member_task_ids(conn: Any) -> set[str]:
    """Return internal team-stage task IDs without hydrating every task DTO."""
    rows = conn.execute(
        "SELECT body FROM task_comments WHERE body LIKE ?",
        ("[xnobrain:team] %",),
    ).fetchall()
    task_ids: set[str] = set()
    for row in rows:
        body = str(row["body"] or "")
        try:
            metadata = json.loads(body[len("[xnobrain:team] "):])
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(metadata, dict):
            continue
        for node in metadata.get("nodes") or []:
            if isinstance(node, dict) and node.get("task_id"):
                task_ids.add(str(node["task_id"]))
        if metadata.get("synthesis_task_id"):
            task_ids.add(str(metadata["synthesis_task_id"]))
    return task_ids


def task_page(
    conn: Any,
    *,
    include_archived: bool,
    archived_only: bool,
    assignee: str | None,
    search: str | None,
    offset: int,
    limit: int,
) -> tuple[list[Any], int]:
    """Read one top-level task page in SQLite instead of hydrating the board."""
    kb = _module()
    clauses = ["1=1"]
    params: list[Any] = []
    member_ids = sorted(team_member_task_ids(conn))
    if member_ids:
        clauses.append(f"id NOT IN ({','.join('?' for _ in member_ids)})")
        params.extend(member_ids)
    if archived_only:
        clauses.append("status = 'archived'")
    elif not include_archived:
        clauses.append("status != 'archived'")
    if assignee:
        clauses.append("assignee = ?")
        params.append(str(assignee))
    query = str(search or "").strip().lower()
    if query:
        clauses.append("(lower(id) LIKE ? OR lower(title) LIKE ? OR lower(COALESCE(body, '')) LIKE ? OR lower(COALESCE(assignee, '')) LIKE ?)")
        needle = f"%{query}%"
        params.extend([needle, needle, needle, needle])
    where = " AND ".join(clauses)
    total = int(conn.execute(f"SELECT COUNT(*) FROM tasks WHERE {where}", params).fetchone()[0])
    rows = conn.execute(
        f"SELECT * FROM tasks WHERE {where} ORDER BY started_at DESC NULLS LAST, created_at DESC LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    return [kb.Task.from_row(row) for row in rows], total


def return_failed_task_to_triage(
    conn: Any,
    task_id: str,
    *,
    actor: str = "xnobrain",
) -> bool:
    """Return a failed (circuit-breaker blocked) task to native triage.

    Hermes exposes unblock operations for retrying a task, but no operation
    for deliberately putting a failed task back into triage. Keep the guarded
    transition here at the integration boundary so task invariants and the
    native event stream remain explicit.
    """
    kb = _module()
    with kb.write_txn(conn):
        row = conn.execute(
            "SELECT status, last_failure_error, current_run_id, claim_lock "
            "FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            return False
        if (
            row["status"] != "blocked"
            or not row["last_failure_error"]
            or row["current_run_id"] is not None
            or row["claim_lock"] is not None
        ):
            return False
        changed = conn.execute(
            "UPDATE tasks SET status = 'triage', claim_lock = NULL, "
            "claim_expires = NULL, worker_pid = NULL, current_run_id = NULL, "
            "consecutive_failures = 0, last_failure_error = NULL "
            "WHERE id = ? AND status = 'blocked' AND last_failure_error IS NOT NULL",
            (task_id,),
        )
        if changed.rowcount != 1:
            return False
        kb._append_event(
            conn,
            task_id,
            "returned_to_triage",
            {"actor": actor},
        )
    return True


def task_attachments(conn: Any, task_id: str) -> list[Any]:
    return list(_module().list_attachments(conn, task_id))


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


def park_task_in_todo(conn: Any, task_id: str) -> bool:
    """Convert Hermes' initial ready state into an explicit Todo state."""
    kb = _module()
    with kb.write_txn(conn):
        changed = conn.execute(
            "UPDATE tasks SET status = 'todo' WHERE id = ? AND status = 'ready'",
            (task_id,),
        )
        if changed.rowcount != 1:
            current = kb.get_task(conn, task_id)
            return current is not None and str(current.status) == "todo"
        conn.execute(
            "INSERT INTO task_events (task_id, kind, payload, created_at) "
            "VALUES (?, 'edited', ?, unixepoch())",
            (task_id, json.dumps({"fields": ["status"], "status": "todo"})),
        )
    return True


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
