"""Session API routes with legacy conversation-path compatibility."""

from ..models import ChatRequest, ConversationCompact, ConversationCreate, ConversationRename, RunApproval
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
        route("POST", f"{root}/{{conversation_id}}/chat/stream", "conversations_stream", ChatRequest, "stream", ("Runs",), include_in_schema),
        route("POST", f"{root}/{{conversation_id}}/runs/{{run_id}}/stop", "run_stop", tags=("Runs",), include_in_schema=include_in_schema),
        route("POST", f"{root}/{{conversation_id}}/runs/{{run_id}}/approval", "run_approval", RunApproval, tags=("Runs",), include_in_schema=include_in_schema),
    )


ROUTES = session_routes("/sessions", ("Sessions",)) + session_routes(
    "/conversations", ("Legacy Conversations",), include_in_schema=False
)
