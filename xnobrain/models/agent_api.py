"""OpenAI-compatible agent invocation contracts."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentChatMessage(BaseModel):
    """One OpenAI-compatible chat message accepted by an agent endpoint."""

    model_config = ConfigDict(extra="ignore")
    role: Literal["system", "developer", "user", "assistant", "tool"]
    content: str | list[dict[str, Any]]
    name: str | None = Field(default=None, max_length=128)
    tool_call_id: str | None = Field(default=None, max_length=256)


class AgentChatCompletionRequest(BaseModel):
    """Supported subset of the OpenAI chat-completions request."""

    model_config = ConfigDict(extra="ignore")
    model: str = Field(min_length=1, max_length=128)
    messages: list[AgentChatMessage] = Field(min_length=1, max_length=200)
    stream: bool = False
    store: bool | None = None
    user: str | None = Field(default=None, max_length=256)
    max_tokens: int | None = Field(default=None, ge=1)
    max_completion_tokens: int | None = Field(default=None, ge=1)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
