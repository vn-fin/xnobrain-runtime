"""Channel link, private inbound-message, and product-surface contracts."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from ..integrations.channel_platforms import (
    PLATFORMS,
    ChannelPlatformId,
    matches_id_pattern,
)

_IDENTITY = r"^[^\x00-\x1f/\\]{1,200}$"
_ZALO_ID = r"^[0-9]{1,32}$"
_ZALO_BOT_ID = r"^[A-Za-z0-9_-]{1,64}$"
_ZALO_BOT_TOKEN = r"^[0-9]{1,20}:[A-Za-z0-9_-]{8,128}$"
_TELEGRAM_ID = PLATFORMS["telegram"].id_pattern
_TELEGRAM_USER_ID = r"^[0-9]{1,20}$"
_DISCORD_ID = PLATFORMS["discord"].id_pattern
_DISCORD_TOKEN = r"^[A-Za-z0-9_.-]{40,200}$"
_SLACK_ID = PLATFORMS["slack"].id_pattern
_SLACK_BOT_TOKEN = r"^xoxb-[A-Za-z0-9-]{10,250}$"
_SLACK_APP_TOKEN = r"^xapp-[A-Za-z0-9-]{10,250}$"
_WHATSAPP_ID = PLATFORMS["whatsapp"].id_pattern
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


class ChannelTurnRequest(BaseModel):
    """One authorized gateway message for an enabled, owner-bound channel turn.

    The request never carries owner identity; Runtime derives it from the
    profile's persisted owner binding and refuses the turn when absent.
    """

    model_config = ConfigDict(extra="forbid")

    platform: ChannelPlatformId
    agent_id: str = Field(min_length=1, max_length=200, pattern=_IDENTITY)
    thread_id: str = Field(min_length=1, max_length=64, pattern=_IDENTITY)
    thread_type: Literal["dm", "group"]
    sender_id: str = Field(min_length=1, max_length=64, pattern=_IDENTITY)
    sender_name: str = Field(default="", max_length=200)
    text: str = Field(min_length=1, max_length=20000)
    message_id: str = Field(min_length=1, max_length=128, pattern=_IDENTITY)
    ts: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_platform_ids(self) -> "ChannelTurnRequest":
        for value in (self.thread_id, self.sender_id):
            if not matches_id_pattern(self.platform, value):
                raise ValueError(f"{self.platform} thread and sender IDs are invalid")
        return self


class ChannelTurnUsage(BaseModel):
    """Token accounting returned with one channel turn."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class ChannelTurnResult(BaseModel):
    """The stored reply from a completed channel turn."""

    conversation_id: str
    run_id: str
    reply: str
    usage: ChannelTurnUsage = Field(default_factory=ChannelTurnUsage)


class SendChannelMessageRequest(BaseModel):
    """Model-facing tool input. The host supplies agent and conversation."""

    model_config = ConfigDict(extra="forbid")

    platform: ChannelPlatformId = "zalo"
    thread_type: Literal["dm", "group"] = "dm"
    recipient: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=20000)
    quote_message_id: str | None = Field(default=None, pattern=r"^[0-9]{1,32}$")
    mentions: list[str] | None = Field(default=None, max_length=20)

    @field_validator("mentions")
    @classmethod
    def validate_mentions(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if not value:
            raise ValueError("mentions must name at least one member")
        ids: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if not re.fullmatch(_ZALO_ID, text) or text in ids:
                raise ValueError("mentions must be unique numeric user ids")
            ids.append(text)
        return ids


class ChannelPluginArtifact(BaseModel):
    """Read-only server artifact metadata for the built-in platform plugin.

    This is presentation metadata only; the private Control artifact authority
    endpoint recomputes the digest and verifies ownership on every decision.
    """

    plugin: str
    artifact_digest: str


class ChannelState(BaseModel):
    """One agent-scoped channel and its runtime status."""

    platform: ChannelPlatformId = "zalo"
    enabled: bool
    status: ChannelStatus
    own_id: str | None = None
    allowed_users: list[str] = Field(default_factory=list)
    dm_policy: DmPolicy = "disabled"
    home_channel: str | None = None
    # None for platforms whose history Runtime does not store (Telegram).
    history_retention_days: int | None = Field(default=30, ge=1, le=365)
    # The bot token itself is write-only and is never part of this contract.
    token_set: bool = False
    # A second write-only secret for platforms that need one (Slack's app-level
    # Socket Mode token); always false for single-token platforms.
    app_token_set: bool = False
    plugin_artifact: ChannelPluginArtifact | None = None


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
    history_retention_days: StrictInt | None = Field(default=None, ge=1, le=365)

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
    history_retention_days: StrictInt | None = Field(default=None, ge=1, le=365)

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


class ChannelTelegramUpdate(BaseModel):
    """Enable or disable the Telegram channel for one agent."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool


class ChannelTelegramPatch(BaseModel):
    """Partial Telegram access-policy and credential update for one agent.

    ``bot_token`` is write-only. It is never serialized into a response or an
    error payload; the validation handler redacts the submitted body for this
    operation family.
    """

    model_config = ConfigDict(extra="forbid")

    bot_token: str | None = Field(
        default=None,
        max_length=200,
        pattern=_ZALO_BOT_TOKEN,
        description="Write-only Telegram bot token from @BotFather (numeric_id:secret)",
    )
    allowed_users: list[str] | None = Field(
        default=None,
        max_length=500,
        description="Telegram user IDs allowed to trigger the agent",
    )
    dm_policy: DmPolicy | None = None
    home_channel: str | None = Field(default=None, max_length=21, pattern=_TELEGRAM_ID)

    @field_validator("allowed_users")
    @classmethod
    def validate_allowed_users(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        result: list[str] = []
        for entry in value:
            item = str(entry or "").strip()
            if not re.fullmatch(_TELEGRAM_USER_ID, item):
                raise ValueError("allowed_users entries must be Telegram user IDs")
            if item not in result:
                result.append(item)
        return result


class ChannelDiscordUpdate(BaseModel):
    """Enable or disable the Discord channel for one agent."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool


class ChannelDiscordPatch(BaseModel):
    """Partial Discord access-policy and credential update for one agent.

    ``bot_token`` is write-only. It is never serialized into a response or an
    error payload; the validation handler redacts the submitted body for this
    operation family.
    """

    model_config = ConfigDict(extra="forbid")

    bot_token: str | None = Field(
        default=None,
        max_length=200,
        pattern=_DISCORD_TOKEN,
        description="Write-only Discord bot token from the Developer Portal",
    )
    allowed_users: list[str] | None = Field(
        default=None,
        max_length=500,
        description="Discord user IDs allowed to trigger the agent",
    )
    dm_policy: DmPolicy | None = None
    home_channel: str | None = Field(default=None, max_length=20, pattern=_DISCORD_ID)

    @field_validator("allowed_users")
    @classmethod
    def validate_allowed_users(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        result: list[str] = []
        for entry in value:
            item = str(entry or "").strip()
            if not re.fullmatch(_DISCORD_ID, item):
                raise ValueError("allowed_users entries must be Discord user IDs")
            if item not in result:
                result.append(item)
        return result


class ChannelSlackUpdate(BaseModel):
    """Enable or disable the Slack channel for one agent."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool


class ChannelSlackPatch(BaseModel):
    """Partial Slack access-policy and credential update for one agent.

    ``bot_token`` and ``app_token`` are write-only. They are never serialized
    into a response or an error payload; the validation handler redacts the
    submitted body for this operation family.
    """

    model_config = ConfigDict(extra="forbid")

    bot_token: str | None = Field(
        default=None,
        max_length=300,
        pattern=_SLACK_BOT_TOKEN,
        description="Write-only Slack bot token (xoxb-...)",
    )
    app_token: str | None = Field(
        default=None,
        max_length=300,
        pattern=_SLACK_APP_TOKEN,
        description="Write-only Slack app-level token for Socket Mode (xapp-...)",
    )
    allowed_users: list[str] | None = Field(
        default=None,
        max_length=500,
        description="Slack member IDs allowed to trigger the agent",
    )
    dm_policy: DmPolicy | None = None
    home_channel: str | None = Field(default=None, max_length=33, pattern=_SLACK_ID)

    @field_validator("allowed_users")
    @classmethod
    def validate_allowed_users(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        result: list[str] = []
        for entry in value:
            item = str(entry or "").strip()
            if not re.fullmatch(_SLACK_ID, item):
                raise ValueError("allowed_users entries must be Slack member IDs")
            if item not in result:
                result.append(item)
        return result


class ChannelWhatsappUpdate(BaseModel):
    """Enable or disable the WhatsApp channel for one agent."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool


class ChannelWhatsappPatch(BaseModel):
    """Partial WhatsApp access-policy update for one agent."""

    model_config = ConfigDict(extra="forbid")

    allowed_users: list[str] | None = Field(
        default=None,
        max_length=500,
        description="WhatsApp phone numbers or JIDs allowed to trigger the agent",
    )
    dm_policy: DmPolicy | None = None
    home_channel: str | None = Field(default=None, max_length=64, pattern=_WHATSAPP_ID)

    @field_validator("allowed_users")
    @classmethod
    def validate_allowed_users(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        result: list[str] = []
        for entry in value:
            item = str(entry or "").strip()
            if not re.fullmatch(_WHATSAPP_ID, item):
                raise ValueError("allowed_users entries must be WhatsApp user IDs")
            if item not in result:
                result.append(item)
        return result


class ChannelPairingPending(BaseModel):
    """One sanitized pending pairing request; no code or hash is exposed."""

    request_id: str = Field(pattern=r"^[0-9a-fA-F]{16}$")
    user_id: str = Field(min_length=1, max_length=200)
    user_name: str = Field(default="", max_length=200)
    requested_at: float = Field(ge=0)
    expires_at: float = Field(ge=0)


class ChannelPairingApproved(BaseModel):
    """One approved sender in the pinned pairing store."""

    user_id: str = Field(min_length=1, max_length=200)
    user_name: str = Field(default="", max_length=200)
    approved_at: float | None = Field(default=None, ge=0)


class ChannelPairingListResult(BaseModel):
    """Sanitized pairing state for one enabled channel."""

    platform: ChannelPlatformId
    dm_policy: DmPolicy
    pending: list[ChannelPairingPending] = Field(default_factory=list)
    approved: list[ChannelPairingApproved] = Field(default_factory=list)


class ChannelPairingRequest(BaseModel):
    """Approve or deny one pending request by its server-side id."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(pattern=r"^[0-9a-fA-F]{16}$")


class ChannelPairingApproveResult(BaseModel):
    status: Literal["approved"] = "approved"
    user_id: str
    user_name: str = ""


class ChannelPairingDenyResult(BaseModel):
    status: Literal["denied"] = "denied"
    request_id: str


class ChannelPairingRevokeRequest(BaseModel):
    """Revoke one approved sender for the selected channel."""

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, max_length=200)


class ChannelPairingRevokeResult(BaseModel):
    status: Literal["revoked", "not_approved"]
    user_id: str


class ChannelGroupPending(BaseModel):
    """One sanitized unapproved Zalo Bot group mention."""

    group_id: str = Field(min_length=1, max_length=64, pattern=_ZALO_BOT_ID)
    group_name: str = Field(default="", max_length=100)
    last_sender_name: str = Field(default="", max_length=100)
    first_seen: float = Field(default=0, ge=0)
    last_seen: float = Field(default=0, ge=0)


class ChannelGroupApproved(BaseModel):
    """One owner-approved Zalo Bot group."""

    group_id: str = Field(min_length=1, max_length=64, pattern=_ZALO_BOT_ID)
    group_name: str = Field(default="", max_length=100)
    last_sender_name: str = Field(default="", max_length=100)
    first_seen: float = Field(default=0, ge=0)
    last_seen: float = Field(default=0, ge=0)
    approved_at: float | None = Field(default=None, ge=0)


class ChannelGroupListResult(BaseModel):
    """Sanitized group approval state for one agent's channel."""

    platform: ChannelPlatformId = "zalo_bot"
    daily_limit: int = Field(default=20, ge=0, le=1000)
    pending: list[ChannelGroupPending] = Field(default_factory=list)
    approved: list[ChannelGroupApproved] = Field(default_factory=list)


class ChannelGroupRequest(BaseModel):
    """Approve, deny, or revoke one group by its Zalo Bot ID."""

    model_config = ConfigDict(extra="forbid")

    group_id: str = Field(min_length=1, max_length=64, pattern=_ZALO_BOT_ID)


class ChannelGroupApproveResult(BaseModel):
    status: Literal["approved"] = "approved"
    group_id: str
    group_name: str = ""


class ChannelGroupDenyResult(BaseModel):
    status: Literal["denied"] = "denied"
    group_id: str


class ChannelGroupRevokeResult(BaseModel):
    status: Literal["revoked", "not_approved"]
    group_id: str


class ChannelGroupSettingsRequest(BaseModel):
    """Set the per-member daily turn limit for approved groups."""

    model_config = ConfigDict(extra="forbid")

    daily_limit: int = Field(ge=0, le=1000)

    @field_validator("daily_limit", mode="before")
    @classmethod
    def reject_boolean_limit(cls, value: int) -> int:
        if isinstance(value, bool):
            raise ValueError("daily_limit must be an integer")
        return value


class ChannelGroupSettingsResult(BaseModel):
    daily_limit: int = Field(ge=0, le=1000)


class ChannelGroupSeenRequest(BaseModel):
    """One unapproved group mention reported by the gateway."""

    model_config = ConfigDict(extra="forbid")

    platform: str = Field(min_length=1, max_length=32)
    agent_id: str = Field(min_length=1, max_length=200, pattern=_IDENTITY)
    group_id: str = Field(min_length=1, max_length=64, pattern=_ZALO_BOT_ID)
    group_name: str = Field(default="", max_length=100)
    sender_name: str = Field(default="", max_length=100)


class ChannelGroupSeenResult(BaseModel):
    group_id: str
    status: Literal["pending", "approved", "ignored"]


class ChannelQrResult(BaseModel):
    """Current QR login state; the image is present only while ``status == "qr"``."""

    status: ChannelStatus
    image_base64: str | None = None
