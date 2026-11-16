"""Shared registry of the messaging channel platforms Runtime supports.

Every shared membership set, label, id pattern, limit and credential-redaction
prefix derives from this one descriptor table, so wiring the next platform is
one entry here plus its adapter-specific modules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ChannelPlatform:
    """One supported channel platform and its shared behavior."""

    id: str
    label: str
    auth: Literal["qr", "token"]
    # Auth kind the connect card uses; Zalo Bot reports the same "api_key"
    # value as an MCP connector, the personal account a QR sign-in.
    connect_auth_kind: str
    id_pattern: str
    # Group approval applies to this platform.
    groups: bool
    # Declared default; the loaded plugin may still report its own limit.
    max_message_length: int
    tools_module: str | None
    # File name under the social-channels skill's ``platforms/`` directory.
    skill_section: str
    # Request-body redaction prefix for a write-only credential operation.
    token_operation_prefix: str | None = None


PLATFORMS: dict[str, ChannelPlatform] = {
    "zalo": ChannelPlatform(
        id="zalo",
        label="Zalo",
        auth="qr",
        connect_auth_kind="qr",
        id_pattern=r"^[0-9]{1,32}$",
        groups=True,
        max_message_length=2000,
        tools_module="xnobrain.integrations.zalo_tools",
        skill_section="zalo.md",
    ),
    "zalo_bot": ChannelPlatform(
        id="zalo_bot",
        label="Zalo Bot",
        auth="token",
        connect_auth_kind="api_key",
        id_pattern=r"^[A-Za-z0-9_-]{1,64}$",
        groups=True,
        max_message_length=2000,
        tools_module="xnobrain.integrations.zalo_bot_tools",
        skill_section="zalo_bot.md",
        token_operation_prefix="channels_zalo_bot_",
    ),
    "telegram": ChannelPlatform(
        id="telegram",
        label="Telegram",
        auth="token",
        connect_auth_kind="api_key",
        # Telegram user ids are positive; group and supergroup chat ids are
        # negative (for example -1001234567890).
        id_pattern=r"^-?[0-9]{1,20}$",
        groups=True,
        # The platform plugin registers max_message_length=4096.
        max_message_length=4096,
        # Hermes already ships the Telegram adapter and its tools; Runtime adds
        # no Telegram-specific tool module.
        tools_module=None,
        skill_section="telegram.md",
        token_operation_prefix="channels_telegram_",
    ),
    "discord": ChannelPlatform(
        id="discord",
        label="Discord",
        auth="token",
        connect_auth_kind="api_key",
        # Discord user, channel and thread ids are numeric snowflakes. The
        # adapter puts the thread id itself in ``chat_id`` for a thread.
        id_pattern=r"^[0-9]{1,20}$",
        groups=True,
        # The platform plugin registers max_message_length=2000.
        max_message_length=2000,
        # Hermes already ships the Discord adapter; Runtime adds no
        # Discord-specific tool module.
        tools_module=None,
        skill_section="discord.md",
        token_operation_prefix="channels_discord_",
    ),
    "slack": ChannelPlatform(
        id="slack",
        label="Slack",
        auth="token",
        connect_auth_kind="api_key",
        # Slack channel ids are ``C``/``G``/``D`` and member ids ``U``/``W``,
        # all uppercase alphanumerics. A thread reply keeps the channel id in
        # ``chat_id`` and carries the thread timestamp separately.
        id_pattern=r"^[A-Z][A-Z0-9]{1,32}$",
        groups=True,
        # The platform plugin registers max_message_length=39000.
        max_message_length=39000,
        # Hermes already ships the Slack adapter; Runtime adds no Slack-specific
        # tool module.
        tools_module=None,
        skill_section="slack.md",
        token_operation_prefix="channels_slack_",
    ),
}

# Pydantic Literal type covering exactly the registered platform ids.
ChannelPlatformId = Literal[tuple(PLATFORMS)]


def platform_ids() -> tuple[str, ...]:
    """Every supported platform id, in registry order."""
    return tuple(PLATFORMS)


def get_platform(platform_id: object) -> ChannelPlatform | None:
    """Return the descriptor for one id, or ``None`` when unsupported."""
    return PLATFORMS.get(str(platform_id or "").strip().lower())


def group_platform_ids() -> tuple[str, ...]:
    """Platform ids where group approval applies, in registry order."""
    return tuple(entry.id for entry in PLATFORMS.values() if entry.groups)


def matches_id_pattern(platform_id: object, value: object) -> bool:
    """True when ``value`` matches the platform's own thread/chat id pattern."""
    entry = get_platform(platform_id)
    if entry is None:
        return False
    return re.fullmatch(entry.id_pattern, str(value or "")) is not None
