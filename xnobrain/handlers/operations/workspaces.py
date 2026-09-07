"""Feature-owned operation handlers."""

import time
from typing import Any, Callable

from ..query import bucket, csv, time_range

Operation = tuple[Callable[[], Any], str, int]


def operations(handler: Any, request: Any, body: dict[str, Any]) -> dict[str, Operation]:
    p, q, s = request.path_params, request.query_params, handler.service
    agent = lambda: (
        str(q.get("agent") or "").strip() or (_ for _ in ()).throw(ValueError("agent is required"))
    )
    return {
        "workspace_list": (
            lambda: s.list_workspace(p["agent_id"], q.get("path", ".")),
            "workspace retrieved successfully",
            200,
        ),
        "workspace_view": (
            lambda: s.read_workspace(p["agent_id"], {"path": q.get("path")}),
            "file retrieved successfully",
            200,
        ),
        "workspace_read": (
            lambda: s.read_workspace(p["agent_id"], body),
            "file retrieved successfully",
            200,
        ),
        "workspace_write": (
            lambda: s.write_workspace(p["agent_id"], body),
            "workspace updated successfully",
            200,
        ),
        "workspace_create": (
            lambda: s.create_workspace(p["agent_id"], body),
            "workspace created successfully",
            201,
        ),
        "workspace_delete": (
            lambda: s.delete_workspace(p["agent_id"], body),
            "workspace deleted successfully",
            200,
        ),
        "workspace_rename": (
            lambda: s.rename_workspace(p["agent_id"], body),
            "workspace renamed successfully",
            200,
        ),
    }
