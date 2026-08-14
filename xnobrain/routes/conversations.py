"""Session API routes with legacy conversation-path compatibility."""

from ..models import ChatRequest, ConversationCompact, ConversationCreate, ConversationRename, GoalCreate, GoalUpdate, RunApproval, SubgoalCreate
from .definition import route

def session_routes(root: str, tags: tuple[str, ...], *, include_in_schema: bool = True) -> tuple:
    return (
        route("GET", root, "conversations_list", tags=tags, include_in_schema=include_in_schema),
        route("POST", root, "conversations_create", ConversationCreate, tags=tags, include_in_schema=include_in_schema),
        route("GET", f"{root}/{{conversation_id}}/detail", "conversations_get", tags=tags, include_in_schema=include_in_schema),
        route("GET", f"{root}/{{conversation_id}}/messages", "messages_list", tags=tags, include_in_schema=include_in_schema),
        route("GET", f"{root}/{{conversation_id}}/usage", "conversations_usage", tags=tags, include_in_schema=include_in_schema),
        route("POST", f"{root}/{{conversation_id}}/compact", "conversations_compact", ConversationCompact, tags=tags, include_in_schema=include_in_schema),
        route("PATCH", f"{root}/{{conversation_id}}/name", "conversations_rename", ConversationRename, tags=tags, include_in_schema=include_in_schema),
        route("DELETE", f"{root}/{{conversation_id}}/delete", "conversations_delete", tags=tags, include_in_schema=include_in_schema),
        route("GET", f"{root}/{{conversation_id}}/goal", "conversation_goal_get", tags=("Goals",), include_in_schema=include_in_schema),
        route("POST", f"{root}/{{conversation_id}}/goal", "conversation_goal_create", GoalCreate, tags=("Goals",), include_in_schema=include_in_schema),
        route("PATCH", f"{root}/{{conversation_id}}/goal", "conversation_goal_update", GoalUpdate, tags=("Goals",), include_in_schema=include_in_schema),
        route("DELETE", f"{root}/{{conversation_id}}/goal", "conversation_goal_delete", tags=("Goals",), include_in_schema=include_in_schema),
        route("POST", f"{root}/{{conversation_id}}/goal/pause", "conversation_goal_pause", tags=("Goals",), include_in_schema=include_in_schema),
        route("POST", f"{root}/{{conversation_id}}/goal/resume", "conversation_goal_resume", tags=("Goals",), include_in_schema=include_in_schema),
        route("POST", f"{root}/{{conversation_id}}/goal/subgoals", "conversation_subgoal_create", SubgoalCreate, tags=("Goals",), include_in_schema=include_in_schema),
        route("DELETE", f"{root}/{{conversation_id}}/goal/subgoals/{{subgoal_index}}", "conversation_subgoal_delete", tags=("Goals",), include_in_schema=include_in_schema),
        route("POST", f"{root}/{{conversation_id}}/chat/stream", "conversations_stream", ChatRequest, "stream", ("Runs",), include_in_schema),
        route("POST", f"{root}/{{conversation_id}}/runs", "conversation_runs_start", ChatRequest, tags=("Runs",), include_in_schema=include_in_schema),
        route("GET", f"{root}/{{conversation_id}}/runs/active", "conversation_runs_active", tags=("Runs",), include_in_schema=include_in_schema),
        route("GET", f"{root}/{{conversation_id}}/runs/{{run_id}}", "conversation_runs_get", tags=("Runs",), include_in_schema=include_in_schema),
        route("GET", f"{root}/{{conversation_id}}/runs/{{run_id}}/events", "conversation_run_events", special="conversation_run_stream", tags=("Runs",), include_in_schema=include_in_schema),
        route("POST", f"{root}/{{conversation_id}}/runs/{{run_id}}/stop", "run_stop", tags=("Runs",), include_in_schema=include_in_schema),
        route("POST", f"{root}/{{conversation_id}}/runs/{{run_id}}/approval", "run_approval", RunApproval, tags=("Runs",), include_in_schema=include_in_schema),
    )


ROUTES = session_routes("/sessions", ("Sessions",)) + session_routes(
    "/conversations", ("Legacy Conversations",), include_in_schema=False
)
