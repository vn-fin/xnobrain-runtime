"""Channel link, private inbound-message, and product-surface contracts."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_IDENTITY = r"^[^\x00-\x1f/\\]{1,200}$"
_ZALO_ID = r"^[0-9]{1,32}$"
_ZALO_BOT_ID = r"^[A-Za-z0-9_-]{1,64}$"
_ZALO_BOT_TOKEN = r"^[0-9]{1,20}:[A-Za-z0-9_-]{8,128}$"
ChannelStatus = Literal[
    "disabled",
    "starting",
    "needs_login",
    "needs_token",
    "qr",
    "connected",
    "configured",
    "closed",
    "pending_restart",
    "gateway_down",
]
DmPolicy = Literal["disabled", "allowlist", "pairing"]
# The vendored zalo_bot adapter also understands "open". The product API
# deliberately exposes only these safe values; "open" is never accepted.


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


class ChannelState(BaseModel):
    """One agent-scoped channel and its runtime status."""

    platform: Literal["zalo", "zalo_bot"] = "zalo"
    enabled: bool
    status: ChannelStatus
    own_id: str | None = None
    allowed_users: list[str] = Field(default_factory=list)
    dm_policy: DmPolicy = "disabled"
    home_channel: str | None = None
    # The bot token itself is write-only and is never part of this contract.
    token_set: bool = False


class ChannelGatewayState(BaseModel):
    """Root gateway liveness and the restart contract for this workspace."""

    running: bool
    pid: int | None = None
    pending_restart: bool = False


class ChannelListResult(BaseModel):
    """All personal channels plus the shared gateway process state."""

    channels: list[ChannelState] = Field(default_factory=list)
    gateway: ChannelGatewayState


class ChannelZaloUpdate(BaseModel):
    """Enable or disable the Zalo channel for one agent."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool


class ChannelZaloPatch(BaseModel):
    """Partial Zalo access-policy update for one agent."""

    model_config = ConfigDict(extra="forbid")

    allowed_users: list[str] | None = Field(
        default=None, max_length=500, description="Zalo user IDs allowed to trigger the agent"
    )
    dm_policy: DmPolicy | None = None
    home_channel: str | None = Field(default=None, max_length=32, pattern=_ZALO_ID)

    @field_validator("allowed_users")
    @classmethod
    def validate_allowed_users(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        result: list[str] = []
        for entry in value:
            item = str(entry or "").strip()
            if not re.fullmatch(_ZALO_ID, item):
                raise ValueError("allowed_users entries must be Zalo user IDs")
            if item not in result:
                result.append(item)
        return result


class ChannelZaloBotUpdate(BaseModel):
    """Enable or disable the Zalo Bot channel for one agent."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool


class ChannelZaloBotPatch(BaseModel):
    """Partial Zalo Bot access-policy and credential update for one agent.

    ``bot_token`` is write-only. It is never serialized into a response or an
    error payload; the validation handler redacts the submitted body for this
    operation family.
    """

    model_config = ConfigDict(extra="forbid")

    bot_token: str | None = Field(
        default=None,
        max_length=200,
        pattern=_ZALO_BOT_TOKEN,
        description="Write-only Zalo Bot API token (numeric_id:secret)",
    )
    allowed_users: list[str] | None = Field(
        default=None,
        max_length=500,
        description="Zalo bot user IDs allowed to trigger the agent",
    )
    dm_policy: DmPolicy | None = None
    home_channel: str | None = Field(default=None, max_length=64, pattern=_ZALO_BOT_ID)

    @field_validator("allowed_users")
    @classmethod
    def validate_allowed_users(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        result: list[str] = []
        for entry in value:
            item = str(entry or "").strip()
            if not re.fullmatch(_ZALO_BOT_ID, item):
                raise ValueError("allowed_users entries must be Zalo bot user IDs")
            if item not in result:
                result.append(item)
        return result


class ChannelQrResult(BaseModel):
    """Current QR login state; the image is present only while ``status == "qr"``."""

    status: ChannelStatus
    image_base64: str | None = None
