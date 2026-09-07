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
        "sandbox": (lambda: s.sandbox(p["action"]), "sandbox detail retrieved", 200),
    }
