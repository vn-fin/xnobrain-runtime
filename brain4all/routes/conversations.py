"""Conversations API route declarations."""

from ..models import ChatRequest, ConversationCreate, ConversationRename, RunApproval
from .definition import route

ROUTES = (
    route("GET", "/conversations", "conversations_list", tags=("Conversations",)),
    route("POST", "/conversations", "conversations_create", ConversationCreate, tags=("Conversations",)),
    route("GET", "/conversations/{conversation_id}/detail", "conversations_get", tags=("Conversations",)),
    route("GET", "/conversations/{conversation_id}/messages", "messages_list", tags=("Conversations",)),
    route("GET", "/conversations/{conversation_id}/usage", "conversations_usage", tags=("Conversations",)),
    route("PATCH", "/conversations/{conversation_id}/name", "conversations_rename", ConversationRename, tags=("Conversations",)),
    route("DELETE", "/conversations/{conversation_id}/delete", "conversations_delete", tags=("Conversations",)),
    route("POST", "/conversations/{conversation_id}/chat/stream", "conversations_stream", ChatRequest, "stream", ("Runs",)),
    route("POST", "/conversations/{conversation_id}/runs/{run_id}/stop", "run_stop", tags=("Runs",)),
    route("POST", "/conversations/{conversation_id}/runs/{run_id}/approval", "run_approval", RunApproval, tags=("Runs",)),
)
