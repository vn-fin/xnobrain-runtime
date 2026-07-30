"""Feature-owned operation handlers."""

import time
from typing import Any, Callable

from ..query import bucket, csv, time_range

Operation = tuple[Callable[[], Any], str, int]


def operations(handler: Any, request: Any, body: dict[str, Any]) -> dict[str, Operation]:
    p, q, s = request.path_params, request.query_params, handler.service
    agent = lambda: str(q.get("agent") or "").strip() or (_ for _ in ()).throw(ValueError("agent is required"))
    return {
        "conversations_list": (lambda: s.list_conversations(agent()), "conversations retrieved successfully", 200),
        "conversations_create": (lambda: s.create_conversation(agent(), body), "conversation created successfully", 201),
        "conversations_get": (lambda: s.get_conversation(agent(), p["conversation_id"]), "conversation retrieved successfully", 200),
        "messages_list": (lambda: {"messages": s.get_conversation(agent(), p["conversation_id"])["messages"]}, "messages retrieved successfully", 200),
        "conversations_usage": (lambda: s.conversation_usage(agent(), p["conversation_id"]), "usage retrieved successfully", 200),
        "conversations_rename": (lambda: s.rename_conversation(agent(), p["conversation_id"], body), "conversation renamed successfully", 200),
        "conversations_delete": (lambda: s.delete_conversation(agent(), p["conversation_id"]), "conversation deleted successfully", 200),
        "run_stop": (lambda: s.stop_run(p["run_id"]), "run stopped successfully", 200),
        "run_approval": (lambda: s.resolve_approval(p["run_id"], body, agent()), "approval resolved successfully", 200),
    }
