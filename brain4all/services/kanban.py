"""Brain4All's product-level Kanban service.

Hermes owns the records and transitions.  This layer only projects Hermes'
execution states into the five product columns and translates user intents into
public Hermes operations.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from ..integrations import kanban as kb_adapter
from ..integrations.kanban import KanbanUnavailable
from .platform import ServiceError


PRODUCT_STATUSES = ("backlog", "todo", "running", "done", "archived")
PRIORITY_TO_INT = {"low": 0, "medium": 1, "high": 2}
INT_TO_PRIORITY = {0: "low", 1: "medium", 2: "high"}


def _iso(epoch: int | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _status(raw: str) -> str:
    if raw == "triage":
        return "backlog"
    if raw in {"todo", "scheduled"}:
        return "todo"
    if raw in {"ready", "running"}:
        return "running"
    if raw in {"review", "blocked", "done"}:
        return "done"
    if raw == "archived":
        return "archived"
    raise ServiceError("The task has an unsupported status", status=503, code="kanban_contract_incompatible")


def _detail(task: Any) -> dict[str, Any]:
    raw = str(task.status)
    labels = {
        "triage": ("triage", "Needs clarification"),
        "todo": ("todo", "Todo"),
        "ready": ("ready", "Ready"),
        "scheduled": ("scheduled", "Scheduled"),
        "running": ("running", "Running"),
        "review": ("review_required", "Review required"),
        "blocked": (str(getattr(task, "block_kind", None) or "needs_input"), "Needs input"),
        "done": ("completed", "Completed"),
        "archived": ("archived", "Archived"),
    }
    kind, label = labels.get(raw, (raw, raw.replace("_", " ").title()))
    # Failure text can contain provider/tool output. Keep the UI actionable
    # without returning that sensitive payload through the management API.
    reason = "The task run failed" if raw == "blocked" and getattr(task, "last_failure_error", None) else None
    return {"kind": kind, "label": label, "reason": reason}


class KanbanService:
    """Translate the HTTP product contract into Hermes Kanban operations."""

    @staticmethod
    def _public_board(item: Mapping[str, Any]) -> dict[str, Any]:
        """Return board metadata without exposing Hermes' local DB path."""
        return {key: value for key, value in item.items() if key != "db_path"}

    @staticmethod
    def _safe_event_payload(payload: Any) -> Any:
        """Keep audit facts while dropping paths and execution internals."""
        blocked = {"prompt", "body", "result", "output", "tool_args", "arguments", "stored_path", "workspace_path", "credentials", "api_key", "token"}
        if isinstance(payload, Mapping):
            return {
                str(key): KanbanService._safe_event_payload(value)
                for key, value in payload.items()
                if str(key).lower() not in blocked and not str(key).lower().endswith("_path")
            }
        if isinstance(payload, list):
            return [KanbanService._safe_event_payload(value) for value in payload]
        return payload

    def _ready(self):
        try:
            return kb_adapter._module()
        except KanbanUnavailable as exc:
            raise ServiceError(str(exc), status=503, code="kanban_not_ready") from exc

    def _board(self, slug: str) -> str:
        try:
            return kb_adapter.board_slug(slug)
        except (ValueError, KanbanUnavailable) as exc:
            if isinstance(exc, KanbanUnavailable):
                raise ServiceError(str(exc), status=503, code="kanban_not_ready") from exc
            raise ServiceError(str(exc), code="board_not_found") from exc

    def _task_dto(self, conn: Any, task: Any, *, board: str) -> dict[str, Any]:
        kb = self._ready()
        parent_ids = kb_adapter.task_dependencies(conn, task.id)
        children = kb_adapter.task_children(conn, task.id)
        parent_rows = []
        for parent_id in parent_ids:
            parent = kb.get_task(conn, parent_id)
            if parent is not None:
                parent_rows.append({
                    "id": parent.id,
                    "title": parent.title,
                    "status": str(parent.status),
                    "kanban_status": _status(parent.status),
                })
        comments = kb_adapter.task_comments(conn, task.id)
        attachments = kb_adapter.task_attachments(conn, task.id)
        runs = kb_adapter.task_runs(conn, task.id)
        raw_status = str(task.status)
        progress = 100 if raw_status in {"done", "archived"} and getattr(task, "completed_at", None) is not None else (50 if raw_status == "running" else 0)
        return {
            "id": str(task.id),
            "title": str(task.title),
            "description": str(task.body or ""),
            "status": raw_status,
            "kanban_status": _status(raw_status),
            "state_detail": _detail(task),
            "priority": INT_TO_PRIORITY.get(int(getattr(task, "priority", 0) or 0), "medium"),
            "assignee": getattr(task, "assignee", None),
            "assignees": [task.assignee] if getattr(task, "assignee", None) else [],
            "parents": parent_rows,
            "children": children,
            "tags": [],
            "progress": progress,
            "archived": raw_status == "archived",
            "block": "The task run failed" if raw_status == "blocked" and getattr(task, "last_failure_error", None) else None,
            "summary": getattr(task, "result", None),
            "workspace_kind": getattr(task, "workspace_kind", "scratch"),
            "workspace_path": None,
            "model_override": getattr(task, "model_override", None),
            "provider_override": getattr(task, "provider_override", None),
            "goal_mode": bool(getattr(task, "goal_mode", False)),
            "comments": [self._comment_dto(item) for item in comments],
            "attachments": [self._attachment_dto(item) for item in attachments],
            "runs": [self._run_dto(item) for item in runs],
            "worker": self._worker_dto(task),
            "created_at": _iso(getattr(task, "created_at", None)),
            "updated_at": _iso(getattr(task, "completed_at", None) or getattr(task, "started_at", None) or getattr(task, "created_at", None)),
            "board_slug": board,
            "revision": str(getattr(task, "updated_at", None) or getattr(task, "created_at", "")),
        }

    @staticmethod
    def _comment_dto(comment: Any) -> dict[str, Any]:
        return {"id": int(comment.id), "task_id": str(comment.task_id), "author": str(comment.author), "body": str(comment.body), "created_at": _iso(comment.created_at)}

    @staticmethod
    def _attachment_dto(attachment: Any) -> dict[str, Any]:
        return {"id": int(attachment.id), "task_id": str(attachment.task_id), "filename": str(attachment.filename), "content_type": attachment.content_type, "size": int(attachment.size), "uploaded_by": attachment.uploaded_by, "created_at": _iso(attachment.created_at)}

    @staticmethod
    def _run_dto(run: Any) -> dict[str, Any]:
        return {"id": int(run.id), "task_id": str(run.task_id), "profile": run.profile, "status": run.status, "outcome": run.outcome, "summary": run.summary, "metadata": run.metadata, "started_at": _iso(run.started_at), "ended_at": _iso(run.ended_at)}

    @staticmethod
    def _worker_dto(task: Any) -> dict[str, Any] | None:
        if str(getattr(task, "status", "")) != "running":
            return None
        return {"pid": getattr(task, "worker_pid", None), "heartbeat_at": _iso(getattr(task, "last_heartbeat_at", None)), "run_id": getattr(task, "current_run_id", None)}

    def list_boards(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        try:
            boards = kb_adapter.list_boards(include_archived=include_archived)
        except (KanbanUnavailable, ValueError) as exc:
            raise ServiceError(str(exc), status=503, code="kanban_not_ready") from exc
        current = kb_adapter.current_board()
        output = []
        for item in boards:
            slug = str(item.get("slug") or "default")
            with kb_adapter.connection(slug) as conn:
                tasks = self._list_task_dtos(conn, slug, include_archived=include_archived)
            output.append({**{key: value for key, value in item.items() if key != "db_path"}, "id": slug, "current": slug == current, "tasks": tasks, "task_count": len(tasks)})
        return output

    def get_board(self, slug: str, *, include_archived: bool = False) -> dict[str, Any]:
        normalized = self._board(slug)
        board = next((item for item in self.list_boards(include_archived=True) if item["id"] == normalized), None)
        if board is None or (board.get("archived") and not include_archived):
            raise ServiceError("board not found", status=404, code="board_not_found")
        if not include_archived:
            board["tasks"] = [item for item in board["tasks"] if not item["archived"]]
            board["task_count"] = len(board["tasks"])
        return board

    def _list_task_dtos(self, conn: Any, board: str, *, include_archived: bool = False, assignee: str | None = None, status: str | None = None, search: str | None = None) -> list[dict[str, Any]]:
        kb = self._ready()
        raw = kb.list_tasks(conn, assignee=assignee or None, include_archived=include_archived, order_by="updated")
        rows = [self._task_dto(conn, task, board=board) for task in raw]
        if status:
            if status not in PRODUCT_STATUSES:
                raise ServiceError("invalid Kanban status", code="invalid_request")
            rows = [row for row in rows if row["kanban_status"] == status]
        query = (search or "").strip().lower()
        if query:
            rows = [row for row in rows if query in " ".join([row["id"], row["title"], row["description"], row.get("assignee") or ""]).lower()]
        return rows

    def list_tasks(self, board: str, query: Mapping[str, Any]) -> dict[str, Any]:
        normalized = self._board(board)
        with kb_adapter.connection(normalized) as conn:
            tasks = self._list_task_dtos(conn, normalized, include_archived=str(query.get("include_archived", "false")).lower() == "true", assignee=query.get("assignee"), status=query.get("status"), search=query.get("search"))
        total = len(tasks)
        try:
            offset = max(0, int(query.get("offset") or 0))
            limit = min(200, max(1, int(query.get("limit") or 200)))
        except (TypeError, ValueError) as exc:
            raise ServiceError("limit and offset must be integers", code="invalid_request") from exc
        return {"board_slug": normalized, "tasks": tasks[offset:offset + limit], "total": total, "offset": offset, "limit": limit}

    def get_task(self, board: str, task_id: str) -> dict[str, Any]:
        normalized = self._board(board)
        kb = self._ready()
        with kb_adapter.connection(normalized) as conn:
            task = kb.get_task(conn, task_id)
            if task is None:
                raise ServiceError("task not found", status=404, code="task_not_found")
            return self._task_dto(conn, task, board=normalized)

    def create_task(self, board: str, body: Mapping[str, Any], *, created_by: str = "user") -> dict[str, Any]:
        normalized = self._board(board)
        kb = self._ready()
        title = str(body.get("title") or "").strip()
        if not title:
            raise ServiceError("title is required", code="invalid_request")
        status = str(body.get("status") or "todo")
        if status not in {"backlog", "todo"}:
            raise ServiceError("new tasks may start in Backlog or Todo", code="invalid_request")
        with kb_adapter.connection(normalized) as conn:
            task_id = kb.create_task(conn, title=title, body=str(body.get("description") or ""), assignee=body.get("assignee"), created_by=created_by, priority=PRIORITY_TO_INT.get(str(body.get("priority") or "medium"), 1), parents=body.get("parents") or (), triage=status == "backlog", idempotency_key=body.get("idempotency_key"), workspace_kind=str(body.get("workspace_kind") or "scratch"), workspace_path=body.get("workspace_path"), skills=body.get("skills"), model_override=body.get("model_override"), provider_override=body.get("provider_override"), goal_mode=bool(body.get("goal_mode", False)), initial_status="running", board=normalized)
            task = kb.get_task(conn, task_id)
            if task is None:
                raise ServiceError("The created task could not be loaded", status=503, code="kanban_contract_incompatible")
            return self._task_dto(conn, task, board=normalized)

    def patch_task(self, board: str, task_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        normalized = self._board(board)
        kb = self._ready()
        with kb_adapter.connection(normalized) as conn:
            task = kb.get_task(conn, task_id)
            if task is None:
                raise ServiceError("task not found", status=404, code="task_not_found")
            if task.status == "triage":
                ok = kb.specify_triage_task(conn, task_id, title=body.get("title"), body=body.get("description"), author="user")
                if not ok:
                    raise ServiceError("task changed before it could be edited", status=409, code="stale_task")
            else:
                raise ServiceError("Active task fields cannot be edited", status=501, code="kanban_contract_incompatible")
            task = kb.get_task(conn, task_id)
            return self._task_dto(conn, task, board=normalized)

    def assign_task(self, board: str, task_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        normalized = self._board(board)
        kb = self._ready()
        with kb_adapter.connection(normalized) as conn:
            task = kb.get_task(conn, task_id)
            if task is None:
                raise ServiceError("task not found", status=404, code="task_not_found")
            if str(task.status) not in {"triage", "todo", "ready", "scheduled"}:
                raise ServiceError(
                    "A task can only be assigned before it starts",
                    status=409,
                    code="assignment_invalid",
                )
            try:
                ok = kb.reassign_task(conn, task_id, body.get("assignee"), reclaim_first=bool(body.get("reclaim_first", False)), reason=body.get("reason"))
            except (ValueError, RuntimeError) as exc:
                raise ServiceError(str(exc), status=409, code="assignment_invalid") from exc
            if not ok:
                raise ServiceError("task cannot be reassigned while active", status=409, code="assignment_invalid")
            task = kb.get_task(conn, task_id)
            if task is None:
                raise ServiceError("task not found", status=404, code="task_not_found")
            return self._task_dto(conn, task, board=normalized)

    def move_task(self, board: str, task_id: str, target: str, *, reason: str | None = None) -> dict[str, Any]:
        normalized = self._board(board)
        kb = self._ready()
        with kb_adapter.connection(normalized) as conn:
            task = kb.get_task(conn, task_id)
            if task is None:
                raise ServiceError("task not found", status=404, code="task_not_found")
            raw = str(task.status)
            try:
                if target == "archived":
                    ok = kb.archive_task(conn, task_id)
                elif target == "done":
                    if raw == "blocked":
                        ok = kb.unblock_task(conn, task_id)
                        if ok:
                            ok = kb.complete_task(conn, task_id, summary=reason or "Completed from Brain4All")
                    else:
                        ok = kb.complete_task(conn, task_id, summary=reason or "Completed from Brain4All")
                elif target == "todo":
                    if raw == "triage":
                        ok = kb.specify_triage_task(conn, task_id, author="user")
                    elif raw in {"blocked", "scheduled"}:
                        ok = kb.unblock_task(conn, task_id)
                    else:
                        ok = raw in {"todo", "ready"}
                elif target == "running":
                    if raw == "review":
                        ok = kb.claim_review_task(conn, task_id, claimer="brain4all") is not None
                    elif raw == "blocked":
                        ok = kb.unblock_task(conn, task_id)
                    elif raw in {"todo", "ready", "scheduled"}:
                        if raw == "scheduled":
                            ok = kb.unblock_task(conn, task_id) and kb.claim_task(conn, task_id, claimer="brain4all") is not None
                        elif raw == "todo":
                            promoted, _reason = kb.promote_task(conn, task_id, actor="brain4all") if hasattr(kb, "promote_task") else (False, None)
                            if not promoted:
                                ok = False
                            else:
                                ok = kb.claim_task(conn, task_id, claimer="brain4all") is not None
                        else:
                            ok = kb.claim_task(conn, task_id, claimer="brain4all") is not None
                    else:
                        ok = False
                elif target == "backlog":
                    raise ServiceError("active tasks cannot be returned to Backlog", status=409, code="invalid_transition")
                else:
                    raise ServiceError("invalid Kanban status", code="invalid_request")
            except ServiceError:
                raise
            except (ValueError, RuntimeError) as exc:
                raise ServiceError(str(exc), status=409, code="invalid_transition") from exc
            if not ok:
                raise ServiceError("task cannot make that transition", status=409, code="invalid_transition")
            task = kb.get_task(conn, task_id)
            return self._task_dto(conn, task, board=normalized)

    def archive_task(self, board: str, task_id: str, *, unarchive: bool = False) -> dict[str, Any]:
        normalized = self._board(board)
        kb = self._ready()
        with kb_adapter.connection(normalized) as conn:
            task = kb.get_task(conn, task_id)
            if task is None:
                raise ServiceError("task not found", status=404, code="task_not_found")
            if unarchive:
                raise ServiceError("Archived tasks cannot be restored", status=501, code="kanban_contract_incompatible")
            if not kb.archive_task(conn, task_id):
                raise ServiceError("task could not be archived", status=409, code="invalid_transition")
            return self._task_dto(conn, kb.get_task(conn, task_id), board=normalized)

    def add_comment(self, board: str, task_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        normalized = self._board(board)
        kb = self._ready()
        with kb_adapter.connection(normalized) as conn:
            try:
                comment_id = kb.add_comment(conn, task_id, str(body.get("author") or "user"), str(body.get("body") or ""))
            except ValueError as exc:
                raise ServiceError(str(exc), code="invalid_request") from exc
            comment = next((item for item in kb.list_comments(conn, task_id) if item.id == comment_id), None)
            if comment is None:
                raise ServiceError("The comment could not be saved", status=503, code="kanban_contract_incompatible")
            return self._comment_dto(comment)

    def link_tasks(self, board: str, parent_id: str, child_id: str) -> dict[str, Any]:
        normalized = self._board(board)
        kb = self._ready()
        with kb_adapter.connection(normalized) as conn:
            try:
                kb.link_tasks(conn, parent_id, child_id)
            except ValueError as exc:
                raise ServiceError(str(exc), status=409, code="dependency_conflict") from exc
            return {"parent_id": parent_id, "child_id": child_id}

    def unlink_tasks(self, board: str, parent_id: str, child_id: str) -> dict[str, Any]:
        normalized = self._board(board)
        kb = self._ready()
        with kb_adapter.connection(normalized) as conn:
            try:
                if not kb.unlink_tasks(conn, parent_id, child_id):
                    raise ServiceError("dependency link not found", status=404, code="dependency_not_found")
            except ServiceError:
                raise
            except ValueError as exc:
                raise ServiceError(str(exc), status=409, code="dependency_conflict") from exc
            return {"parent_id": parent_id, "child_id": child_id, "linked": False}

    def diagnostics(self, board: str = "default") -> dict[str, Any]:
        normalized = self._board(board)
        kb = self._ready()
        path = kb.kanban_db_path(board=normalized)
        return {"board_slug": normalized, "ready": True, "db_exists": bool(path.exists()), "dispatcher": "managed-by-local-runtime"}

    def create_board(self, body: Mapping[str, Any]) -> dict[str, Any]:
        try:
            return self._public_board(kb_adapter.create_board(str(body.get("slug") or ""), name=body.get("name"), description=body.get("description"), color=body.get("color")))
        except (ValueError, KanbanUnavailable) as exc:
            code = "kanban_not_ready" if isinstance(exc, KanbanUnavailable) else "invalid_request"
            raise ServiceError(str(exc), status=503 if isinstance(exc, KanbanUnavailable) else 400, code=code) from exc

    def patch_board(self, slug: str, body: Mapping[str, Any]) -> dict[str, Any]:
        normalized = self._board(slug)
        fields = {key: body[key] for key in ("name", "description", "color") if key in body and body[key] is not None}
        try:
            return self._public_board(kb_adapter.write_board_metadata(normalized, **fields))
        except (ValueError, KanbanUnavailable) as exc:
            raise ServiceError(str(exc), status=503 if isinstance(exc, KanbanUnavailable) else 400, code="kanban_not_ready" if isinstance(exc, KanbanUnavailable) else "invalid_request") from exc

    def select_board(self, slug: str) -> dict[str, Any]:
        normalized = self._board(slug)
        try:
            return {"board_slug": kb_adapter.select_board(normalized)}
        except (ValueError, KanbanUnavailable) as exc:
            raise ServiceError(str(exc), status=503 if isinstance(exc, KanbanUnavailable) else 404, code="kanban_not_ready" if isinstance(exc, KanbanUnavailable) else "board_not_found") from exc

    def delete_board(self, slug: str) -> dict[str, Any]:
        normalized = self._board(slug)
        if normalized == "default":
            raise ServiceError("the default board cannot be deleted", status=409, code="invalid_request")
        try:
            return kb_adapter.remove_board(normalized, archive=True)
        except (ValueError, KanbanUnavailable) as exc:
            raise ServiceError(str(exc), status=503 if isinstance(exc, KanbanUnavailable) else 404, code="kanban_not_ready" if isinstance(exc, KanbanUnavailable) else "board_not_found") from exc

    def list_comments(self, board: str, task_id: str) -> list[dict[str, Any]]:
        normalized = self._board(board)
        kb = self._ready()
        with kb_adapter.connection(normalized) as conn:
            if kb.get_task(conn, task_id) is None:
                raise ServiceError("task not found", status=404, code="task_not_found")
            return [self._comment_dto(item) for item in kb.list_comments(conn, task_id)]

    def list_events(self, board: str, task_id: str) -> list[dict[str, Any]]:
        normalized = self._board(board)
        kb = self._ready()
        with kb_adapter.connection(normalized) as conn:
            task = kb.get_task(conn, task_id)
            if task is None:
                raise ServiceError("task not found", status=404, code="task_not_found")
            return [self._event_dto(item, task) for item in kb.list_events(conn, task_id)]

    def board_events(self, board: str, *, after_id: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        """Read board events through public task/event operations."""
        normalized = self._board(board)
        kb = self._ready()
        rows: list[dict[str, Any]] = []
        with kb_adapter.connection(normalized) as conn:
            for task in kb.list_tasks(conn, include_archived=True, order_by="updated"):
                for event in kb.list_events(conn, task.id):
                    if int(event.id) > after_id:
                        rows.append(self._event_dto(event, task))
        rows.sort(key=lambda item: item["id"])
        return rows[:max(1, min(int(limit), 200))]

    def board_event_cursor(self, board: str) -> int:
        cursor = 0
        while True:
            events = self.board_events(board, after_id=cursor, limit=200)
            if not events:
                return cursor
            cursor = events[-1]["id"]
            if len(events) < 200:
                return cursor

    def _event_dto(self, event: Any, task: Any) -> dict[str, Any]:
        native_status = str(getattr(task, "status", ""))
        return {
            "id": int(event.id),
            "task_id": str(event.task_id),
            "kind": str(event.kind),
            "payload": self._safe_event_payload(event.payload),
            "created_at": _iso(event.created_at),
            "run_id": event.run_id,
            "assignee": getattr(task, "assignee", None),
            "status": native_status,
            "kanban_status": _status(native_status),
        }
