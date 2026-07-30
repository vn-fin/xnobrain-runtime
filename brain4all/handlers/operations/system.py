"""Feature-owned operation handlers."""

import time
from typing import Any, Callable

from ..query import bucket, csv, time_range

Operation = tuple[Callable[[], Any], str, int]


def operations(handler: Any, request: Any, body: dict[str, Any]) -> dict[str, Operation]:
    p, q, s = request.path_params, request.query_params, handler.service
    agent = lambda: str(q.get("agent") or "").strip() or (_ for _ in ()).throw(ValueError("agent is required"))
    return {
        "health": (lambda: {"status": "ok", "edition": "opensource", "api": "fastapi", "uptime_seconds": int(time.time()-handler.started_at)}, "healthy", 200),
        "limits": (lambda: {"plan_id": "self-hosted", "local_features_unlimited": True, "agents": -1, "teams": -1, "mcp_servers": -1, "cron_jobs": -1}, "limits retrieved", 200),
        "deployment": (lambda: {"mode": "local", "runtime": "hermes-fastapi", "runtime_transport": "in-process", "database": False}, "deployment retrieved", 200),
    }
