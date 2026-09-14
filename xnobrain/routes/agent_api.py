"""OpenAI-compatible per-agent invocation route."""

from ..models import AgentChatCompletionRequest
from .definition import route

ROUTES = (
    route(
        "POST",
        "/agents/{agent_id}/chat/completions",
        "agent_chat_completions",
        AgentChatCompletionRequest,
        special="agent_chat_completions",
        tags=("Agent API",),
        response_data=None,
    ),
)
