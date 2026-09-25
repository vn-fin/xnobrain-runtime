"""Feature-owned operation handlers."""

import inspect
from collections.abc import Callable
from typing import Any

from ...trusted_context import from_request

Operation = tuple[Callable[[], Any], str, int]


def operations(handler: Any, request: Any, body: dict[str, Any]) -> dict[str, Operation]:
    p, q, s = request.path_params, request.query_params, handler.service
    trusted = from_request(request)

    def agent():
        identifier = str(q.get("agent") or "").strip()
        if not identifier:
            raise ValueError("agent is required")
        return identifier

    result = {
        "conversation_reasoning_get": (
            lambda: s.get_conversation_reasoning(agent(), p["conversation_id"]),
            "reasoning retrieved",
            200,
        ),
        "conversation_reasoning_update": (
            lambda: s.update_conversation_reasoning(agent(), p["conversation_id"], body, trusted),
            "reasoning updated",
            200,
        ),
        "conversations_list": (
            lambda: s.list_conversations(
                agent(),
                page=int(q.get("page") or 1),
                limit=int(q.get("limit") or 50),
                trusted_context=trusted,
            ),
            "conversations retrieved successfully",
            200,
        ),
        "conversations_create": (
            lambda: s.create_conversation(agent(), body, trusted),
            "conversation created successfully",
            201,
        ),
        "conversations_get": (
            lambda: s.get_conversation(agent(), p["conversation_id"]),
            "conversation retrieved successfully",
            200,
        ),
        "messages_list": (
            lambda: {"messages": s.get_conversation(agent(), p["conversation_id"])["messages"]},
            "messages retrieved successfully",
            200,
        ),
        "conversations_usage": (
            lambda: s.conversation_usage(agent(), p["conversation_id"]),
            "usage retrieved successfully",
            200,
        ),
        "conversations_compact": (
            lambda: s.compact_conversation(agent(), p["conversation_id"], body),
            "conversation context compacted successfully",
            200,
        ),
        "conversations_rename": (
            lambda: s.rename_conversation(agent(), p["conversation_id"], body),
            "conversation renamed successfully",
            200,
        ),
        "conversations_delete": (
            lambda: s.delete_conversation(agent(), p["conversation_id"]),
            "conversation deleted successfully",
            200,
        ),
        "conversation_goal_get": (
            lambda: s.get_conversation_goal(agent(), p["conversation_id"]),
            "goal retrieved",
            200,
        ),
        "conversation_goal_create": (
            lambda: s.create_conversation_goal(agent(), p["conversation_id"], body),
            "goal started",
            202,
        ),
        "conversation_goal_update": (
            lambda: s.update_conversation_goal(agent(), p["conversation_id"], body),
            "goal updated",
            200,
        ),
        "conversation_goal_delete": (
            lambda: s.delete_conversation_goal(agent(), p["conversation_id"]),
            "goal deleted",
            200,
        ),
        "conversation_goal_pause": (
            lambda: s.pause_conversation_goal(agent(), p["conversation_id"]),
            "goal paused",
            200,
        ),
        "conversation_goal_resume": (
            lambda: s.resume_conversation_goal(agent(), p["conversation_id"]),
            "goal resumed",
            202,
        ),
        "conversation_subgoal_create": (
            lambda: s.add_conversation_subgoal(agent(), p["conversation_id"], body),
            "subgoal added",
            201,
        ),
        "conversation_subgoal_delete": (
            lambda: s.remove_conversation_subgoal(
                agent(), p["conversation_id"], p["subgoal_index"]
            ),
            "subgoal deleted",
            200,
        ),
        "conversation_runs_start": (
            lambda: s.start_conversation_run(agent(), p["conversation_id"], body, trusted),
            "conversation run started",
            202,
        ),
        "conversation_runs_list": (
            lambda: s.list_conversation_runs(
                agent(), p["conversation_id"], int(q.get("limit") or 20), q.get("cursor") or ""
            ),
            "conversation run history retrieved",
            200,
        ),
        "conversation_runs_active": (
            lambda: s.active_conversation_run(agent(), p["conversation_id"]),
            "active conversation run retrieved",
            200,
        ),
        "conversation_runs_get": (
            lambda: s.get_conversation_run(agent(), p["conversation_id"], p["run_id"]),
            "conversation run retrieved",
            200,
        ),
        "conversation_child_run_stop": (
            lambda: s.stop_conversation_child_run(
                agent(),
                p["conversation_id"],
                p["run_id"],
                p["child_run_id"],
                body,
            ),
            "child run cancellation requested",
            202,
        ),
        "conversation_run_todo_update": (
            lambda: s.update_conversation_run_todo(
                agent(),
                p["conversation_id"],
                p["run_id"],
                p["todo_id"],
                body,
            ),
            "todo updated successfully",
            200,
        ),
        "run_stop": (
            lambda: s.stop_run(agent(), p["conversation_id"], p["run_id"]),
            "run stopped successfully",
            200,
        ),
        "run_approval": (
            lambda: s.resolve_approval(p["run_id"], body, agent()),
            "approval resolved successfully",
            200,
        ),
    }

    # The operation factories are assembled eagerly; authorize only the selected
    # operation, never touch another session while resolving an unrelated route.
    if trusted.subject:
        for name, (operation, message, status) in list(result.items()):
            if name in {"conversations_create", "conversations_list"}:
                continue

            def checked(operation=operation, name=name):
                readonly = name in {
                    "conversation_reasoning_get",
                    "conversations_get",
                    "messages_list",
                    "conversations_usage",
                    "conversation_goal_get",
                    "conversation_runs_get",
                    "conversation_runs_active",
                }
                stopping = name in {
                    "run_stop",
                    "conversation_child_run_stop",
                    "conversation_goal_pause",
                }
                s.authorize_conversation(
                    agent(), p["conversation_id"], trusted, active=not readonly and not stopping
                )
                if name == "run_approval":
                    # Approval dispatch accepts a run ID internally; bind the URL
                    # session too before handing it to that existing subsystem.
                    s.repository.get_conversation_run(agent(), p["conversation_id"], p["run_id"])
                return operation()

            result[name] = (checked, message, status)
    from ...services.conversation_authority import public_run

    for name in {
        "conversation_runs_start",
        "conversation_runs_get",
        "conversation_runs_active",
        "conversation_child_run_stop",
        "conversation_run_todo_update",
        "run_stop",
    }:
        operation, message, status = result[name]

        async def projected(operation=operation, name=name):
            value = operation()
            if inspect.isawaitable(value):
                value = await value
            if name == "conversation_runs_active":
                return {**value, "run": public_run(value.get("run"))}
            return public_run(value)

        result[name] = (projected, message, status)
    return result
