"""Scoped Hermes tools for the product-owned Big Brother profile."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from typing import Any, Mapping

from hermes_constants import get_hermes_home
from tools.registry import registry

from ..defaults import BIG_BROTHER_AGENT_ID, BIG_BROTHER_TOOLSET


_platform_service: Any | None = None


def bind_platform_service(service: Any) -> None:
    """Bind the current application service to the process-global tool registry."""
    global _platform_service
    _platform_service = service


def _authorized_service() -> Any:
    service = _platform_service
    if service is None:
        raise PermissionError("Brain4All platform service is unavailable")
    expected = service.repository.profile_path(BIG_BROTHER_AGENT_ID).resolve()
    if get_hermes_home().resolve() != expected:
        raise PermissionError("This tool is restricted to the Big Brother profile")
    return service


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _error(exc: Exception) -> str:
    if hasattr(exc, "code") and hasattr(exc, "status"):
        return _json({
            "success": False,
            "error": str(exc),
            "code": str(getattr(exc, "code")),
            "status": int(getattr(exc, "status")),
        })
    if isinstance(exc, (PermissionError, ValueError)):
        return _json({"success": False, "error": str(exc)})
    return _json({"success": False, "error": "Platform operation failed"})


def _task_summary(task: Mapping[str, Any]) -> dict[str, Any]:
    detail = task.get("state_detail")
    return {
        "id": str(task.get("id") or ""),
        "title": str(task.get("title") or ""),
        "assignee": task.get("assignee"),
        "status": str(task.get("status") or ""),
        "kanban_status": str(task.get("kanban_status") or ""),
        "state_label": (
            str(detail.get("label") or "")
            if isinstance(detail, Mapping)
            else ""
        ),
        "updated_at": task.get("updated_at"),
    }


async def _handle_overview(args: dict[str, Any], **_kw: Any) -> str:
    try:
        service = _authorized_service()
        days = max(1, min(90, int(args.get("days") or 7)))
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        usage = await service.analytics.usage_overview(
            agent_ids=[],
            start_epoch=start.timestamp(),
            end_epoch=end.timestamp(),
            bucket="day",
        )
        boards = service.kanban.list_boards(include_archived=False)
        return _json({
            "success": True,
            "range_days": days,
            "agents": service.list_agents(),
            "usage": {
                "totals": usage.get("totals", {}),
                "agents": usage.get("agents", []),
                "agents_available": usage.get("agents_available", 0),
                "source": usage.get("source"),
                "warnings": usage.get("warnings", []),
            },
            "boards": [
                {
                    "id": str(board.get("id") or ""),
                    "name": str(board.get("name") or board.get("id") or ""),
                    "current": bool(board.get("current")),
                    "tasks": [
                        _task_summary(task)
                        for task in board.get("tasks", [])
                        if isinstance(task, Mapping)
                    ],
                }
                for board in boards
            ],
            "privacy": (
                "Conversation bodies, prompts, credentials, and raw profile "
                "databases are intentionally excluded."
            ),
        })
    except Exception as exc:
        return _error(exc)


def _handle_manage_agent(args: dict[str, Any], **_kw: Any) -> str:
    try:
        service = _authorized_service()
        action = str(args.get("action") or "").strip()
        if action == "list":
            result: Any = service.list_agents()
        elif action == "create":
            result = service.create_agent({
                "display_name": args.get("display_name"),
                "description": args.get("description") or "",
            })
        elif action == "update":
            agent_id = str(args.get("agent_id") or "").strip()
            if not agent_id:
                raise ValueError("agent_id is required for update")
            patch = {
                key: args[key]
                for key in ("display_name", "description")
                if key in args
            }
            if not patch:
                raise ValueError("display_name or description is required for update")
            result = service.update_agent_metadata(agent_id, patch)
        else:
            raise ValueError("action must be list, create, or update")
        return _json({"success": True, "result": result})
    except Exception as exc:
        return _error(exc)


def _handle_manage_skill(args: dict[str, Any], **_kw: Any) -> str:
    try:
        service = _authorized_service()
        action = str(args.get("action") or "").strip()
        agent_id = str(args.get("agent_id") or "").strip()
        if not agent_id:
            raise ValueError("agent_id is required")
        if action == "list":
            result = service.list_skills(agent_id)
        elif action == "set_enabled":
            skill_id = str(args.get("skill_id") or "").strip()
            if not skill_id:
                raise ValueError("skill_id is required for set_enabled")
            if "enabled" not in args:
                raise ValueError("enabled is required for set_enabled")
            result = service.set_skill_enabled(
                agent_id,
                skill_id,
                {"enabled": bool(args["enabled"])},
            )
        else:
            raise ValueError("action must be list or set_enabled")
        return _json({"success": True, "result": result})
    except Exception as exc:
        return _error(exc)


OVERVIEW_SCHEMA = {
    "name": "brain4all_overview",
    "description": (
        "Read a sanitized cross-agent overview of usage and Kanban work. "
        "Does not return conversation bodies, prompts, credentials, or raw databases."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 90,
                "description": "Usage lookback window in days. Defaults to 7.",
            },
        },
    },
}

MANAGE_AGENT_SCHEMA = {
    "name": "brain4all_manage_agent",
    "description": "List, create, or update Brain4All agent profiles.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "create", "update"]},
            "agent_id": {"type": "string", "description": "Required for update."},
            "display_name": {
                "type": "string",
                "description": "Required for create; optional for update.",
            },
            "description": {"type": "string"},
        },
        "required": ["action"],
    },
}

MANAGE_SKILL_SCHEMA = {
    "name": "brain4all_manage_skill",
    "description": (
        "List or enable/disable skills already installed on a Brain4All profile. "
        "Skill installation and deletion remain in the reviewed Skills UI/API."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "set_enabled"]},
            "agent_id": {"type": "string"},
            "skill_id": {"type": "string", "description": "Required for set_enabled."},
            "enabled": {"type": "boolean", "description": "Required for set_enabled."},
        },
        "required": ["action", "agent_id"],
    },
}


registry.register(
    name="brain4all_overview",
    toolset=BIG_BROTHER_TOOLSET,
    schema=OVERVIEW_SCHEMA,
    handler=_handle_overview,
    is_async=True,
    emoji="👁️",
)
registry.register(
    name="brain4all_manage_agent",
    toolset=BIG_BROTHER_TOOLSET,
    schema=MANAGE_AGENT_SCHEMA,
    handler=_handle_manage_agent,
    emoji="👥",
)
registry.register(
    name="brain4all_manage_skill",
    toolset=BIG_BROTHER_TOOLSET,
    schema=MANAGE_SKILL_SCHEMA,
    handler=_handle_manage_skill,
    emoji="🧩",
)
