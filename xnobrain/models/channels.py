"""Channel link and private inbound-message contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_IDENTITY = r"^[^\x00-\x1f/\\]{1,200}$"


class ChannelInboundMessage(BaseModel):
    """One inbound personal-channel message forwarded by the gateway."""

    model_config = ConfigDict(extra="forbid")

    platform: Literal["zalo"] = "zalo"
    agent_id: str = Field(min_length=1, max_length=200, pattern=_IDENTITY)
    conversation_id: str = Field(min_length=1, max_length=200, pattern=_IDENTITY)
    thread_id: str = Field(min_length=1, max_length=32, pattern=r"^[0-9]{1,32}$")
    sender_name: str = Field(default="", max_length=200)
    text: str = Field(min_length=1, max_length=20000)
    message_id: str = Field(min_length=1, max_length=128, pattern=_IDENTITY)
    ts: float | None = Field(default=None, ge=0)


class ChannelInboundResult(BaseModel):
    """Idempotent result of appending one external message."""

    appended: bool
    duplicate: bool
    message_row_id: int | None = None


class SendChannelMessageRequest(BaseModel):
    """Model-facing tool input. The host supplies agent and conversation."""

    model_config = ConfigDict(extra="forbid")

    platform: Literal["zalo"] = "zalo"
    recipient: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=20000)
