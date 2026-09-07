"""XNOBrain's product-level Kanban service.

Hermes owns the records and transitions.  This layer only projects Hermes'
execution states into the five product columns and translates user intents into
public Hermes operations.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..integrations import AgentAPIError
from ..integrations import kanban as kb_adapter
from ..integrations.kanban import KanbanUnavailable
from ..repositories import StoreError
from .base import ServiceError
from .constants import DEFAULT_TEAM_COORDINATOR_PROMPT

PRODUCT_STATUSES = ("backlog", "todo", "scheduled", "running", "done", "archived")
PRIORITY_TO_INT = {"low": 0, "medium": 1, "high": 2}
INT_TO_PRIORITY = {0: "low", 1: "medium", 2: "high"}
TEAM_META_PREFIX = "[xnobrain:team] "
TEAM_CANCEL_PREFIX = "[xnobrain:team-cancelled]"


def _iso(epoch: int | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _status(raw: str) -> str:
    if raw == "triage":
        return "backlog"
    if raw == "todo":
        return "todo"
    if raw == "scheduled":
        return "scheduled"
    if raw in {"ready", "running", "review"}:
        return "running"
    if raw in {"blocked", "done"}:
        return "done"
    if raw == "archived":
        return "archived"
    raise ServiceError(
        "The task has an unsupported status", status=503, code="kanban_contract_incompatible"
    )


def _detail(task: Any, *, has_schedule: bool = False) -> dict[str, Any]:
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
    reason = (
        "The task run failed"
        if raw == "blocked" and getattr(task, "last_failure_error", None)
        else None
    )
    return {"kind": kind, "label": label, "reason": reason}


def _allowed_moves(
    raw: str,
    *,
    failed: bool = False,
    has_schedule: bool = False,
) -> list[str]:
    """Return product columns reachable without hidden reclaim/reopen work."""
    if raw == "triage":
        return ["todo", "running", "archived"]
    if raw == "todo":
        return ["running", "archived"]
    if raw in {"ready", "running"}:
        return ["archived"]
    if raw == "scheduled":
        return ["archived"]
    if raw == "blocked":
        return []
    if raw == "review":
        return ["archived"]
    return []


class KanbanService:
    """Translate the HTTP product contract into Hermes Kanban operations."""

    def __init__(self, agents: Any | None = None, repository: Any | None = None):
        self.agents = agents
        self.repository = repository

    def _enable_agent_automation(self, agent_id: Any) -> None:
        """Make an assigned profile non-interactive before a worker can start."""
        name = str(agent_id or "").strip()
        if not name or self.agents is None or self.repository is None:
            return
        described = self.agents.describe_agent(name, include_memory=False)
        config = described.get("config") if isinstance(described, Mapping) else {}
        already_automatic = (
            isinstance(config, Mapping)
            and str(config.get("approval_mode") or "") == "off"
            and config.get("skills_write_approval") is False
            and config.get("memory_write_approval") is False
        )
        profile = Path(str(described["profile_path"]))
        config_path = profile / "config.yaml"
        snapshot_root = profile / "snapshots" / "config"
        if config_path.is_file() and (not already_automatic or not any(snapshot_root.rglob("*"))):
            payload = config_path.read_bytes()
            if profile == self.agents.root_profile:
                digest = hashlib.sha256(payload).hexdigest()
                snapshot = (
                    profile
                    / "snapshots"
                    / "config"
                    / "config"
                    / f"{time.time_ns()}-{digest[:12]}.yaml"
                )
                self.repository.atomic_write(snapshot, payload, mode=0o440, replace=False)
            else:
                self.repository.snapshot(name, "config", "config", payload)
        if already_automatic:
            return
        self.agents.update_config(
            name,
            {
                "approval_mode": "off",
                "skills_write_approval": False,
                "memory_write_approval": False,
            },
        )

    def _enabled_agent_skills(self, agent_id: str) -> list[str]:
        if self.agents is None:
            return []
        try:
            skills = self.agents.list_skills(agent_id).get("skills", [])
        except (AgentAPIError, StoreError):
            return []
        return sorted(
            {
                str(item.get("skill_id") or "").strip()
                for item in skills
                if item.get("enabled", True) and str(item.get("skill_id") or "").strip()
            }
        )

    def _validate_agent_skills(self, agent_id: Any, requested: Any) -> list[str] | None:
        if requested is None:
            return None
        selected = list(
            dict.fromkeys(str(item or "").strip() for item in requested if str(item or "").strip())
        )
        if not selected:
            return []
        assignee = str(agent_id or "").strip()
        if not assignee:
            raise ServiceError(
                "Choose an assignee before selecting skills",
                code="invalid_skills",
            )
        if self.agents is None:
            raise ServiceError(
                "Agent skills are unavailable", status=503, code="skills_unavailable"
            )
        try:
            inventory = self.agents.list_skills(assignee).get("skills", [])
        except (AgentAPIError, StoreError) as exc:
            raise ServiceError(str(exc), status=404, code="agent_not_found") from exc
        enabled = {
            str(item.get("skill_id") or "").strip()
            for item in inventory
            if item.get("installed", True) and item.get("enabled", True)
        }
        unavailable = [skill for skill in selected if skill not in enabled]
        if unavailable:
            raise ServiceError(
                "Selected skills are not enabled for this assignee: " + ", ".join(unavailable),
                status=409,
                code="skill_not_enabled",
            )
        return selected

    @staticmethod
    def _team_metadata(comments: list[Any]) -> tuple[dict[str, Any] | None, bool]:
        metadata = None
        cancelled = False
        for comment in comments:
            text = str(getattr(comment, "body", ""))
            if text.startswith(TEAM_META_PREFIX):
                try:
                    candidate = json.loads(text[len(TEAM_META_PREFIX) :])
                except (TypeError, json.JSONDecodeError):
                    continue
                if isinstance(candidate, dict):
                    metadata = candidate
            elif text.startswith(TEAM_CANCEL_PREFIX):
                cancelled = True
        return metadata, cancelled

    def _team_projection(
        self,
        conn: Any,
        metadata: Mapping[str, Any],
        *,
        cancelled: bool,
    ) -> dict[str, Any]:
        kb = self._ready()
        projected: list[dict[str, Any]] = []
        all_ids: list[str] = []
        for node in list(metadata.get("nodes") or []):
            if not isinstance(node, Mapping):
                continue
            task_id = str(node.get("task_id") or "")
            task = kb.get_task(conn, task_id)
            if task is None:
                continue
            raw_status = str(task.status)
            runs = kb_adapter.task_runs(conn, task_id)
            summary = getattr(task, "result", None) or next(
                (str(run.summary) for run in reversed(runs) if getattr(run, "summary", None)),
                None,
            )
            projected.append(
                {
                    "step_id": str(node.get("step_id") or task_id),
                    "task_id": task_id,
                    "title": str(task.title),
                    "agent_id": str(node.get("agent_id") or getattr(task, "assignee", "") or ""),
                    "role": str(node.get("role") or "worker"),
                    "needs": [str(item) for item in node.get("needs") or []],
                    "status": raw_status,
                    "kanban_status": _status(raw_status),
                    "summary": summary,
                }
            )
            all_ids.append(task_id)
        synthesis_id = str(metadata.get("synthesis_task_id") or "")
        synthesis = kb.get_task(conn, synthesis_id) if synthesis_id else None
        if synthesis is not None:
            raw_status = str(synthesis.status)
            runs = kb_adapter.task_runs(conn, synthesis_id)
            summary = getattr(synthesis, "result", None) or next(
                (str(run.summary) for run in reversed(runs) if getattr(run, "summary", None)),
                None,
            )
            projected.append(
                {
                    "step_id": "__synthesis__",
                    "task_id": synthesis_id,
                    "title": str(synthesis.title),
                    "agent_id": str(
                        metadata.get("synthesis_agent_id")
                        or getattr(synthesis, "assignee", "")
                        or ""
                    ),
                    "role": "synthesizer",
                    "needs": [str(item) for item in metadata.get("leaf_step_ids") or []],
                    "status": raw_status,
                    "kanban_status": _status(raw_status),
                    "summary": summary,
                }
            )
            all_ids.append(synthesis_id)
        terminal = {"done", "archived"}
        complete_count = sum(1 for node in projected if node["status"] in terminal)
        if cancelled:
            status = "cancelled"
            kanban_status = "done"
        elif projected and all(node["status"] in terminal for node in projected):
            status = "done"
            kanban_status = "done"
        elif any(node["status"] in {"ready", "running", "review"} for node in projected):
            status = "running"
            kanban_status = "running"
        elif any(node["status"] == "blocked" for node in projected):
            status = "blocked"
            kanban_status = "done"
        else:
            status = "todo"
            kanban_status = "todo"
        return {
            "id": str(metadata.get("team_id") or ""),
            "name": str(metadata.get("team_name") or "Agent team"),
            "orchestrator_id": str(metadata.get("orchestrator_id") or ""),
            "status": status,
            "kanban_status": kanban_status,
            "nodes": projected,
            "task_ids": all_ids,
            "synthesis_task_id": synthesis_id or None,
            "progress": 100
            if status == "done"
            else (round(100 * complete_count / len(projected)) if projected else 0),
            "cancelled": cancelled,
        }

    def delete_assignee_tasks(self, agent_id: str) -> int:
        try:
            return kb_adapter.delete_assignee_tasks(agent_id)
        except (KanbanUnavailable, ValueError) as exc:
            raise ServiceError(
                str(exc),
                status=503,
                code="kanban_not_ready",
            ) from exc

    def _workspace_for_assignee(
        self,
        assignee: Any,
        requested_kind: Any = None,
        requested_path: Any = None,
    ) -> tuple[str, str | None]:
        """Default assigned work to the agent's persistent workspace."""
        kind = str(requested_kind or "").strip()
        path = str(requested_path or "").strip() or None
        if kind:
            if kind == "dir" and path is None and assignee and self.agents is not None:
                path = str(self.agents.workspace_dir(assignee))
            return kind, path
        if path:
            return "dir", path
        if assignee and self.agents is not None:
            return "dir", str(self.agents.workspace_dir(assignee))
        return "scratch", None

    @staticmethod
    def _schedule_values(value: Any) -> tuple[str, int, int | None, str]:
        if not isinstance(value, Mapping):
            raise ServiceError("schedule is required for a scheduled task", code="invalid_schedule")
        recurrence = str(value.get("recurrence") or "once")
        timezone_name = str(value.get("timezone") or "Etc/UTC").strip()
        try:
            local_zone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ServiceError("schedule timezone is invalid", code="invalid_schedule") from exc
        raw_time = str(value.get("scheduled_at") or "").strip()
        if not raw_time:
            raise ServiceError("scheduled time is required", code="invalid_schedule")
        try:
            parsed = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ServiceError("scheduled time is invalid", code="invalid_schedule") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=local_zone)
        scheduled_at = int(parsed.astimezone(timezone.utc).timestamp())
        if scheduled_at <= int(datetime.now(timezone.utc).timestamp()):
            raise ServiceError("scheduled time must be in the future", code="invalid_schedule")
        interval_seconds: int | None = None
        if recurrence == "interval":
            try:
                interval_seconds = int(value.get("interval_minutes") or 0) * 60
            except (TypeError, ValueError) as exc:
                raise ServiceError("repeat interval is invalid", code="invalid_schedule") from exc
            if interval_seconds < 60:
                raise ServiceError(
                    "repeat interval must be at least one minute", code="invalid_schedule"
                )
        elif recurrence != "once":
            raise ServiceError("schedule recurrence is invalid", code="invalid_schedule")
        return recurrence, scheduled_at, interval_seconds, timezone_name

    @staticmethod
    def _public_board(item: Mapping[str, Any]) -> dict[str, Any]:
        """Return board metadata without exposing Hermes' local DB path."""
        return {key: value for key, value in item.items() if key != "db_path"}

    @staticmethod
    def _safe_event_payload(payload: Any) -> Any:
        """Keep audit facts while dropping paths and execution internals."""
        blocked = {
            "prompt",
            "body",
            "result",
            "output",
            "tool_args",
            "arguments",
            "stored_path",
            "workspace_path",
            "credentials",
            "api_key",
            "token",
        }
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

    def _task_dto(
        self,
        conn: Any,
        task: Any,
        *,
        board: str,
        include_detail: bool = False,
    ) -> dict[str, Any]:
        kb = self._ready()
        parent_ids = kb_adapter.task_dependencies(conn, task.id)
        children = kb_adapter.task_children(conn, task.id)
        parent_rows = []
        for parent_id in parent_ids:
            parent = kb.get_task(conn, parent_id)
            if parent is not None:
                parent_rows.append(
                    {
                        "id": parent.id,
                        "title": parent.title,
                        "status": str(parent.status),
                        "kanban_status": _status(parent.status),
                    }
                )
        comments = kb_adapter.task_comments(conn, task.id)
        team_metadata, team_cancelled = self._team_metadata(comments)
        attachments = kb_adapter.task_attachments(conn, task.id)
        runs = kb_adapter.task_runs(conn, task.id)
        raw_status = str(task.status)
        schedule = kb_adapter.task_schedule(conn, str(task.id))
        latest_summary = next(
            (str(run.summary) for run in reversed(runs) if getattr(run, "summary", None)),
            None,
        )
        progress = (
            100
            if raw_status in {"done", "archived"}
            and getattr(task, "completed_at", None) is not None
            else (50 if raw_status == "running" else 0)
        )
        result = {
            "id": str(task.id),
            "title": str(task.title),
            "description": str(task.body or ""),
            "status": raw_status,
            "kanban_status": _status(raw_status),
            "allowed_kanban_statuses": _allowed_moves(
                raw_status,
                failed=bool(getattr(task, "last_failure_error", None)),
                has_schedule=schedule is not None,
            ),
            "state_detail": _detail(task, has_schedule=schedule is not None),
            "priority": INT_TO_PRIORITY.get(int(getattr(task, "priority", 0) or 0), "medium"),
            "assignee": getattr(task, "assignee", None),
            "assignees": [task.assignee] if getattr(task, "assignee", None) else [],
            "skills": list(getattr(task, "skills", None) or []),
            "parents": parent_rows,
            "children": children,
            "tags": [],
            "progress": progress,
            "archived": raw_status == "archived",
            "block": "The task run failed"
            if raw_status == "blocked" and getattr(task, "last_failure_error", None)
            else None,
            "summary": getattr(task, "result", None) or latest_summary,
            "result": getattr(task, "result", None) or latest_summary,
            "workspace_kind": getattr(task, "workspace_kind", "scratch"),
            "workspace_path": None,
            "model_override": getattr(task, "model_override", None),
            "provider_override": getattr(task, "provider_override", None),
            "goal_mode": bool(getattr(task, "goal_mode", False)),
            "schedule": (
                {
                    "recurrence": str(schedule["recurrence"]),
                    "next_run_at": _iso(schedule.get("next_run_at")),
                    "interval_minutes": (
                        int(schedule["interval_seconds"]) // 60
                        if schedule.get("interval_seconds") is not None
                        else None
                    ),
                    "timezone": str(schedule["timezone"]),
                    "enabled": bool(schedule["enabled"]),
                    "occurrence_count": int(schedule["occurrence_count"]),
                    "last_run_at": _iso(schedule.get("last_run_at")),
                }
                if schedule is not None
                else None
            ),
            "comments": [
                self._comment_dto(item)
                for item in comments
                if not str(getattr(item, "body", "")).startswith(
                    (TEAM_META_PREFIX, TEAM_CANCEL_PREFIX)
                )
            ],
            "attachments": [self._attachment_dto(item) for item in attachments],
            "runs": [self._run_dto(item) for item in runs],
            "worker": self._worker_dto(task),
            "created_at": _iso(getattr(task, "created_at", None)),
            "updated_at": _iso(
                getattr(task, "completed_at", None)
                or getattr(task, "started_at", None)
                or getattr(task, "created_at", None)
            ),
            "board_slug": board,
            "revision": str(getattr(task, "updated_at", None) or getattr(task, "created_at", "")),
        }
        if include_detail:
            events = kb_adapter.task_events(conn, task.id)
            activity = kb_adapter.safe_worker_activity(task.id, board=board)
            session_id = activity.get("session_id")
            assignee = getattr(task, "assignee", None)
            result["events"] = [
                self._event_dto(item, task)
                for item in events
                if str(getattr(item, "kind", "")) != "heartbeat"
            ]
            result["worker_activity"] = activity
            result["conversation"] = (
                {
                    "id": session_id,
                    "agent_id": assignee,
                    "url": (
                        f"/agents/{quote(str(assignee), safe='')}/sessions/"
                        f"{quote(str(session_id), safe='')}"
                    ),
                }
                if session_id and assignee
                else None
            )
        else:
            result["events"] = []
            result["worker_activity"] = None
            result["conversation"] = None
        result["team"] = None
        if team_metadata is not None:
            team = self._team_projection(conn, team_metadata, cancelled=team_cancelled)
            if raw_status == "archived":
                team["status"] = "archived"
                team["kanban_status"] = "archived"
            elif raw_status == "triage":
                team["status"] = "backlog"
                team["kanban_status"] = "backlog"
            result["team"] = team
            result["kanban_status"] = team["kanban_status"]
            if team["status"] == "backlog":
                result["allowed_kanban_statuses"] = ["todo", "running", "archived"]
            elif team["status"] == "todo":
                result["allowed_kanban_statuses"] = ["running", "archived"]
            elif team["status"] in {"done", "blocked", "cancelled"}:
                result["allowed_kanban_statuses"] = []
            else:
                result["allowed_kanban_statuses"] = []
            result["state_detail"] = {
                "kind": team["status"],
                "label": team["status"].replace("_", " ").title(),
                "reason": None,
            }
            result["assignee"] = team["orchestrator_id"] or None
            result["assignees"] = list(
                dict.fromkeys(node["agent_id"] for node in team["nodes"] if node["agent_id"])
            )
            result["progress"] = team["progress"]
            result["block"] = (
                "A team stage needs attention" if team["status"] == "blocked" else None
            )
            synthesis = next(
                (node for node in team["nodes"] if node["step_id"] == "__synthesis__"),
                None,
            )
            result["summary"] = (
                "Team run cancelled"
                if team_cancelled
                else synthesis.get("summary")
                if synthesis and synthesis["status"] == "done"
                else None
            )
            result["result"] = result["summary"]
        return result

    @staticmethod
    def _comment_dto(comment: Any) -> dict[str, Any]:
        return {
            "id": int(comment.id),
            "task_id": str(comment.task_id),
            "author": str(comment.author),
            "body": str(comment.body),
            "created_at": _iso(comment.created_at),
        }

    @staticmethod
    def _attachment_dto(attachment: Any) -> dict[str, Any]:
        return {
            "id": int(attachment.id),
            "task_id": str(attachment.task_id),
            "filename": str(attachment.filename),
            "content_type": attachment.content_type,
            "size": int(attachment.size),
            "uploaded_by": attachment.uploaded_by,
            "created_at": _iso(attachment.created_at),
        }

    @staticmethod
    def _run_dto(run: Any) -> dict[str, Any]:
        return {
            "id": int(run.id),
            "task_id": str(run.task_id),
            "profile": run.profile,
            "status": run.status,
            "outcome": run.outcome,
            "summary": run.summary,
            "started_at": _iso(run.started_at),
            "ended_at": _iso(run.ended_at),
        }

    @staticmethod
    def _worker_dto(task: Any) -> dict[str, Any] | None:
        if str(getattr(task, "status", "")) != "running":
            return None
        return {
            "pid": getattr(task, "worker_pid", None),
            "heartbeat_at": _iso(getattr(task, "last_heartbeat_at", None)),
            "run_id": getattr(task, "current_run_id", None),
        }

    def list_boards(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        try:
            boards = kb_adapter.list_boards(include_archived=include_archived)
        except (KanbanUnavailable, ValueError) as exc:
            raise ServiceError(str(exc), status=503, code="kanban_not_ready") from exc
        current = kb_adapter.current_board()
        return [
            {
                **self._public_board(item),
                "id": str(item.get("slug") or "default"),
                "current": str(item.get("slug") or "default") == current,
            }
            for item in boards
        ]

    def active_agent_ids(self) -> set[str]:
        """Return assignees whose Hermes task is currently being executed."""
        kb = self._ready()
        active: set[str] = set()
        for board in self.list_boards(include_archived=False):
            with kb_adapter.connection(str(board["id"])) as conn:
                for task in kb.list_tasks(conn, include_archived=False):
                    # Product ``running`` also includes ready/review tasks. Only
                    # Hermes' raw running state means a worker owns the task now.
                    if str(getattr(task, "status", "")) != "running":
                        continue
                    assignee = str(getattr(task, "assignee", "") or "").strip()
                    if assignee:
                        active.add(assignee)
        return active

    def get_board(self, slug: str, *, include_archived: bool = False) -> dict[str, Any]:
        normalized = self._board(slug)
        try:
            boards = kb_adapter.list_boards(include_archived=True)
        except (KanbanUnavailable, ValueError) as exc:
            raise ServiceError(str(exc), status=503, code="kanban_not_ready") from exc
        item = next(
            (item for item in boards if str(item.get("slug") or "default") == normalized), None
        )
        board = {**self._public_board(item), "id": normalized} if item is not None else None
        if board is None or (board.get("archived") and not include_archived):
            raise ServiceError("board not found", status=404, code="board_not_found")
        return board

    def board_stats(self, board: str) -> dict[str, Any]:
        normalized = self._board(board)
        kb = self._ready()
        with kb_adapter.connection(normalized) as conn:
            member_ids = kb_adapter.team_member_task_ids(conn)
            rows = kb.list_tasks(conn, include_archived=True)
            counts = {status: 0 for status in PRODUCT_STATUSES}
            blocked = 0
            for task in rows:
                if str(task.id) in member_ids:
                    continue
                product_status = _status(str(task.status))
                counts[product_status] += 1
                if str(task.status) == "blocked":
                    blocked += 1
        current = sum(counts[status] for status in PRODUCT_STATUSES if status != "archived")
        return {
            "board_slug": normalized,
            "total": current + counts["archived"],
            "current": current,
            "completed": max(0, counts["done"] - blocked),
            "archived": counts["archived"],
            "running": counts["running"],
            "blocked": blocked,
            "by_status": counts,
        }

    def _list_task_dtos(
        self,
        conn: Any,
        board: str,
        *,
        include_archived: bool = False,
        assignee: str | None = None,
        status: str | None = None,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        kb = self._ready()
        raw = kb.list_tasks(
            conn, assignee=assignee or None, include_archived=include_archived, order_by="updated"
        )
        rows = [self._task_dto(conn, task, board=board) for task in raw]
        member_ids = {
            task_id for row in rows if row.get("team") for task_id in row["team"]["task_ids"]
        }
        rows = [row for row in rows if row["id"] not in member_ids]
        if status:
            if status not in PRODUCT_STATUSES:
                raise ServiceError("invalid Kanban status", code="invalid_request")
            rows = [row for row in rows if row["kanban_status"] == status]
        query = (search or "").strip().lower()
        if query:
            rows = [
                row
                for row in rows
                if query
                in " ".join(
                    [row["id"], row["title"], row["description"], row.get("assignee") or ""]
                ).lower()
            ]
        return rows

    def list_tasks(self, board: str, query: Mapping[str, Any]) -> dict[str, Any]:
        normalized = self._board(board)
        try:
            offset = max(0, int(query.get("offset") or 0))
            limit = min(200, max(1, int(query.get("limit") or 200)))
        except (TypeError, ValueError) as exc:
            raise ServiceError("limit and offset must be integers", code="invalid_request") from exc
        status = str(query.get("status") or "").strip() or None
        if status and status not in PRODUCT_STATUSES:
            raise ServiceError("invalid Kanban status", code="invalid_request")
        include_archived = str(query.get("include_archived", "false")).lower() == "true"
        # The progressive UI requests either current work or the archived tab.
        # Other status filters retain the exact product projection path.
        if status not in {None, "archived"}:
            with kb_adapter.connection(normalized) as conn:
                tasks = self._list_task_dtos(
                    conn,
                    normalized,
                    include_archived=include_archived,
                    assignee=query.get("assignee"),
                    status=status,
                    search=query.get("search"),
                )
            total = len(tasks)
            return {
                "board_slug": normalized,
                "tasks": tasks[offset : offset + limit],
                "total": total,
                "offset": offset,
                "limit": limit,
            }
        with kb_adapter.connection(normalized) as conn:
            raw, total = kb_adapter.task_page(
                conn,
                include_archived=include_archived,
                archived_only=status == "archived",
                assignee=str(query.get("assignee") or "").strip() or None,
                search=str(query.get("search") or "").strip() or None,
                offset=offset,
                limit=limit,
            )
            tasks = [self._task_dto(conn, task, board=normalized) for task in raw]
        return {
            "board_slug": normalized,
            "tasks": tasks,
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    def get_task(self, board: str, task_id: str) -> dict[str, Any]:
        normalized = self._board(board)
        kb = self._ready()
        with kb_adapter.connection(normalized) as conn:
            task = kb.get_task(conn, task_id)
            if task is None:
                raise ServiceError("task not found", status=404, code="task_not_found")
            return self._task_dto(conn, task, board=normalized, include_detail=True)

    def create_task(
        self, board: str, body: Mapping[str, Any], *, created_by: str = "user"
    ) -> dict[str, Any]:
        normalized = self._board(board)
        kb = self._ready()
        title = str(body.get("title") or "").strip()
        if not title:
            raise ServiceError("title is required", code="invalid_request")
        description = str(body.get("description") or "").strip()
        if not description:
            raise ServiceError("description is required", code="invalid_request")
        status = str(body.get("status") or "todo")
        if status not in {"backlog", "todo", "scheduled"}:
            raise ServiceError(
                "new tasks may start in Backlog, Todo, or Scheduled", code="invalid_request"
            )
        schedule_values = (
            self._schedule_values(body.get("schedule")) if status == "scheduled" else None
        )
        if status != "scheduled" and body.get("schedule") is not None:
            raise ServiceError("choose Scheduled to set a task schedule", code="invalid_schedule")
        team_id = str(body.get("team_id") or "").strip()
        if team_id and body.get("assignee"):
            raise ServiceError("choose either an agent or a team", code="invalid_request")
        if team_id and status == "scheduled":
            raise ServiceError(
                "Scheduled tasks must use one agent",
                code="invalid_schedule",
            )
        selected_skills = (
            None
            if team_id
            else self._validate_agent_skills(body.get("assignee"), body.get("skills"))
        )
        try:
            workspace_kind, workspace_path = self._workspace_for_assignee(
                body.get("assignee"),
                body.get("workspace_kind"),
                body.get("workspace_path"),
            )
        except ValueError as exc:
            raise ServiceError(str(exc), code="invalid_workspace") from exc
        with kb_adapter.connection(normalized) as conn:
            if team_id:
                return self._create_team_task(
                    conn,
                    normalized,
                    team_id=team_id,
                    title=title,
                    description=description,
                    priority=PRIORITY_TO_INT.get(str(body.get("priority") or "medium"), 1),
                    created_by=created_by,
                    status=status,
                    idempotency_key=body.get("idempotency_key"),
                )
            task_id = kb_adapter.create_task(
                conn,
                title=title,
                body=description,
                assignee=body.get("assignee"),
                created_by=created_by,
                priority=PRIORITY_TO_INT.get(str(body.get("priority") or "medium"), 1),
                parents=body.get("parents") or (),
                triage=status == "backlog",
                idempotency_key=body.get("idempotency_key"),
                workspace_kind=workspace_kind,
                workspace_path=workspace_path,
                skills=selected_skills,
                model_override=body.get("model_override"),
                provider_override=body.get("provider_override"),
                goal_mode=bool(body.get("goal_mode", False)),
                initial_status="running",
                board=normalized,
            )
            self._enable_agent_automation(body.get("assignee"))
            task = kb.get_task(conn, task_id)
            if task is None:
                raise ServiceError(
                    "The created task could not be loaded",
                    status=503,
                    code="kanban_contract_incompatible",
                )
            # Hermes promotes parent-free tasks to ready on creation. Preserve
            # an explicit Todo choice; ready remains part of In Progress.
            if status == "todo" and not kb_adapter.park_task_in_todo(conn, task_id):
                raise ServiceError(
                    "task could not be parked in Todo",
                    status=409,
                    code="invalid_transition",
                )
            if status == "todo":
                task = kb.get_task(conn, task_id)
            if schedule_values is not None:
                if not kb.schedule_task(
                    conn,
                    task_id,
                    reason="Scheduled from XNOBrain",
                ):
                    raise ServiceError(
                        "task could not be scheduled",
                        status=409,
                        code="invalid_schedule",
                    )
                task = kb.get_task(conn, task_id)
            if schedule_values is not None:
                recurrence, scheduled_at, interval_seconds, timezone_name = schedule_values
                kb_adapter.put_task_schedule(
                    conn,
                    task_id,
                    recurrence=recurrence,
                    next_run_at=scheduled_at,
                    interval_seconds=interval_seconds,
                    timezone_name=timezone_name,
                )
                task = kb.get_task(conn, task_id)
            return self._task_dto(conn, task, board=normalized)

    def _create_team_task(
        self,
        conn: Any,
        board: str,
        *,
        team_id: str,
        title: str,
        description: str,
        priority: int,
        created_by: str,
        status: str,
        idempotency_key: Any,
    ) -> dict[str, Any]:
        if self.repository is None:
            raise ServiceError("team tasks are unavailable", status=503, code="kanban_not_ready")
        try:
            team = self.repository.get_team(team_id)
        except StoreError as exc:
            raise ServiceError(
                str(exc),
                status=exc.status,
                code=exc.code,
            ) from exc
        if not team.get("enabled", True):
            raise ServiceError("team is disabled", status=409, code="team_disabled")
        workflow = list(team.get("workflow") or [])
        if not workflow:
            workflow = [
                {
                    "id": f"worker-{index + 1}",
                    "task": description,
                    "agent_id": member.get("agent_id"),
                    "role": member.get("role") or "worker",
                    "needs": [],
                }
                for index, member in enumerate(team.get("members") or [])
                if member.get("enabled", True)
            ]
        if not workflow:
            raise ServiceError(
                "team has no enabled workflow", status=409, code="team_has_no_workers"
            )
        step_ids = [str(step.get("id") or "") for step in workflow]
        if any(not step_id for step_id in step_ids) or len(set(step_ids)) != len(step_ids):
            raise ServiceError("team workflow is invalid", status=409, code="invalid_team")
        known_steps = set(step_ids)
        for step in workflow:
            needs = [str(item) for item in step.get("needs") or []]
            if any(parent not in known_steps for parent in needs):
                raise ServiceError(
                    "team workflow dependency does not exist", status=409, code="invalid_team"
                )
            if not str(step.get("agent_id") or ""):
                raise ServiceError(
                    "team workflow step has no agent", status=409, code="invalid_team"
                )
        ordered: list[dict[str, Any]] = []
        remaining = list(workflow)
        completed: set[str] = set()
        while remaining:
            ready = [
                step
                for step in remaining
                if all(str(parent) in completed for parent in step.get("needs") or [])
            ]
            if not ready:
                raise ServiceError(
                    "team workflow contains a dependency cycle", status=409, code="invalid_team"
                )
            ordered.extend(ready)
            completed.update(str(step["id"]) for step in ready)
            remaining = [step for step in remaining if step not in ready]
        workflow = ordered
        step_ids = [str(step["id"]) for step in workflow]
        root_id = kb_adapter.create_task(
            conn,
            title=title,
            body=description,
            assignee=None,
            created_by=created_by,
            priority=priority,
            parents=(),
            idempotency_key=idempotency_key,
            workspace_kind="scratch",
            triage=status == "backlog",
            initial_status="running",
            board=board,
        )
        kb = self._ready()
        if status == "todo" and not kb_adapter.park_task_in_todo(conn, root_id):
            raise ServiceError(
                "team workflow could not be parked",
                status=409,
                code="invalid_transition",
            )
        task_ids: dict[str, str] = {}
        nodes: list[dict[str, Any]] = []
        root_parent_id = root_id
        orchestrator = str(team.get("orchestrator_id") or "")
        coordinator_prompt = (
            str(team.get("coordinator_prompt") or "").strip() or DEFAULT_TEAM_COORDINATOR_PROMPT
        )
        if coordinator_prompt:
            if not orchestrator:
                raise ServiceError("team has no orchestrator", status=409, code="invalid_team")
            self._enable_agent_automation(orchestrator)
            workspace_kind, workspace_path = self._workspace_for_assignee(orchestrator)
            coordinator_skills = team.get("coordinator_skills")
            if coordinator_skills is None:
                coordinator_skills = self._enabled_agent_skills(orchestrator)
            root_parent_id = kb_adapter.create_task(
                conn,
                title=f"{title} · Coordination",
                body=f"{coordinator_prompt}\n\nTeam objective:\n{description}",
                assignee=orchestrator,
                created_by=f"team:{team_id}",
                priority=priority,
                parents=[root_id],
                workspace_kind=workspace_kind,
                workspace_path=workspace_path,
                skills=list(coordinator_skills or []) or None,
                initial_status="running",
                board=board,
            )
            nodes.append(
                {
                    "step_id": "__coordination__",
                    "task_id": root_parent_id,
                    "agent_id": orchestrator,
                    "role": "coordinator",
                    "needs": [],
                }
            )
        for step in workflow:
            step_id = str(step["id"])
            needs = [str(item) for item in step.get("needs") or []]
            if any(parent not in task_ids for parent in needs):
                raise ServiceError(
                    "team workflow dependencies are not in topological order",
                    status=409,
                    code="invalid_team",
                )
            agent_id = str(step.get("agent_id") or "")
            if not agent_id:
                raise ServiceError(
                    "team workflow step has no agent", status=409, code="invalid_team"
                )
            self._enable_agent_automation(agent_id)
            role = str(step.get("role") or "worker")
            instruction = str(step.get("task") or description).strip()
            workspace_kind, workspace_path = self._workspace_for_assignee(agent_id)
            step_skills = step.get("skills")
            if step_skills is None:
                step_skills = self._enabled_agent_skills(agent_id)
            task_id = kb_adapter.create_task(
                conn,
                title=f"{title} · {role}",
                body=(
                    f"Team objective:\n{description}\n\n"
                    f"Your role: {role}\nStage instructions:\n{instruction}"
                ),
                assignee=agent_id,
                created_by=f"team:{team_id}",
                priority=priority,
                parents=[task_ids[item] for item in needs] or [root_parent_id],
                workspace_kind=workspace_kind,
                workspace_path=workspace_path,
                skills=list(step_skills or []) or None,
                initial_status="running",
                board=board,
            )
            task_ids[step_id] = task_id
            nodes.append(
                {
                    "step_id": step_id,
                    "task_id": task_id,
                    "agent_id": agent_id,
                    "role": role,
                    "needs": needs or (["__coordination__"] if coordinator_prompt else []),
                }
            )
        depended_on = {item for step in workflow for item in step.get("needs") or []}
        leaf_ids = [step_id for step_id in step_ids if step_id not in depended_on]
        if not orchestrator:
            raise ServiceError("team has no orchestrator", status=409, code="invalid_team")
        synthesis_agent = str(team.get("synthesis_agent_id") or orchestrator)
        self._enable_agent_automation(synthesis_agent)
        workspace_kind, workspace_path = self._workspace_for_assignee(synthesis_agent)
        synthesis_skills = team.get("synthesis_skills")
        if synthesis_skills is None:
            synthesis_skills = self._enabled_agent_skills(synthesis_agent)
        synthesis_id = kb_adapter.create_task(
            conn,
            title=f"{title} · Synthesis",
            body=(
                f"{team.get('synthesis_instruction') or 'Synthesize the completed team stages into one final answer.'}\n\n"
                f"Original objective:\n{description}"
            ),
            assignee=synthesis_agent,
            created_by=f"team:{team_id}",
            priority=priority,
            parents=[task_ids[item] for item in leaf_ids],
            workspace_kind=workspace_kind,
            workspace_path=workspace_path,
            skills=list(synthesis_skills or []) or None,
            initial_status="running",
            board=board,
        )
        metadata = {
            "version": 1,
            "team_id": team_id,
            "team_name": str(team.get("name") or team_id),
            "orchestrator_id": orchestrator,
            "synthesis_agent_id": synthesis_agent,
            "nodes": nodes,
            "leaf_step_ids": leaf_ids,
            "synthesis_task_id": synthesis_id,
        }
        kb.add_comment(
            conn,
            root_id,
            "xnobrain",
            TEAM_META_PREFIX + json.dumps(metadata, separators=(",", ":")),
        )
        root = kb.get_task(conn, root_id)
        return self._task_dto(conn, root, board=board, include_detail=True)

    def cancel_team_task(self, board: str, task_id: str) -> dict[str, Any]:
        normalized = self._board(board)
        kb = self._ready()
        with kb_adapter.connection(normalized) as conn:
            task = kb.get_task(conn, task_id)
            if task is None:
                raise ServiceError("task not found", status=404, code="task_not_found")
            comments = kb_adapter.task_comments(conn, task_id)
            metadata, cancelled = self._team_metadata(comments)
            if metadata is None:
                raise ServiceError("task is not a team run", status=409, code="invalid_request")
            if cancelled:
                return self._task_dto(conn, task, board=normalized, include_detail=True)
            projection = self._team_projection(conn, metadata, cancelled=False)
            for member_id in projection["task_ids"]:
                member = kb.get_task(conn, member_id)
                if member is None or str(member.status) in {"done", "archived"}:
                    continue
                if str(member.status) == "running":
                    kb.reclaim_task(conn, member_id, reason="Team run cancelled from XNOBrain")
                kb.archive_task(conn, member_id)
            kb.add_comment(conn, task_id, "xnobrain", TEAM_CANCEL_PREFIX)
            return self._task_dto(conn, task, board=normalized, include_detail=True)

    def cancel_task(self, board: str, task_id: str) -> dict[str, Any]:
        """Stop one active worker and close the task without allowing respawn."""
        normalized = self._board(board)
        kb = self._ready()
        with kb_adapter.connection(normalized) as conn:
            task = kb.get_task(conn, task_id)
            if task is None:
                raise ServiceError("task not found", status=404, code="task_not_found")
            if str(task.status) != "running":
                raise ServiceError(
                    "only a running task can be cancelled",
                    status=409,
                    code="task_not_running",
                )
            comments = kb_adapter.task_comments(conn, task_id)
            metadata, _ = self._team_metadata(comments)
            if metadata is not None:
                raise ServiceError(
                    "cancel this task through the team run action",
                    status=409,
                    code="invalid_request",
                )
            if not kb.reclaim_task(
                conn,
                task_id,
                reason="Task cancelled from XNOBrain",
            ):
                raise ServiceError(
                    "task could not be cancelled",
                    status=409,
                    code="cancel_failed",
                )
            if not kb.complete_task(conn, task_id, summary="Task cancelled"):
                raise ServiceError(
                    "task was stopped but could not be closed",
                    status=409,
                    code="cancel_failed",
                )
            task = kb.get_task(conn, task_id)
            return self._task_dto(conn, task, board=normalized, include_detail=True)

    def patch_task(self, board: str, task_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        normalized = self._board(board)
        kb = self._ready()
        with kb_adapter.connection(normalized) as conn:
            task = kb.get_task(conn, task_id)
            if task is None:
                raise ServiceError("task not found", status=404, code="task_not_found")
            try:
                selected_skills = self._validate_agent_skills(
                    getattr(task, "assignee", None), body.get("skills")
                )
                ok = kb_adapter.update_task_fields(
                    conn,
                    task_id,
                    title=body.get("title"),
                    body=body.get("description"),
                    priority=(
                        PRIORITY_TO_INT[str(body["priority"])]
                        if body.get("priority") is not None
                        else None
                    ),
                    skills=selected_skills,
                )
            except ValueError as exc:
                raise ServiceError(str(exc), code="invalid_request") from exc
            except RuntimeError as exc:
                raise ServiceError(str(exc), status=409, code="stale_task") from exc
            if not ok:
                raise ServiceError("task not found", status=404, code="task_not_found")
            if body.get("schedule") is not None:
                recurrence, scheduled_at, interval_seconds, timezone_name = self._schedule_values(
                    body["schedule"]
                )
                current = kb.get_task(conn, task_id)
                if current is None:
                    raise ServiceError("task not found", status=404, code="task_not_found")
                if str(current.status) == "triage":
                    if not kb.specify_triage_task(conn, task_id, author="user"):
                        raise ServiceError(
                            "task could not be scheduled", status=409, code="invalid_schedule"
                        )
                    current = kb.get_task(conn, task_id)
                if str(current.status) != "scheduled" and not kb.schedule_task(
                    conn,
                    task_id,
                    reason="Schedule updated from XNOBrain",
                ):
                    raise ServiceError(
                        "task could not be scheduled", status=409, code="invalid_schedule"
                    )
                kb_adapter.put_task_schedule(
                    conn,
                    task_id,
                    recurrence=recurrence,
                    next_run_at=scheduled_at,
                    interval_seconds=interval_seconds,
                    timezone_name=timezone_name,
                )
            task = kb.get_task(conn, task_id)
            return self._task_dto(conn, task, board=normalized, include_detail=True)

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
            existing_skills = list(getattr(task, "skills", None) or [])
            if existing_skills:
                self._validate_agent_skills(body.get("assignee"), existing_skills)
            try:
                ok = kb.reassign_task(
                    conn,
                    task_id,
                    body.get("assignee"),
                    reclaim_first=bool(body.get("reclaim_first", False)),
                    reason=body.get("reason"),
                )
            except (ValueError, RuntimeError) as exc:
                raise ServiceError(str(exc), status=409, code="assignment_invalid") from exc
            if not ok:
                raise ServiceError(
                    "task cannot be reassigned while active", status=409, code="assignment_invalid"
                )
            try:
                workspace_kind, workspace_path = self._workspace_for_assignee(body.get("assignee"))
                kb_adapter.update_task_workspace(
                    conn,
                    task_id,
                    workspace_kind=workspace_kind,
                    workspace_path=workspace_path,
                )
            except ValueError as exc:
                raise ServiceError(str(exc), status=409, code="assignment_invalid") from exc
            self._enable_agent_automation(body.get("assignee"))
            task = kb.get_task(conn, task_id)
            if task is None:
                raise ServiceError("task not found", status=404, code="task_not_found")
            return self._task_dto(conn, task, board=normalized)

    def move_task(
        self, board: str, task_id: str, target: str, *, reason: str | None = None
    ) -> dict[str, Any]:
        normalized = self._board(board)
        kb = self._ready()
        with kb_adapter.connection(normalized) as conn:
            task = kb.get_task(conn, task_id)
            if task is None:
                raise ServiceError("task not found", status=404, code="task_not_found")
            raw = str(task.status)
            team_metadata, team_cancelled = self._team_metadata(
                kb_adapter.task_comments(conn, task_id)
            )
            has_schedule = kb_adapter.task_schedule(conn, task_id) is not None
            if raw == "scheduled" and has_schedule and target != "archived":
                raise ServiceError(
                    "Scheduled tasks are controlled by their schedule",
                    status=409,
                    code="invalid_transition",
                )
            if target not in _allowed_moves(
                raw,
                failed=bool(getattr(task, "last_failure_error", None)),
                has_schedule=has_schedule,
            ):
                raise ServiceError(
                    "task cannot make that transition",
                    status=409,
                    code="invalid_transition",
                )
            try:
                if target == "archived":
                    ok = kb.archive_task(conn, task_id)
                elif target == "done":
                    if raw in {"blocked", "scheduled"}:
                        ok = kb.unblock_task(conn, task_id)
                        if ok:
                            ok = kb.complete_task(
                                conn, task_id, summary=reason or "Completed from XNOBrain"
                            )
                    else:
                        ok = kb.complete_task(
                            conn, task_id, summary=reason or "Completed from XNOBrain"
                        )
                elif target == "todo":
                    if raw == "triage":
                        ok = kb.specify_triage_task(conn, task_id, author="user")
                        if ok:
                            ok = kb_adapter.park_task_in_todo(conn, task_id)
                    elif raw == "blocked":
                        ok = kb.unblock_task(conn, task_id)
                        if ok:
                            ok = kb_adapter.park_task_in_todo(conn, task_id)
                    elif raw in {"todo", "ready"}:
                        ok = raw == "todo" or kb_adapter.park_task_in_todo(conn, task_id)
                    else:
                        ok = raw == "scheduled" and not has_schedule
                elif target == "running":
                    if team_metadata is not None:
                        projection = self._team_projection(
                            conn,
                            team_metadata,
                            cancelled=team_cancelled,
                        )
                        if projection["status"] != "todo":
                            ok = projection["status"] == "running"
                        else:
                            if raw == "todo":
                                promoted, _ = kb.promote_task(conn, task_id, actor="xnobrain")
                                ok = promoted
                            else:
                                ok = (
                                    kb.unblock_task(conn, task_id)
                                    if raw in {"blocked", "scheduled"}
                                    else raw == "ready"
                                )
                            if ok:
                                ok = kb.complete_task(
                                    conn,
                                    task_id,
                                    summary=reason or "Team workflow started",
                                )
                    elif raw == "review":
                        ok = kb.claim_review_task(conn, task_id, claimer="xnobrain") is not None
                    elif raw == "blocked":
                        ok = kb.unblock_task(conn, task_id)
                    elif raw == "triage":
                        specified = kb.specify_triage_task(conn, task_id, author="user")
                        promoted, promote_reason = (
                            kb.promote_task(conn, task_id, actor="xnobrain")
                            if specified and hasattr(kb, "promote_task")
                            else (False, None)
                        )
                        # The dispatcher may recompute the newly specified
                        # parent-free todo as ready between these two public
                        # Hermes operations.  That is the requested outcome,
                        # not a transition conflict.
                        if not promoted and specified:
                            current = kb.get_task(conn, task_id)
                            promoted = current is not None and str(current.status) == "ready"
                        if not promoted and promote_reason:
                            raise ServiceError(
                                promote_reason,
                                status=409,
                                code="invalid_transition",
                            )
                        ok = promoted
                    elif raw == "todo":
                        promoted, promote_reason = (
                            kb.promote_task(conn, task_id, actor="xnobrain")
                            if hasattr(kb, "promote_task")
                            else (False, None)
                        )
                        if not promoted and promote_reason:
                            raise ServiceError(
                                promote_reason,
                                status=409,
                                code="invalid_transition",
                            )
                        ok = promoted
                    elif raw == "scheduled" and not has_schedule:
                        ok = kb.unblock_task(conn, task_id)
                    elif raw in {"ready", "running"}:
                        ok = True
                    else:
                        ok = False
                elif target == "backlog":
                    if raw == "blocked" and getattr(task, "last_failure_error", None):
                        ok = kb_adapter.return_failed_task_to_triage(conn, task_id)
                    elif raw in {"todo", "ready", "scheduled"} and not has_schedule:
                        ok = kb_adapter.return_waiting_task_to_triage(conn, task_id)
                    else:
                        raise ServiceError(
                            "active tasks cannot be returned to Backlog",
                            status=409,
                            code="invalid_transition",
                        )
                else:
                    raise ServiceError("invalid Kanban status", code="invalid_request")
            except ServiceError:
                raise
            except (ValueError, RuntimeError) as exc:
                raise ServiceError(str(exc), status=409, code="invalid_transition") from exc
            if not ok:
                raise ServiceError(
                    "task cannot make that transition", status=409, code="invalid_transition"
                )
            task = kb.get_task(conn, task_id)
            return self._task_dto(conn, task, board=normalized)

    def schedule_action(self, board: str, task_id: str, action: str) -> dict[str, Any]:
        normalized = self._board(board)
        kb = self._ready()
        with kb_adapter.connection(normalized) as conn:
            task = kb.get_task(conn, task_id)
            if task is None:
                raise ServiceError("task not found", status=404, code="task_not_found")
            schedule = kb_adapter.task_schedule(conn, task_id)
            if schedule is None:
                raise ServiceError("task schedule not found", status=404, code="schedule_not_found")
            if str(task.status) == "archived":
                raise ServiceError(
                    "archived schedules cannot be changed",
                    status=409,
                    code="invalid_schedule",
                )
            if (
                action == "resume"
                and str(schedule["recurrence"]) == "once"
                and schedule.get("next_run_at") is None
            ):
                raise ServiceError(
                    "one-time schedule has already run",
                    status=409,
                    code="invalid_schedule",
                )
            try:
                if action == "pause":
                    kb_adapter.set_task_schedule_enabled(conn, task_id, False)
                elif action == "resume":
                    kb_adapter.set_task_schedule_enabled(conn, task_id, True)
                elif action == "run_now":
                    kb_adapter.run_task_schedule_now(conn, task_id, board=normalized)
                else:
                    raise ServiceError("schedule action is invalid", code="invalid_request")
            except ValueError as exc:
                raise ServiceError(str(exc), status=409, code="invalid_schedule") from exc
            task = kb.get_task(conn, task_id)
            if task is None:
                raise ServiceError("task not found", status=404, code="task_not_found")
            return self._task_dto(conn, task, board=normalized, include_detail=True)

    def archive_task(self, board: str, task_id: str, *, unarchive: bool = False) -> dict[str, Any]:
        normalized = self._board(board)
        kb = self._ready()
        with kb_adapter.connection(normalized) as conn:
            task = kb.get_task(conn, task_id)
            if task is None:
                raise ServiceError("task not found", status=404, code="task_not_found")
            if unarchive:
                raise ServiceError(
                    "Archived tasks cannot be restored",
                    status=501,
                    code="kanban_contract_incompatible",
                )
            comments = kb_adapter.task_comments(conn, task_id)
            metadata, cancelled = self._team_metadata(comments)
            if metadata is not None:
                projection = self._team_projection(conn, metadata, cancelled=cancelled)
                if projection["status"] not in {"done", "blocked", "cancelled"}:
                    raise ServiceError(
                        "Cancel the active team run before archiving it",
                        status=409,
                        code="invalid_transition",
                    )
                for member_id in projection["task_ids"]:
                    member = kb.get_task(conn, member_id)
                    if member is not None and str(member.status) != "archived":
                        kb.archive_task(conn, member_id)
            if not kb.archive_task(conn, task_id):
                raise ServiceError(
                    "task could not be archived", status=409, code="invalid_transition"
                )
            if kb_adapter.task_schedule(conn, task_id) is not None:
                kb_adapter.set_task_schedule_enabled(conn, task_id, False)
            return self._task_dto(conn, kb.get_task(conn, task_id), board=normalized)

    def add_comment(self, board: str, task_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        normalized = self._board(board)
        kb = self._ready()
        with kb_adapter.connection(normalized) as conn:
            try:
                comment_id = kb.add_comment(
                    conn, task_id, str(body.get("author") or "user"), str(body.get("body") or "")
                )
            except ValueError as exc:
                raise ServiceError(str(exc), code="invalid_request") from exc
            comment = next(
                (item for item in kb.list_comments(conn, task_id) if item.id == comment_id), None
            )
            if comment is None:
                raise ServiceError(
                    "The comment could not be saved",
                    status=503,
                    code="kanban_contract_incompatible",
                )
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
                    raise ServiceError(
                        "dependency link not found", status=404, code="dependency_not_found"
                    )
            except ServiceError:
                raise
            except ValueError as exc:
                raise ServiceError(str(exc), status=409, code="dependency_conflict") from exc
            return {"parent_id": parent_id, "child_id": child_id, "linked": False}

    def diagnostics(self, board: str = "default") -> dict[str, Any]:
        normalized = self._board(board)
        kb = self._ready()
        path = kb.kanban_db_path(board=normalized)
        return {
            "board_slug": normalized,
            "ready": True,
            "db_exists": bool(path.exists()),
            "dispatcher": "managed-by-local-runtime",
        }

    def create_board(self, body: Mapping[str, Any]) -> dict[str, Any]:
        try:
            return self._public_board(
                kb_adapter.create_board(
                    str(body.get("slug") or ""),
                    name=body.get("name"),
                    description=body.get("description"),
                    color=body.get("color"),
                )
            )
        except (ValueError, KanbanUnavailable) as exc:
            code = "kanban_not_ready" if isinstance(exc, KanbanUnavailable) else "invalid_request"
            raise ServiceError(
                str(exc), status=503 if isinstance(exc, KanbanUnavailable) else 400, code=code
            ) from exc

    def patch_board(self, slug: str, body: Mapping[str, Any]) -> dict[str, Any]:
        normalized = self._board(slug)
        fields = {
            key: body[key]
            for key in ("name", "description", "color")
            if key in body and body[key] is not None
        }
        try:
            return self._public_board(kb_adapter.write_board_metadata(normalized, **fields))
        except (ValueError, KanbanUnavailable) as exc:
            raise ServiceError(
                str(exc),
                status=503 if isinstance(exc, KanbanUnavailable) else 400,
                code="kanban_not_ready"
                if isinstance(exc, KanbanUnavailable)
                else "invalid_request",
            ) from exc

    def select_board(self, slug: str) -> dict[str, Any]:
        normalized = self._board(slug)
        try:
            return {"board_slug": kb_adapter.select_board(normalized)}
        except (ValueError, KanbanUnavailable) as exc:
            raise ServiceError(
                str(exc),
                status=503 if isinstance(exc, KanbanUnavailable) else 404,
                code="kanban_not_ready"
                if isinstance(exc, KanbanUnavailable)
                else "board_not_found",
            ) from exc

    def delete_board(self, slug: str) -> dict[str, Any]:
        normalized = self._board(slug)
        if normalized == "default":
            raise ServiceError(
                "the default board cannot be deleted", status=409, code="invalid_request"
            )
        try:
            return kb_adapter.remove_board(normalized, archive=True)
        except (ValueError, KanbanUnavailable) as exc:
            raise ServiceError(
                str(exc),
                status=503 if isinstance(exc, KanbanUnavailable) else 404,
                code="kanban_not_ready"
                if isinstance(exc, KanbanUnavailable)
                else "board_not_found",
            ) from exc

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
            return [
                self._event_dto(item, task)
                for item in kb.list_events(conn, task_id)
                if str(getattr(item, "kind", "")) != "heartbeat"
            ]

    def board_events(
        self, board: str, *, after_id: int = 0, limit: int = 200
    ) -> list[dict[str, Any]]:
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
        return rows[: max(1, min(int(limit), 200))]

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
            "title": str(task.title),
            "kind": str(event.kind),
            "payload": self._safe_event_payload(event.payload),
            "created_at": _iso(event.created_at),
            "run_id": event.run_id,
            "assignee": getattr(task, "assignee", None),
            "status": native_status,
            "kanban_status": _status(native_status),
        }
