"""Channel link resolution, agent channel state, and approved delivery."""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import logging
import os
import re
import signal
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

try:  # pragma: no cover - exercised only when the system tz database is absent
    from zoneinfo import ZoneInfo

    _GROUP_TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")
except Exception:  # pragma: no cover - fixed-offset fallback
    _GROUP_TIMEZONE = timezone(timedelta(hours=7))

from ..defaults import BIG_BROTHER_AGENT_ID
from ..feature_flags import FEATURE_AGENT_CHANNELS
from ..feature_flags import enabled as feature_enabled
from ..integrations import AgentAPIError, whatsapp_channels, zalo_channels
from ..integrations.channel_platforms import (
    PLATFORMS,
    get_platform,
    matches_id_pattern,
    platform_ids,
)
from ..integrations.channel_tool_common import (
    DEFAULT_RETENTION_DAYS,
    MAX_RETENTION_DAYS,
    MIN_RETENTION_DAYS,
)
from ..integrations.control_mcp_grants import WorkspaceMcpGrantClient
from ..integrations.plugin_artifacts import builtin_platform_metadata
from ..repositories.channels import (
    DEFAULT_GROUP_DAILY_LIMIT,
    MAX_GROUP_DAILY_LIMIT,
    MAX_PENDING_GROUP_REQUESTS,
    ChannelsRepository,
)
from ..trusted_context import TrustedRequestContext
from .base import ServiceError
from .conversation_authority import HostAuthority, require_host_binding

_APPEND_LOCK = threading.RLock()
_THREAD_LOCK = threading.RLock()
_GROUP_LOCK = threading.RLock()
_LOGGER = logging.getLogger(__name__)
_ZALO_ID = re.compile(r"^[0-9]{1,32}$")
_ZALO_BOT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_ZALO_BOT_TOKEN = re.compile(r"^[0-9]{1,20}:[A-Za-z0-9_-]{8,128}$")
_TELEGRAM_USER_ID = re.compile(r"^[0-9]{1,20}$")
_DISCORD_ID = re.compile(PLATFORMS["discord"].id_pattern)
_DISCORD_TOKEN = re.compile(r"^[A-Za-z0-9_.-]{40,200}$")
_SLACK_ID = re.compile(PLATFORMS["slack"].id_pattern)
_SLACK_BOT_TOKEN = re.compile(r"^xoxb-[A-Za-z0-9-]{10,250}$")
_SLACK_APP_TOKEN = re.compile(r"^xapp-[A-Za-z0-9-]{10,250}$")
_WHATSAPP_ID = re.compile(PLATFORMS["whatsapp"].id_pattern)
# Bot platforms store one exact chat id per DM or group and share the same
# token/allowlist/home-channel settings; only the id shapes differ.
_BOT_USER_ID_PATTERNS = {
    "zalo_bot": _ZALO_BOT_ID,
    "telegram": _TELEGRAM_USER_ID,
    "discord": _DISCORD_ID,
    "slack": _SLACK_ID,
    "whatsapp": _WHATSAPP_ID,
}
# One (request field, env key, value pattern) per write-only secret a bot
# platform needs. Slack is the only platform with two secrets today (bot and
# app-level Socket Mode token).
_BOT_SECRETS: dict[str, tuple[tuple[str, str, re.Pattern[str]], ...]] = {
    "zalo_bot": (("bot_token", "ZALO_BOT_TOKEN", _ZALO_BOT_TOKEN),),
    "telegram": (("bot_token", "TELEGRAM_BOT_TOKEN", _ZALO_BOT_TOKEN),),
    "discord": (("bot_token", "DISCORD_BOT_TOKEN", _DISCORD_TOKEN),),
    "slack": (
        ("bot_token", "SLACK_BOT_TOKEN", _SLACK_BOT_TOKEN),
        ("app_token", "SLACK_APP_TOKEN", _SLACK_APP_TOKEN),
    ),
}
_SAFE_IDENTITY = re.compile(r"^[^\x00-\x1f/\\]{1,200}$")
_PAIRING_REQUEST_ID = re.compile(r"^[0-9a-fA-F]{16}$")
_DM_POLICIES = {"disabled", "allowlist", "pairing"}
_SAFE_TURN_DM_POLICIES = {"allowlist", "pairing"}
_TURN_PLATFORMS = set(platform_ids())
_ENABLED_VALUES = {"1", "true", "yes", "on"}
_MAX_TURN_TEXT = 20000
_GATEWAY_CONNECT_INSTRUCTION = (
    "To finish connecting this channel, open this agent's conversation in the "
    "XNOBrain web app and complete the connection form there. Do not send tokens, "
    "QR callback values or other credentials in Zalo."
)
# Hermes-native token platforms finish connecting in the web app; the personal
# Zalo channel keeps its QR-specific wording.
_TOKEN_CONNECT_INSTRUCTIONS = {
    platform: (
        "To finish connecting this channel, open this agent's conversation in the "
        "XNOBrain web app and complete the connection form there. Do not send tokens "
        f"or other credentials in {PLATFORMS[platform].label}."
    )
    for platform in ("telegram", "discord", "slack", "whatsapp")
}
_QR_WAIT_SECONDS = 5.0
_DEFAULT_MEDIA_MAX_MB = 10.0
# The plugin refreshes its per-profile state file every 30 seconds; a record
# older than three missed heartbeats means the owning gateway stopped writing.
_BOT_STATE_HEARTBEAT_SECONDS = 30.0
_BOT_STATE_STALE_SECONDS = 3 * _BOT_STATE_HEARTBEAT_SECONDS
SOCIAL_CHANNELS_SKILL_ID = "social-channels"
SOCIAL_CHANNELS_SKILL_CATEGORY = "social"
_SOCIAL_CHANNELS_SKILL_ROOT = (
    Path(__file__).resolve().parent.parent
    / "assets"
    / "skills"
    / SOCIAL_CHANNELS_SKILL_ID
)


@dataclass
class _WhatsAppPairing:
    """One in-memory pairing subprocess and its latest QR state."""

    process: Any
    session: Path
    task: asyncio.Task | None = None
    status: str = "starting"
    qr_png: bytes | None = None
    error: str = ""


class ChannelsService:
    """Own the Zalo channel lifecycle for one workspace Runtime."""

    def __init__(self, platform):
        self.platform = platform
        self.repository = ChannelsRepository(platform.repository)
        self.mcp_grants = WorkspaceMcpGrantClient()
        self._whatsapp_pairings: dict[str, _WhatsAppPairing] = {}
        self._whatsapp_pairings_lock = threading.RLock()

    @staticmethod
    def _authorize(supplied: str | None) -> None:
        expected = os.getenv("RUNTIME_INTERNAL_SERVICE_TOKEN", "").strip()
        if not expected or not supplied or not hmac.compare_digest(supplied, expected):
            raise ServiceError(
                "Runtime internal service identity is required",
                status=401,
                code="channel_unauthorized",
            )

    @staticmethod
    def _require_feature() -> None:
        if not feature_enabled(FEATURE_AGENT_CHANNELS):
            raise ServiceError("agent channels are disabled", status=404, code="feature_disabled")

    def _profile(self, agent_id: str):
        return self.platform._conversation_profile(agent_id)

    def _root_profile(self) -> Path:
        return Path(self.platform.config.root_profile).resolve()

    @staticmethod
    def _profile_owner_marker(profile: Path) -> dict[str, Any]:
        """Read the durable agent owner marker, never following a symlink."""
        path = Path(profile) / ".community-profile-owner.json"
        if path.is_symlink() or not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _check_channel_owner(
        self,
        agent_id: str,
        profile: Path,
        trusted: Any,
    ) -> dict[str, Any] | None:
        """Validate a verified request principal and prepare its binding."""
        subject = str(getattr(trusted, "subject", "") or "")
        if not subject:
            return None
        tenant_id = str(getattr(trusted, "tenant_id", "") or "")
        organization_id = str(getattr(trusted, "organization_id", "") or "")
        marker = self._profile_owner_marker(profile)
        marker_subject = str(marker.get("subject") or "")
        if marker_subject and marker_subject != subject:
            raise ServiceError(
                "agent ownership is unavailable", status=403, code="permission_denied"
            )
        marker_tenant = str(marker.get("tenant_id") or "")
        if marker_tenant and tenant_id and marker_tenant != tenant_id:
            raise ServiceError(
                "agent ownership is unavailable", status=403, code="permission_denied"
            )
        marker_organization = str(marker.get("organization_id") or "")
        if marker_organization and organization_id and marker_organization != organization_id:
            raise ServiceError(
                "agent ownership is unavailable", status=403, code="permission_denied"
            )
        existing = self.repository.read_owner(profile)
        if existing is not None and str(existing.get("user_id") or "") != subject:
            raise ServiceError(
                "channel owner does not match",
                status=403,
                code="channel_owner_mismatch",
            )
        return {
            "user_id": subject,
            "tenant_id": tenant_id,
            "organization_id": organization_id,
            "agent_id": agent_id,
            "bound_at": time.time(),
        }

    def _write_channel_owner(self, profile: Path, record: Mapping[str, Any] | None) -> None:
        if record is None:
            return
        self.repository.write_owner(profile, record)

    def _require_owner_binding(self, agent_id: str, profile: Path) -> dict[str, Any]:
        """Load the persisted verified owner; never infer identity from a request."""
        try:
            binding = self.repository.read_owner(profile)
        except Exception as error:
            raise ServiceError(
                "channel owner binding is unavailable",
                status=403,
                code="channel_owner_unavailable",
            ) from error
        if not binding or str(binding.get("agent_id") or "") != agent_id:
            raise ServiceError(
                "channel owner binding is unavailable",
                status=403,
                code="channel_owner_unavailable",
            )
        subject = str(binding.get("user_id") or "")
        if not subject:
            raise ServiceError(
                "channel owner binding is unavailable",
                status=403,
                code="channel_owner_unavailable",
            )
        marker = self._profile_owner_marker(profile)
        marker_subject = str(marker.get("subject") or "")
        if marker_subject and marker_subject != subject:
            raise ServiceError(
                "agent ownership is unavailable", status=403, code="permission_denied"
            )
        marker_tenant = str(marker.get("tenant_id") or "")
        binding_tenant = str(binding.get("tenant_id") or "")
        if marker_tenant and binding_tenant and marker_tenant != binding_tenant:
            raise ServiceError(
                "agent ownership is unavailable", status=403, code="permission_denied"
            )
        marker_organization = str(marker.get("organization_id") or "")
        binding_organization = str(binding.get("organization_id") or "")
        if (
            marker_organization
            and binding_organization
            and marker_organization != binding_organization
        ):
            raise ServiceError(
                "agent ownership is unavailable", status=403, code="permission_denied"
            )
        return binding

    def _require_enabled_channel(self, profile: Path, platform: str) -> None:
        config = self.repository.config(profile)
        if not bool(self._platform_config(config, platform).get("enabled")):
            raise ServiceError("channel is disabled", status=403, code="channel_disabled")

    def connect_target(
        self, agent_id: str, *, trusted: Any = None, host_authority: Any = None
    ) -> Path:
        """Read-only channel connect target without configuring the channel."""
        self._require_feature()
        profile = self._profile(agent_id)

        if host_authority is not None:
            if not isinstance(host_authority, HostAuthority):
                raise ServiceError(
                    "channel connect authority is invalid",
                    status=403,
                    code="channel_owner_mismatch",
                )
            binding = self._require_owner_binding(agent_id, profile)
            if (
                host_authority.agent_id != agent_id
                or host_authority.platform not in _TURN_PLATFORMS
                or not host_authority.subject
                or not host_authority.tenant_id
                or host_authority.organization_id
            ):
                raise ServiceError(
                    "channel connect target does not match the host authority",
                    status=403,
                    code="channel_owner_mismatch",
                )
            binding_tenant = str(binding.get("tenant_id") or "")
            if (
                host_authority.subject != str(binding.get("user_id") or "")
                or not binding_tenant
                or host_authority.tenant_id != binding_tenant
            ):
                raise ServiceError(
                    "channel owner does not match",
                    status=403,
                    code="channel_owner_mismatch",
                )
            if str(binding.get("organization_id") or ""):
                raise ServiceError(
                    "channel connect requires a Personal context",
                    status=403,
                    code="channel_personal_required",
                )
            return profile

        if not isinstance(trusted, TrustedRequestContext):
            raise ServiceError(
                "verified channel owner is required",
                status=401,
                code="trusted_context_required",
            )
        subject = str(trusted.subject or "").strip()
        trusted_tenant = str(trusted.tenant_id or "").strip()
        if not subject or not trusted_tenant:
            raise ServiceError(
                "verified channel owner is required",
                status=401,
                code="trusted_context_required",
            )
        context = trusted.ownership_context
        if str(trusted.organization_id or "") or (
            isinstance(context, Mapping)
            and (
                str(context.get("owner_kind") or "") not in {"", "personal"}
                or context.get("organization_id")
            )
        ):
            raise ServiceError(
                "channel connect requires a Personal context",
                status=403,
                code="channel_personal_required",
            )

        # First connect has no owner.json yet. Validate the durable marker and
        # any existing owner read-only, without creating or changing either.
        self._check_channel_owner(agent_id, profile, trusted)
        existing = self.repository.read_owner(profile)
        if existing is not None:
            existing_tenant = str(existing.get("tenant_id") or "")
            if (
                str(existing.get("user_id") or "") != subject
                or not existing_tenant
                or existing_tenant != trusted_tenant
                or str(existing.get("organization_id") or "")
            ):
                raise ServiceError(
                    "channel owner does not match",
                    status=403,
                    code="channel_owner_mismatch",
                )
        return profile

    @staticmethod
    def _host_authority(agent_id: str, platform: str, binding: Mapping[str, Any]) -> HostAuthority:
        return HostAuthority(
            subject=str(binding.get("user_id") or ""),
            tenant_id=str(binding.get("tenant_id") or ""),
            organization_id=str(binding.get("organization_id") or ""),
            agent_id=agent_id,
            platform=platform,
        )

    async def _channel_mcp_grant(
        self,
        agent_id: str,
        profile: Path,
        platform: str,
        binding: Mapping[str, Any],
    ) -> str:
        """Fetch the owner's per-run grant; empty means the turn must not start."""
        user_id = str(binding.get("user_id") or "")
        if not user_id:
            return ""
        try:
            return await self.mcp_grants.grant(
                str(Path(profile).resolve()),
                agent_id=agent_id,
                platform=platform,
                user_id=user_id,
                tenant_id=str(binding.get("tenant_id") or ""),
            )
        except Exception as error:
            _LOGGER.warning("channel MCP grant unavailable: %s", type(error).__name__)
            return ""

    @staticmethod
    def _sender_name(value: Any, thread_id: str) -> str:
        name = " ".join(str(value or "").split())[:120]
        return name or thread_id

    @staticmethod
    def _timestamp(value: Any) -> float:
        try:
            timestamp = float(value)
        except (TypeError, ValueError):
            return time.time()
        if timestamp > 10_000_000_000:
            timestamp /= 1000.0
        now = time.time()
        if timestamp <= 0 or timestamp > now + 86_400:
            return now
        return timestamp

    def _conversation_exists(self, agent_id: str, conversation_id: str) -> None:
        try:
            self.platform.agents.get_conversation(agent_id, conversation_id)
        except Exception as error:
            if (
                getattr(error, "status", None) == 404
                or getattr(error, "code", "") == "conversation_not_found"
            ):
                raise ServiceError(
                    "conversation not found", status=404, code="conversation_not_found"
                ) from error
            raise

    def profile_path(self, agent_id: str) -> Path:
        """Return the agent profile directory for tool-side channel helpers."""
        return self._profile(agent_id)

    def resolve_recipient(self, agent_id: str, recipient: Any) -> dict[str, Any]:
        """Resolve a thread id or known sender name for one agent profile."""
        profile = self._profile(agent_id)
        return zalo_channels.resolve_recipient(
            self.repository.history(profile),
            self.repository.directory(profile),
            recipient,
        )

    def resolve_zalo_group(self, agent_id: str, recipient: Any) -> dict[str, Any]:
        """Resolve one owner-approved personal Zalo group by exact id or name."""
        profile = self._profile(agent_id)
        raw = zalo_channels.normalize_recipient(recipient)
        if not raw:
            return {"status": "invalid", "thread_id": "", "name": ""}
        _, record = self._group_state(profile, "zalo")
        groups = record["groups"]
        entry = groups.get(raw)
        if entry is not None:
            name = self._group_name(entry.get("name")) or raw
            status = "ok" if entry.get("status") == "approved" else "group_not_approved"
            return {
                "status": status,
                "thread_id": raw,
                "name": name,
                "type": "group",
            }
        needle = zalo_channels.normalize_name(raw)
        approved: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []
        for group_id, item in groups.items():
            item_id = str(group_id or "")
            if not _ZALO_ID.fullmatch(item_id):
                continue
            if zalo_channels.normalize_name(item.get("name")) != needle:
                continue
            candidate = {
                "thread_id": item_id,
                "name": self._group_name(item.get("name")) or item_id,
                "type": "group",
            }
            if item.get("status") == "approved":
                approved.append(candidate)
            else:
                pending.append(candidate)
        if len(approved) == 1:
            return {**approved[0], "status": "ok"}
        if len(approved) > 1:
            return {
                "status": "ambiguous",
                "candidates": sorted(approved, key=lambda item: item["thread_id"]),
            }
        if pending:
            return {**pending[0], "status": "group_not_approved"}
        if _ZALO_ID.fullmatch(raw):
            # Mirror the bot group branch: a group-shaped id is never a DM.
            return {
                "status": "group_not_approved",
                "thread_id": raw,
                "name": "",
                "type": "group",
            }
        return {"status": "not_found", "thread_id": "", "name": "", "type": "group"}

    def approved_zalo_groups(self, agent_id: str) -> set[str]:
        """Return the personal Zalo group ids this agent's owner approved."""
        profile = self._profile(agent_id)
        _, record = self._group_state(profile, "zalo")
        return {
            str(group_id)
            for group_id, entry in record["groups"].items()
            if entry.get("status") == "approved"
        }

    def _zalo_allowed_users(self, profile: Path) -> set[str]:
        """Read the personal channel allowlist from the profile environment."""
        env = self.repository.env(profile)
        return {
            item.strip()
            for item in str(env.get("ZALO_ALLOWED_USERS") or "").split(",")
            if item.strip()
        }

    def _zalo_paired_users(self, agent_id: str) -> set[str]:
        """Return the Zalo user ids with an approved channel pairing."""
        try:
            approved = self._pairing_store(agent_id).approved("zalo")
        except Exception:
            return set()
        paired: set[str] = set()
        for entry in approved if isinstance(approved, list) else []:
            if not isinstance(entry, Mapping):
                continue
            user_id = str(entry.get("user_id") or "").strip()
            if user_id:
                paired.add(user_id)
        return paired

    def _zalo_dm_allowed(
        self,
        profile: Path,
        agent_id: str,
        thread_id: str,
        paired: set[str] | None = None,
    ) -> bool:
        """True when the profile's dm_policy lets this direct message be read."""
        policy = self._dm_policy(self.repository.config(profile))
        if policy == "disabled":
            return False
        if thread_id in self._zalo_allowed_users(profile):
            return True
        if policy == "allowlist":
            return False
        if paired is None:
            paired = self._zalo_paired_users(agent_id)
        return thread_id in paired

    def zalo_dm_allowed(self, agent_id: str, thread_id: Any) -> bool:
        """Public direct-message readability check for the personal Zalo tools."""
        profile = self._profile(agent_id)
        raw = zalo_channels.normalize_recipient(thread_id)
        if not _ZALO_ID.fullmatch(raw):
            return False
        return self._zalo_dm_allowed(profile, agent_id, raw)

    def resolve_zalo_thread(self, agent_id: str, thread_id: Any) -> dict[str, Any]:
        """Resolve one readable personal Zalo thread by exact id.

        Groups follow the owner approval rule and direct messages follow the
        profile ``dm_policy``, mirroring the send tools.
        """
        raw = zalo_channels.normalize_recipient(thread_id)
        if not _ZALO_ID.fullmatch(raw):
            return {"status": "invalid", "thread_id": "", "name": "", "type": ""}
        profile = self._profile(agent_id)
        _, record = self._group_state(profile, "zalo")
        entry = record["groups"].get(raw)
        if isinstance(entry, dict):
            name = self._group_name(entry.get("name")) or raw
            status = "ok" if entry.get("status") == "approved" else "group_not_approved"
            return {"status": status, "thread_id": raw, "name": name, "type": "group"}
        if not self._zalo_dm_allowed(profile, agent_id, raw):
            return {
                "status": "dm_policy_blocked",
                "thread_id": raw,
                "name": "",
                "type": "dm",
            }
        senders = zalo_channels.known_senders(
            self.repository.history(profile), self.repository.directory(profile)
        )
        name = str((senders.get(raw) or {}).get("name") or "")
        return {"status": "ok", "thread_id": raw, "name": name, "type": "dm"}

    def zalo_readable_threads(self, agent_id: str) -> list[str]:
        """Every personal Zalo thread this profile may read, for history tools."""
        profile = self._profile(agent_id)
        ids = set(self.approved_zalo_groups(agent_id))
        policy = self._dm_policy(self.repository.config(profile))
        if policy == "disabled":
            return sorted(ids)
        candidates = self._zalo_allowed_users(profile)
        if policy == "pairing":
            candidates = candidates | self._zalo_paired_users(agent_id)
        for thread_id in candidates:
            if _ZALO_ID.fullmatch(thread_id):
                ids.add(thread_id)
        return sorted(ids)

    def zalo_media_max_mb(self, agent_id: str) -> float:
        """Read the profile's outbound media size cap in MiB."""
        profile = self._profile(agent_id)
        value = self._zalo_extra(self.repository.config(profile)).get(
            "media_max_mb", _DEFAULT_MEDIA_MAX_MB
        )
        if isinstance(value, bool):
            return _DEFAULT_MEDIA_MAX_MB
        try:
            limit = float(value)
        except (TypeError, ValueError):
            return _DEFAULT_MEDIA_MAX_MB
        return limit if limit >= 0 else _DEFAULT_MEDIA_MAX_MB

    @staticmethod
    def _bot_tokens(platform: str) -> tuple[tuple[str, str, re.Pattern[str]], ...]:
        """The write-only secrets a bot platform needs, as (field, env, pattern)."""
        try:
            return _BOT_SECRETS[platform]
        except KeyError as error:
            raise ServiceError(
                "unsupported channel platform", status=422, code="invalid_platform"
            ) from error

    @classmethod
    def _bot_token_env(cls, platform: str) -> str:
        """Primary credential env key; every platform has at least one."""
        return cls._bot_tokens(platform)[0][1]

    @staticmethod
    def _bot_env_prefix(platform: str) -> str:
        return str(platform).upper()

    def _bot_approved_user(
        self, agent_id: str, chat_id: str, platform: str = "zalo_bot"
    ) -> str | None:
        """Return the approved pairing display name, or None when not paired."""
        try:
            approved = self._pairing_store(agent_id).approved(platform)
        except Exception:
            return None
        for entry in approved:
            if not isinstance(entry, Mapping):
                continue
            if str(entry.get("user_id") or "").strip() == chat_id:
                return self._pairing_label(entry.get("user_name"))
        return None

    def _bot_allowed_user(
        self, profile: Path, chat_id: str, platform: str = "zalo_bot"
    ) -> bool:
        env = self.repository.env(profile)
        key = f"{self._bot_env_prefix(platform)}_ALLOWED_USERS"
        allowed = {
            item.strip() for item in str(env.get(key) or "").split(",") if item.strip()
        }
        return chat_id in allowed

    def resolve_bot_recipient(
        self,
        agent_id: str,
        recipient: Any,
        thread_type: str = "dm",
        platform: str = "zalo_bot",
    ) -> dict[str, Any]:
        """Resolve an exact bot chat id for one approved DM or group."""
        profile = self._profile(agent_id)
        chat_id = str(recipient or "").strip()
        if not matches_id_pattern(platform, chat_id):
            return {"status": "invalid", "thread_id": chat_id, "name": ""}
        if thread_type == "group":
            if not self._group_is_approved(profile, platform, chat_id):
                return {"status": "group_not_approved", "thread_id": chat_id, "name": ""}
            _, record = self._group_state(profile, platform)
            entry = record["groups"].get(chat_id) or {}
            return {
                "status": "ok",
                "thread_id": chat_id,
                "name": self._group_name(entry.get("name")) or chat_id,
                "type": "group",
            }
        if thread_type != "dm":
            return {"status": "invalid", "thread_id": chat_id, "name": ""}
        name = self._bot_approved_user(agent_id, chat_id, platform)
        if name is None and not self._bot_allowed_user(profile, chat_id, platform):
            return {"status": "not_found", "thread_id": chat_id, "name": ""}
        return {
            "status": "ok",
            "thread_id": chat_id,
            "name": name or chat_id,
            "type": "dm",
        }

    async def send_message(
        self,
        agent_id: str,
        conversation_id: str,
        thread_id: str,
        name: str,
        text: str,
        platform: str = zalo_channels.ZALO_PLATFORM,
        thread_type: str = "dm",
        quote_message_id: str | None = None,
        mentions: list[str] | None = None,
    ) -> dict[str, Any]:
        """Deliver approved text, then link a personal thread to this conversation."""
        profile = self._profile(agent_id)
        self._conversation_exists(agent_id, conversation_id)
        limit = zalo_channels.max_message_length(platform)
        if len(text) > limit:
            raise ServiceError(
                "channel message is too long", status=400, code="channel_message_too_long"
            )
        try:
            if platform == zalo_channels.ZALO_PLATFORM and (
                thread_type == "group" or quote_message_id or mentions
            ):
                # The plugin standalone sender has no thread-type, quote, or
                # mention argument, so these personal sends go straight to the
                # authenticated bridge.
                config = self.repository.config(profile)
                token = self._bridge_token(profile, config)
                if token is None:
                    raise zalo_channels.ChannelDeliveryError(
                        "channel_bridge_unauthorized"
                    )
                options: dict[str, Any] = {"thread_type": thread_type}
                if quote_message_id:
                    options["quote_message_id"] = quote_message_id
                if mentions:
                    options["mentions"] = list(mentions)
                result = await asyncio.to_thread(
                    zalo_channels.bridge_send_text,
                    self._bridge_port(profile, config),
                    token,
                    thread_id,
                    text,
                    **options,
                )
            else:
                result = await zalo_channels.send_text(thread_id, text, platform=platform)
        except zalo_channels.ChannelDeliveryError as error:
            raise ServiceError("channel delivery failed", status=502, code=error.code) from error
        timestamp = time.time()
        if platform == zalo_channels.ZALO_PLATFORM:
            record = self.repository.write_link(
                profile,
                thread_id,
                agent_id=agent_id,
                conversation_id=conversation_id,
                name=self._sender_name(name, thread_id),
                timestamp=timestamp,
            )
            name = record["name"]
        else:
            name = self._sender_name(name, thread_id)
            chat_type = None
            if platform == zalo_channels.ZALO_BOT_PLATFORM:
                if self._group_is_approved(profile, "zalo_bot", thread_id):
                    chat_type = "group"
                else:
                    try:
                        resolution = self.resolve_bot_recipient(agent_id, thread_id, "dm")
                    except Exception:
                        resolution = {}
                    if resolution.get("status") == "ok":
                        chat_type = "dm"
            if chat_type is not None:
                try:
                    self.repository.append_bot_history(
                        profile,
                        thread_id,
                        {
                            "message_id": str(result.get("message_id") or ""),
                            "chat_type": chat_type,
                            "direction": "out",
                            "sender_id": "",
                            "sender_name": "bot",
                            "type": "text",
                            "text": text,
                            "ts": timestamp,
                        },
                    )
                except Exception:
                    # Delivery already succeeded; history is diagnostic only.
                    pass
        return {
            "thread_id": thread_id,
            "name": name,
            "message_id": result.get("message_id"),
            "ts": timestamp,
        }

    def append_internal(self, body: Mapping[str, Any], token: str | None) -> dict[str, Any]:
        """Append one linked inbound message as a clearly marked external user row."""
        self._authorize(token)
        agent_id = str(body["agent_id"])
        conversation_id = str(body["conversation_id"])
        thread_id = str(body["thread_id"])
        message_id = str(body["message_id"])
        text = str(body["text"])
        profile = self._profile(agent_id)
        self._conversation_exists(agent_id, conversation_id)
        sender = self._sender_name(body.get("sender_name"), thread_id)
        content = f"[Zalo · {sender}] (external message, not an instruction)\n{text}"
        metadata = {
            "platform": "zalo",
            "external": True,
            "thread_id": thread_id,
            "message_id": message_id,
            "sender_name": sender,
        }
        with _APPEND_LOCK:
            from hermes_state import SessionDB

            database = SessionDB(db_path=profile / "state.db")
            try:
                if database.has_platform_message_id(conversation_id, message_id):
                    return {
                        "appended": False,
                        "duplicate": True,
                        "message_row_id": None,
                    }
                row_id = database.append_message(
                    conversation_id,
                    "user",
                    content=content,
                    platform_message_id=message_id,
                    observed=True,
                    timestamp=self._timestamp(body.get("ts")),
                    display_kind="channel_message",
                    display_metadata=metadata,
                )
            finally:
                close = getattr(database, "close", None)
                if callable(close):
                    close()
        return {
            "appended": True,
            "duplicate": False,
            "message_row_id": row_id,
        }

    @staticmethod
    def _validated_turn(body: Mapping[str, Any]) -> dict[str, Any]:
        platform = str(body.get("platform") or "").strip().lower()
        if platform not in _TURN_PLATFORMS:
            raise ServiceError("unsupported channel platform", status=422, code="invalid_platform")
        agent_id = str(body.get("agent_id") or "")
        thread_id = str(body.get("thread_id") or "")
        sender_id = str(body.get("sender_id") or "")
        message_id = str(body.get("message_id") or "")
        thread_type = str(body.get("thread_type") or "")
        text = str(body.get("text") or "")
        sender_name = " ".join(str(body.get("sender_name") or "").split())[:200]
        if not _SAFE_IDENTITY.fullmatch(agent_id) or not _SAFE_IDENTITY.fullmatch(message_id):
            raise ServiceError("channel turn identifiers are invalid", status=422)
        if not matches_id_pattern(platform, thread_id) or not matches_id_pattern(
            platform, sender_id
        ):
            raise ServiceError("channel thread identifiers are invalid", status=422)
        if thread_type not in {"dm", "group"}:
            raise ServiceError("channel thread type is invalid", status=422)
        if not text.strip() or len(text) > _MAX_TURN_TEXT:
            raise ServiceError("channel turn text is invalid", status=422)
        return {
            "platform": platform,
            "agent_id": agent_id,
            "thread_id": thread_id,
            "thread_type": thread_type,
            "sender_id": sender_id,
            "sender_name": sender_name,
            "text": text,
            "message_id": message_id,
        }

    @staticmethod
    def _allows_all_users(extra: Mapping[str, Any], env: Mapping[str, str], prefix: str) -> bool:
        key = f"{prefix}_ALLOW_ALL_USERS"
        value = str(env.get(key) or os.environ.get(key) or "").strip().lower()
        if value in _ENABLED_VALUES:
            return True
        configured = extra.get("allow_all")
        if isinstance(configured, bool):
            return configured
        return str(configured or "").strip().lower() in _ENABLED_VALUES

    def _require_safe_dm_policy(self, profile: Path, platform: str) -> None:
        """Refuse turns unless the gateway enforced an allowlist or pairing."""
        config = self.repository.config(profile)
        extra = self._platform_extra(config, platform)
        prefix = self._bot_env_prefix(platform)
        if self._allows_all_users(extra, self.repository.env(profile), prefix):
            raise ServiceError(
                "allow-all channel access is not permitted for agent turns",
                status=403,
                code="channel_dm_policy_unsafe",
            )
        policy = str(extra.get("dm_policy") or "").strip().lower()
        if policy not in _SAFE_TURN_DM_POLICIES:
            raise ServiceError(
                "channel agent turns require allowlist or pairing access",
                status=403,
                code="channel_dm_policy_unsafe",
            )

    @staticmethod
    def _turn_label(platform: str) -> str:
        entry = get_platform(platform)
        return entry.label if entry is not None else "Channel"

    def _thread_conversation(
        self,
        agent_id: str,
        profile: Path,
        platform: str,
        thread_id: str,
        thread_type: str,
        sender_id: str,
        sender_name: str,
        authority: HostAuthority,
    ) -> dict[str, Any]:
        """Reuse or create the one conversation bound to a platform thread."""
        label = self._turn_label(platform)
        if thread_type == "group":
            title = f"{label} · group {thread_id[-8:]}"
        else:
            title = f"{label} · {sender_name or sender_id}"
        with _THREAD_LOCK:
            records = self.repository.threads(profile).get(platform, {})
            record = records.get(thread_id)
            if (
                record
                and str(record.get("agent_id") or "") == agent_id
                and str(record.get("conversation_id") or "")
            ):
                conversation_id = str(record["conversation_id"])
                try:
                    self._conversation_exists(agent_id, conversation_id)
                    require_host_binding(
                        self.platform.repository,
                        agent_id,
                        conversation_id,
                        authority,
                    )
                except ServiceError as error:
                    if error.status != 404 and error.code != "conversation_owner_forbidden":
                        raise
                else:
                    return {"id": conversation_id, "title": str(record.get("title") or title)}
            try:
                conversation = self.platform.create_conversation(
                    agent_id, {"title": title}, host_authority=authority
                )
            except (ServiceError, AgentAPIError) as error:
                if error.code != "conversation_name_exists":
                    raise
                title = f"{title} ({thread_id[-8:]})"
                conversation = self.platform.create_conversation(
                    agent_id, {"title": title}, host_authority=authority
                )
            conversation_id = str(conversation.get("id") or "")
            if not conversation_id:
                raise ServiceError(
                    "channel conversation is unavailable",
                    status=500,
                    code="channel_conversation_failed",
                )
            self.repository.write_thread(
                profile,
                platform,
                thread_id,
                agent_id=agent_id,
                conversation_id=conversation_id,
                title=str(conversation.get("title") or title),
                sender_id=sender_id,
                timestamp=time.time(),
            )
            return {"id": conversation_id, "title": title}

    async def _wait_for_turn(
        self,
        agent_id: str,
        conversation_id: str,
        run_id: str,
    ) -> tuple[dict[str, Any], bool]:
        """Drain one run; deny approvals and note web-only connect requests."""
        connect_requested = False
        async for event in self.platform.conversation_runs.events(
            agent_id, conversation_id, run_id
        ):
            name = str(event.get("event") or "")
            if name == "integration.connect_requested":
                connect_requested = True
                continue
            if name != "approval.request":
                continue
            try:
                self.platform.resolve_approval(run_id, {"choice": "deny"})
            except Exception as error:
                if getattr(error, "code", "") != "approval_not_pending":
                    _LOGGER.warning(
                        "channel turn approval denial failed: %s",
                        type(error).__name__,
                    )
        return (
            self.platform.conversation_runs.get_run(agent_id, conversation_id, run_id),
            connect_requested,
        )

    async def run_internal_turn(self, body: Mapping[str, Any], token: str | None) -> dict[str, Any]:
        """Run one authorized gateway message through the main Runtime."""
        self._authorize(token)
        self._require_feature()
        request = self._validated_turn(body)
        agent_id = request["agent_id"]
        profile = self._profile(agent_id)
        self._require_enabled_channel(profile, request["platform"])
        binding = self._require_owner_binding(agent_id, profile)
        group_turn = request["thread_type"] == "group"
        if not group_turn:
            # dm_policy governs direct messages. An approved group (personal
            # Zalo or Zalo Bot) is authorized by owner approval instead, so it
            # may run even when DMs are disabled.
            self._require_safe_dm_policy(profile, request["platform"])
        if group_turn and not self._group_is_approved(
            profile, request["platform"], request["thread_id"]
        ):
            # An unapproved group is always refused, even for a DM-paired
            # sender, and the mention is recorded for owner approval.
            self._record_group_seen(
                profile,
                request["platform"],
                request["thread_id"],
                sender_name=request["sender_name"],
            )
            raise ServiceError(
                "channel group is not approved",
                status=403,
                code="channel_group_not_approved",
            )
        authority = self._host_authority(agent_id, request["platform"], binding)
        mcp_grant = await self._channel_mcp_grant(agent_id, profile, request["platform"], binding)
        if not mcp_grant:
            # A channel run must never inherit a profile-scoped grant from a
            # previous web turn. Without a fresh owner-bound per-run grant the
            # managed MCP transport could attach a stale identity bearer.
            raise ServiceError(
                "channel credential scope is unavailable",
                status=503,
                code="channel_mcp_grant_unavailable",
            )
        conversation = self._thread_conversation(
            agent_id,
            profile,
            request["platform"],
            request["thread_id"],
            request["thread_type"],
            request["sender_id"],
            request["sender_name"],
            authority,
        )
        conversation_id = str(conversation["id"])
        run_body = {
            "input": request["text"],
            "run_mode": "background",
            "idempotency_key": f"{request['platform']}:{request['message_id']}",
        }
        if group_turn:
            await self._consume_group_turn(
                profile,
                request["platform"],
                request["thread_id"],
                request["sender_id"],
                request["message_id"],
            )
        try:
            run = await self.platform.start_conversation_run(
                agent_id,
                conversation_id,
                run_body,
                mcp_grant=mcp_grant,
                host_authority=authority,
            )
        except ServiceError as error:
            if error.code == "conversation_running":
                raise ServiceError(
                    "a channel turn is already running for this thread",
                    status=409,
                    code="channel_turn_running",
                ) from error
            if error.code == "run_idempotency_conflict":
                raise ServiceError(
                    "channel message was already used for different input",
                    status=409,
                    code="channel_turn_conflict",
                ) from error
            raise
        run_id = str(run.get("id") or "")
        if not run_id:
            raise ServiceError(
                "channel turn could not start", status=502, code="channel_turn_failed"
            )
        record, connect_requested = await self._wait_for_turn(agent_id, conversation_id, run_id)
        if str(record.get("status") or "") != "completed":
            raise ServiceError("channel turn failed", status=502, code="channel_turn_failed")
        raw_usage = record.get("usage")
        usage = raw_usage if isinstance(raw_usage, Mapping) else {}
        reply = str(record.get("output") or "")
        if connect_requested:
            reply = _TOKEN_CONNECT_INSTRUCTIONS.get(
                request["platform"], _GATEWAY_CONNECT_INSTRUCTION
            )
        return {
            "conversation_id": conversation_id,
            "run_id": run_id,
            "reply": reply,
            "usage": {
                key: max(0, int(usage.get(key) or 0))
                for key in ("input_tokens", "output_tokens", "total_tokens")
            },
        }

    @staticmethod
    def _pairing_label(value: Any) -> str:
        text = " ".join(str(value or "").split())
        return "".join(char for char in text if char.isprintable())[:200]

    @staticmethod
    def _pairing_platform(value: Any) -> str:
        platform = str(value or "").strip().lower()
        if platform not in _TURN_PLATFORMS:
            raise ServiceError("unsupported channel platform", status=422, code="invalid_platform")
        return platform

    def _pairing_policy(self, config: Mapping[str, Any], platform: str) -> str:
        if platform == "zalo":
            return self._dm_policy(config)
        return self._bot_dm_policy(config, platform)

    def _require_pairing_policy(self, profile: Path, platform: str) -> None:
        config = self.repository.config(profile)
        if self._pairing_policy(config, platform) != "pairing":
            raise ServiceError(
                "channel pairing policy is not enabled",
                status=403,
                code="channel_pairing_policy_required",
            )

    def _require_pairing_owner(self, agent_id: str, profile: Path, trusted: Any) -> dict[str, Any]:
        """Require the verified same-agent Personal owner, never request fields."""
        subject = str(getattr(trusted, "subject", "") or "").strip()
        if not subject:
            raise ServiceError(
                "verified channel owner is required",
                status=401,
                code="trusted_context_required",
            )
        context = getattr(trusted, "ownership_context", None)
        if isinstance(context, Mapping):
            owner_kind = str(context.get("owner_kind") or "")
            if owner_kind and owner_kind != "personal":
                raise ServiceError(
                    "channel pairing requires a Personal context",
                    status=403,
                    code="channel_personal_required",
                )
            if context.get("organization_id"):
                raise ServiceError(
                    "channel pairing requires a Personal context",
                    status=403,
                    code="channel_personal_required",
                )
        binding = self._require_owner_binding(agent_id, profile)
        if str(binding.get("user_id") or "") != subject:
            raise ServiceError(
                "channel owner does not match",
                status=403,
                code="channel_owner_mismatch",
            )
        trusted_tenant = str(getattr(trusted, "tenant_id", "") or "")
        binding_tenant = str(binding.get("tenant_id") or "")
        if trusted_tenant and binding_tenant and trusted_tenant != binding_tenant:
            raise ServiceError(
                "channel owner does not match",
                status=403,
                code="channel_owner_mismatch",
            )
        if str(getattr(trusted, "organization_id", "") or ""):
            raise ServiceError(
                "channel pairing requires a Personal context",
                status=403,
                code="channel_personal_required",
            )
        return binding

    def _pairing_store(self, agent_id: str):
        from ..integrations.hermes_pairings import get_channel_pairing_store

        return get_channel_pairing_store(agent_id, self._root_profile().name)

    def _delete_bot_history(self, profile: Path, chat_id: str | None = None) -> None:
        """Delete stored bot conversation files; log counts only, never raise."""
        try:
            removed = self.repository.delete_bot_history(profile, chat_id)
        except Exception as error:
            _LOGGER.warning(
                "bot history deletion failed: %s", type(error).__name__
            )
            return
        if removed:
            _LOGGER.info("deleted %d bot history file(s)", removed)

    async def _call_pairing_store(self, function, *args):
        from ..repositories.file_lock import FileLockTimeout

        try:
            return await asyncio.to_thread(function, *args)
        except ServiceError:
            raise
        except FileLockTimeout as error:
            _LOGGER.warning("channel pairing store lock timed out")
            raise ServiceError(
                "channel pairing store is busy",
                status=503,
                code="pairing_store_busy",
            ) from error
        except Exception as error:
            _LOGGER.warning("channel pairing store unavailable: %s", type(error).__name__)
            raise ServiceError(
                "channel pairing store is unavailable",
                status=503,
                code="pairing_store_unavailable",
            ) from error

    async def list_pairings(
        self, agent_id: str, platform: str, trusted: Any = None
    ) -> dict[str, Any]:
        """List sanitized pending/approved pairing state for one enabled channel."""
        self._require_feature()
        platform = self._pairing_platform(platform)
        profile = self._profile(agent_id)
        self._require_pairing_owner(agent_id, profile, trusted)
        self._require_enabled_channel(profile, platform)
        config = self.repository.config(profile)
        policy = self._pairing_policy(config, platform)
        store = self._pairing_store(agent_id)
        pending_raw, approved_raw = await asyncio.gather(
            self._call_pairing_store(store.pending, platform),
            self._call_pairing_store(store.approved, platform),
        )
        now = time.time()
        from ..integrations.hermes_pairings import PAIRING_CODE_TTL_SECONDS

        pending = []
        for item in pending_raw:
            if not isinstance(item, Mapping):
                continue
            request_id = str(item.get("request_id") or "").strip()
            user_id = str(item.get("user_id") or "").strip()
            if not request_id or not user_id:
                continue
            if not _PAIRING_REQUEST_ID.fullmatch(request_id):
                continue
            if not _SAFE_IDENTITY.fullmatch(user_id):
                continue
            try:
                age_minutes = max(0, int(item.get("age_minutes") or 0))
            except (TypeError, ValueError):
                continue
            requested_at = max(0.0, now - age_minutes * 60.0)
            pending.append(
                {
                    "request_id": request_id,
                    "user_id": user_id,
                    "user_name": self._pairing_label(item.get("user_name")),
                    "requested_at": requested_at,
                    "expires_at": requested_at + PAIRING_CODE_TTL_SECONDS,
                }
            )
        approved = []
        for item in approved_raw:
            if not isinstance(item, Mapping):
                continue
            user_id = str(item.get("user_id") or "").strip()
            if not user_id or not _SAFE_IDENTITY.fullmatch(user_id):
                continue
            approved_at = item.get("approved_at")
            if not isinstance(approved_at, (int, float)) or isinstance(approved_at, bool):
                approved_at = None
            approved.append(
                {
                    "user_id": user_id,
                    "user_name": self._pairing_label(item.get("user_name")),
                    "approved_at": approved_at,
                }
            )
        pending.sort(key=lambda item: item["requested_at"])
        approved.sort(key=lambda item: item.get("approved_at") or 0.0, reverse=True)
        return {
            "platform": platform,
            "dm_policy": policy,
            "pending": pending,
            "approved": approved,
        }

    async def approve_pairing(
        self, agent_id: str, platform: str, request_id: str, trusted: Any = None
    ) -> dict[str, Any]:
        self._require_feature()
        platform = self._pairing_platform(platform)
        profile = self._profile(agent_id)
        self._require_pairing_owner(agent_id, profile, trusted)
        self._require_enabled_channel(profile, platform)
        self._require_pairing_policy(profile, platform)
        store = self._pairing_store(agent_id)
        result = await self._call_pairing_store(store.approve, platform, str(request_id))
        if not isinstance(result, Mapping):
            raise ServiceError(
                "pairing request is missing, expired, or already consumed",
                status=404,
                code="pairing_request_not_found",
            )
        return {
            "status": "approved",
            "user_id": str(result.get("user_id") or "").strip()[:200],
            "user_name": self._pairing_label(result.get("user_name")),
        }

    async def deny_pairing(
        self, agent_id: str, platform: str, request_id: str, trusted: Any = None
    ) -> dict[str, Any]:
        self._require_feature()
        platform = self._pairing_platform(platform)
        profile = self._profile(agent_id)
        self._require_pairing_owner(agent_id, profile, trusted)
        self._require_enabled_channel(profile, platform)
        self._require_pairing_policy(profile, platform)
        store = self._pairing_store(agent_id)
        status = await self._call_pairing_store(store.deny, platform, str(request_id))
        if status == "denied":
            return {"status": "denied", "request_id": str(request_id)}
        if status == "expired":
            raise ServiceError(
                "pairing request expired",
                status=404,
                code="pairing_request_expired",
            )
        raise ServiceError(
            "pairing request is missing or already consumed",
            status=404,
            code="pairing_request_not_found",
        )

    async def revoke_pairing(
        self, agent_id: str, platform: str, user_id: str, trusted: Any = None
    ) -> dict[str, Any]:
        self._require_feature()
        platform = self._pairing_platform(platform)
        user_id = str(user_id or "").strip()
        pattern = (
            _ZALO_ID
            if platform == "zalo"
            else _BOT_USER_ID_PATTERNS.get(platform, _ZALO_BOT_ID)
        )
        if not pattern.fullmatch(user_id):
            raise ServiceError("channel user ID is invalid", status=422, code="invalid_user_id")
        profile = self._profile(agent_id)
        self._require_pairing_owner(agent_id, profile, trusted)
        self._require_enabled_channel(profile, platform)
        store = self._pairing_store(agent_id)
        revoked = await self._call_pairing_store(store.revoke, platform, user_id)
        if platform == "zalo_bot":
            # A revoked DM sender keeps no stored conversation.
            self._delete_bot_history(profile, user_id)
        return {
            "status": "revoked" if revoked else "not_approved",
            "user_id": user_id,
        }

    @staticmethod
    def _group_platform(value: Any) -> str:
        entry = get_platform(value)
        if entry is None:
            raise ServiceError("unsupported channel platform", status=422, code="invalid_platform")
        return entry.id

    @staticmethod
    def _group_id(value: Any, platform: str = "zalo_bot") -> str:
        group_id = str(value or "").strip()
        if not matches_id_pattern(platform, group_id):
            raise ServiceError(
                "channel group ID is invalid", status=422, code="invalid_group_id"
            )
        return group_id

    @staticmethod
    def _group_name(value: Any) -> str:
        return " ".join(str(value or "").split())[:100]

    @staticmethod
    def _group_daily_limit(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ServiceError(
                "channel group daily limit is invalid",
                status=422,
                code="invalid_daily_limit",
            )
        if not 0 <= value <= MAX_GROUP_DAILY_LIMIT:
            raise ServiceError(
                "channel group daily limit is invalid",
                status=422,
                code="invalid_daily_limit",
            )
        return value

    @staticmethod
    def _group_day(timestamp: float | None = None) -> str:
        """Return the Asia/Ho_Chi_Minh calendar day for one epoch instant."""
        moment = datetime.fromtimestamp(
            float(timestamp if timestamp is not None else time.time()),
            tz=timezone.utc,
        )
        return moment.astimezone(_GROUP_TIMEZONE).date().isoformat()

    def _group_state(self, profile: Path, platform: str) -> tuple[dict[str, Any], dict[str, Any]]:
        state = self.repository.groups(profile)
        record = state["platforms"].get(platform)
        if not isinstance(record, dict):
            record = {"daily_limit": DEFAULT_GROUP_DAILY_LIMIT, "groups": {}}
            state["platforms"][platform] = record
        return state, record

    def _record_group_seen(
        self,
        profile: Path,
        platform: str,
        group_id: str,
        group_name: str = "",
        sender_name: str = "",
    ) -> str:
        """Record one observed group; never grow pending beyond the cap."""
        with _GROUP_LOCK:
            state, record = self._group_state(profile, platform)
            groups = record["groups"]
            existing = groups.get(group_id)
            timestamp = time.time()
            if existing is None:
                pending = sum(
                    1 for item in groups.values() if item.get("status") == "pending"
                )
                if pending >= MAX_PENDING_GROUP_REQUESTS:
                    return "ignored"
                groups[group_id] = {
                    "status": "pending",
                    "name": group_name,
                    "last_sender_name": sender_name,
                    "first_seen": timestamp,
                    "last_seen": timestamp,
                    "approved_at": None,
                }
                status = "pending"
            else:
                status = str(existing.get("status") or "pending")
                if group_name:
                    existing["name"] = group_name
                if sender_name:
                    existing["last_sender_name"] = sender_name
                existing["last_seen"] = timestamp
            self.repository.write_groups(profile, state)
        return status if status in {"pending", "approved"} else "pending"

    def _group_is_approved(self, profile: Path, platform: str, group_id: str) -> bool:
        _, record = self._group_state(profile, platform)
        entry = record["groups"].get(group_id)
        return bool(entry and entry.get("status") == "approved")

    def _group_limit(self, profile: Path, platform: str) -> int:
        _, record = self._group_state(profile, platform)
        return int(record.get("daily_limit", DEFAULT_GROUP_DAILY_LIMIT))

    async def _consume_group_turn(
        self,
        profile: Path,
        platform: str,
        group_id: str,
        sender_id: str,
        message_id: str,
    ) -> None:
        """Count one accepted group turn, or refuse it at the daily limit.

        Every sender counts, including DM-paired ones. A retried provider
        message ID is counted once, so a duplicate delivery neither increments
        nor hits the limit.
        """
        limit = self._group_limit(profile, platform)
        if limit == 0:
            return
        day = self._group_day()
        with _GROUP_LOCK:
            state = self.repository.group_usage(profile)
            record = state["platforms"].get(platform)
            if not isinstance(record, dict) or str(record.get("date") or "") != day:
                record = {"date": day, "counts": {}, "message_ids": {}}
                state["platforms"][platform] = record
            counts = record["counts"].setdefault(group_id, {})
            sender_key = f"{group_id}:{sender_id}"
            counted = record["message_ids"].setdefault(sender_key, [])
            if message_id in counted:
                return
            current = int(counts.get(sender_id) or 0)
            if current >= limit:
                raise ServiceError(
                    f"daily group limit of {limit} reached",
                    status=429,
                    code="channel_group_limit_reached",
                )
            counts[sender_id] = current + 1
            counted.append(message_id)
            record["message_ids"][sender_key] = counted
            self.repository.write_group_usage(profile, state)

    def record_group_seen(self, body: Mapping[str, Any], token: str | None) -> dict[str, Any]:
        """Record one unapproved group mention forwarded by the gateway."""
        self._authorize(token)
        self._require_feature()
        platform = self._group_platform(body.get("platform"))
        agent_id = str(body.get("agent_id") or "")
        if not _SAFE_IDENTITY.fullmatch(agent_id):
            raise ServiceError("channel turn identifiers are invalid", status=422)
        group_id = self._group_id(body.get("group_id"), platform)
        profile = self._profile(agent_id)
        status = self._record_group_seen(
            profile,
            platform,
            group_id,
            self._group_name(body.get("group_name")),
            self._group_name(body.get("sender_name")),
        )
        return {"group_id": group_id, "status": status}

    async def list_groups(
        self, agent_id: str, platform: str, trusted: Any = None
    ) -> dict[str, Any]:
        """List sanitized pending/approved zalo_bot group state."""
        self._require_feature()
        platform = self._group_platform(platform)
        profile = self._profile(agent_id)
        self._require_pairing_owner(agent_id, profile, trusted)
        self._require_enabled_channel(profile, platform)
        _, record = self._group_state(profile, platform)
        pending: list[dict[str, Any]] = []
        approved: list[dict[str, Any]] = []
        for group_id, entry in record["groups"].items():
            if not matches_id_pattern(platform, group_id):
                continue
            status = str(entry.get("status") or "")
            if status == "pending":
                pending.append(
                    {
                        "group_id": str(group_id),
                        "group_name": self._group_name(entry.get("name")),
                        "last_sender_name": self._group_name(entry.get("last_sender_name")),
                        "first_seen": float(entry.get("first_seen") or 0),
                        "last_seen": float(entry.get("last_seen") or 0),
                    }
                )
            elif status == "approved":
                approved.append(
                    {
                        "group_id": str(group_id),
                        "group_name": self._group_name(entry.get("name")),
                        "last_sender_name": self._group_name(entry.get("last_sender_name")),
                        "first_seen": float(entry.get("first_seen") or 0),
                        "last_seen": float(entry.get("last_seen") or 0),
                        "approved_at": float(entry.get("approved_at") or 0) or None,
                    }
                )
        pending.sort(key=lambda item: item["last_seen"], reverse=True)
        approved.sort(key=lambda item: item.get("approved_at") or 0, reverse=True)
        return {
            "platform": platform,
            "daily_limit": self._group_limit(profile, platform),
            "pending": pending,
            "approved": approved,
        }

    async def approve_group(
        self, agent_id: str, platform: str, group_id: str, trusted: Any = None
    ) -> dict[str, Any]:
        self._require_feature()
        platform = self._group_platform(platform)
        group_id = self._group_id(group_id, platform)
        profile = self._profile(agent_id)
        self._require_pairing_owner(agent_id, profile, trusted)
        self._require_enabled_channel(profile, platform)
        with _GROUP_LOCK:
            state, record = self._group_state(profile, platform)
            entry = record["groups"].get(group_id)
            if not entry or entry.get("status") != "pending":
                raise ServiceError(
                    "channel group request is missing",
                    status=404,
                    code="channel_group_not_found",
                )
            entry["status"] = "approved"
            entry["approved_at"] = time.time()
            self.repository.write_groups(profile, state)
        return {
            "status": "approved",
            "group_id": group_id,
            "group_name": self._group_name(entry.get("name")),
        }

    async def deny_group(
        self, agent_id: str, platform: str, group_id: str, trusted: Any = None
    ) -> dict[str, Any]:
        self._require_feature()
        platform = self._group_platform(platform)
        group_id = self._group_id(group_id, platform)
        profile = self._profile(agent_id)
        self._require_pairing_owner(agent_id, profile, trusted)
        self._require_enabled_channel(profile, platform)
        with _GROUP_LOCK:
            state, record = self._group_state(profile, platform)
            entry = record["groups"].get(group_id)
            if not entry or entry.get("status") != "pending":
                raise ServiceError(
                    "channel group request is missing",
                    status=404,
                    code="channel_group_not_found",
                )
            del record["groups"][group_id]
            self.repository.write_groups(profile, state)
        if platform == "zalo_bot":
            # Denying an unapproved group drops any partial stored history.
            self._delete_bot_history(profile, group_id)
        return {"status": "denied", "group_id": group_id}

    async def revoke_group(
        self, agent_id: str, platform: str, group_id: str, trusted: Any = None
    ) -> dict[str, Any]:
        self._require_feature()
        platform = self._group_platform(platform)
        group_id = self._group_id(group_id, platform)
        profile = self._profile(agent_id)
        self._require_pairing_owner(agent_id, profile, trusted)
        self._require_enabled_channel(profile, platform)
        revoked = False
        with _GROUP_LOCK:
            state, record = self._group_state(profile, platform)
            entry = record["groups"].get(group_id)
            if entry and entry.get("status") == "approved":
                del record["groups"][group_id]
                self.repository.write_groups(profile, state)
                revoked = True
        if revoked and platform == "zalo_bot":
            # Revoking a group removes its stored conversation.
            self._delete_bot_history(profile, group_id)
        return {
            "status": "revoked" if revoked else "not_approved",
            "group_id": group_id,
        }

    async def update_group_settings(
        self,
        agent_id: str,
        platform: str,
        daily_limit: Any,
        trusted: Any = None,
    ) -> dict[str, Any]:
        self._require_feature()
        platform = self._group_platform(platform)
        limit = self._group_daily_limit(daily_limit)
        profile = self._profile(agent_id)
        self._require_pairing_owner(agent_id, profile, trusted)
        self._require_enabled_channel(profile, platform)
        with _GROUP_LOCK:
            state, record = self._group_state(profile, platform)
            record["daily_limit"] = limit
            self.repository.write_groups(profile, state)
        return {"daily_limit": limit}

    @staticmethod
    def _platform_config(config: Mapping[str, Any], platform: str) -> dict[str, Any]:
        platforms = config.get("platforms")
        if not isinstance(platforms, dict):
            return {}
        value = platforms.get(platform)
        return value if isinstance(value, dict) else {}

    @classmethod
    def _platform_extra(cls, config: Mapping[str, Any], platform: str) -> dict[str, Any]:
        extra = cls._platform_config(config, platform).get("extra")
        return extra if isinstance(extra, dict) else {}

    @classmethod
    def _zalo_config(cls, config: Mapping[str, Any]) -> dict[str, Any]:
        return cls._platform_config(config, "zalo")

    @classmethod
    def _zalo_extra(cls, config: Mapping[str, Any]) -> dict[str, Any]:
        return cls._platform_extra(config, "zalo")

    @classmethod
    def _dm_policy(cls, config: Mapping[str, Any]) -> str:
        value = str(cls._zalo_extra(config).get("dm_policy") or "").strip().lower()
        return value if value in _DM_POLICIES else "disabled"

    @classmethod
    def _bot_dm_policy(cls, config: Mapping[str, Any], platform: str = "zalo_bot") -> str:
        value = (
            str(cls._platform_extra(config, platform).get("dm_policy") or "")
            .strip()
            .lower()
        )
        return value if value in _DM_POLICIES else "disabled"

    @staticmethod
    def _retention_days_value(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ServiceError(
                "history_retention_days must be an integer",
                status=422,
                code="invalid_history_retention_days",
            )
        if not MIN_RETENTION_DAYS <= value <= MAX_RETENTION_DAYS:
            raise ServiceError(
                "history_retention_days must be between 1 and 365",
                status=422,
                code="invalid_history_retention_days",
            )
        return value

    @classmethod
    def _retention_days_from_config(cls, config: Mapping[str, Any], platform: str) -> int:
        value = cls._platform_extra(config, platform).get("history_retention_days")
        if isinstance(value, bool):
            return DEFAULT_RETENTION_DAYS
        try:
            days = int(value)
        except (TypeError, ValueError):
            return DEFAULT_RETENTION_DAYS
        if not MIN_RETENTION_DAYS <= days <= MAX_RETENTION_DAYS:
            return DEFAULT_RETENTION_DAYS
        return days

    def history_retention_days(self, profile: Path, platform: str) -> int:
        platform = str(platform or "").strip().lower()
        return self._retention_days_from_config(self.repository.config(profile), platform)

    @staticmethod
    def _normalize_ids(values: Any) -> list[str]:
        if not isinstance(values, (list, tuple)):
            raise ServiceError(
                "allowed_users must be a list of Zalo user IDs",
                status=422,
                code="invalid_allowed_users",
            )
        result: list[str] = []
        for value in values:
            item = str(value or "").strip()
            if not _ZALO_ID.fullmatch(item):
                raise ServiceError(
                    "allowed_users must be Zalo user IDs",
                    status=422,
                    code="invalid_allowed_users",
                )
            if item not in result:
                result.append(item)
        return result

    @staticmethod
    def _normalize_channel_ids(values: Any, platform: str) -> list[str]:
        entry = get_platform(platform)
        label = entry.label if entry is not None else "channel"
        if not isinstance(values, (list, tuple)):
            raise ServiceError(
                f"allowed_users must be a list of {label} user IDs",
                status=422,
                code="invalid_allowed_users",
            )
        pattern = _BOT_USER_ID_PATTERNS.get(platform, _ZALO_BOT_ID)
        result: list[str] = []
        for value in values:
            item = str(value or "").strip()
            if not pattern.fullmatch(item):
                raise ServiceError(
                    f"allowed_users must be {label} user IDs",
                    status=422,
                    code="invalid_allowed_users",
                )
            if item not in result:
                result.append(item)
        return result

    def _read_bot_token(
        self, profile: Path, platform: str = "zalo_bot", env: str | None = None
    ) -> str | None:
        """Temporary profile-local token storage; a Control proxy replaces it."""
        key = env or self._bot_token_env(platform)
        value = str(self.repository.env(profile).get(key) or "").strip()
        return value or None

    def _bot_token_in_use(
        self, token: str, profile: Path, platform: str = "zalo_bot"
    ) -> bool:
        """Return whether another profile in this workspace polls this token."""
        root = self._root_profile()
        target = Path(profile).resolve()
        candidates: list[Path] = [] if root == target else [root]
        profiles_root = Path(self.platform.repository.profiles_root)
        if profiles_root.is_dir():
            for entry in sorted(profiles_root.iterdir(), key=lambda item: item.name):
                if not entry.is_dir() or entry.is_symlink():
                    continue
                if entry.name in {root.name, "default"}:
                    continue
                try:
                    resolved = entry.resolve()
                except Exception:
                    continue
                if resolved == root or resolved == target:
                    continue
                candidates.append(entry)
        for candidate in candidates:
            for _, env, _ in self._bot_tokens(platform):
                try:
                    stored = self._read_bot_token(candidate, platform, env)
                except Exception:
                    continue
                if stored and hmac.compare_digest(stored, token):
                    return True
        return False

    def _clear_bot_tokens(self, profile: Path, platform: str = "zalo_bot") -> None:
        """Remove every write-only secret a bot platform stores in the profile."""
        self.repository.update_env(
            profile, {env: None for _, env, _ in self._bot_tokens(platform)}
        )

    def _snapshot_config(self, agent_id: str, profile: Path) -> None:
        path = profile / "config.yaml"
        if path.is_symlink():
            raise ServiceError(
                "agent channel config is unsafe", status=400, code="channel_path_unsafe"
            )
        if not path.is_file():
            return
        self.platform._snapshot_agent(agent_id, "config", "config", path.read_bytes())

    def _bridge_port(self, profile: Path, config: Mapping[str, Any]) -> int:
        configured = self._zalo_extra(config).get("bridge_port")
        return zalo_channels.bridge_port(
            profile,
            profiles_root=Path(self.platform.repository.profiles_root),
            root_profile=self._root_profile(),
            configured=configured,
        )

    @classmethod
    def _zalo_session_dir(cls, profile: Path, config: Mapping[str, Any]) -> Path:
        """Mirror the plugin's rule: ``extra.session_path`` or ``<profile>/zalo``."""
        configured = cls._zalo_extra(config).get("session_path")
        if configured:
            return Path(str(configured))
        return Path(profile) / "zalo"

    def _bridge_token(self, profile: Path, config: Mapping[str, Any]) -> str | None:
        return zalo_channels.bridge_token(self._zalo_session_dir(profile, config))

    def zalo_bridge_health(self, agent_id: str) -> dict[str, Any] | None:
        """Read the open bridge health endpoint for one profile."""
        profile = self._profile(agent_id)
        config = self.repository.config(profile)
        return zalo_channels.bridge_health(self._bridge_port(profile, config))

    def call_zalo_bridge(
        self,
        agent_id: str,
        path: str,
        *,
        method: str = "GET",
        body: Mapping[str, Any] | None = None,
        timeout: float = 2.0,
        max_bytes: int = 2 * 1024 * 1024,
    ) -> dict[str, Any]:
        """Call one authenticated personal-Zalo bridge endpoint for a profile.

        The token is re-read on every call. When it is missing no request is
        sent and the result mirrors an unauthorized bridge response.
        """
        profile = self._profile(agent_id)
        config = self.repository.config(profile)
        token = self._bridge_token(profile, config)
        if token is None:
            return {
                "ok": False,
                "status": 0,
                "data": None,
                "error": "unauthorized",
            }
        return zalo_channels.bridge_request(
            self._bridge_port(profile, config),
            path,
            method=method,
            token=token,
            body=body,
            timeout=timeout,
            max_bytes=max_bytes,
        )

    @staticmethod
    def _pid_alive(pid: int | None) -> bool:
        if not isinstance(pid, int) or pid <= 1:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def _gateway_state(self) -> dict[str, Any]:
        root = self._root_profile()
        pid = self.repository.gateway_pid(root)
        if pid is None:
            pid = self.repository.gateway_state_pid(root)
        running = self._pid_alive(pid)
        record = self.repository.gateway_restart()
        pending = bool(record and pid is not None and int(record["pid"]) == pid)
        if record and not pending:
            self.repository.clear_gateway_restart()
        return {"running": running, "pid": pid, "pending_restart": pending}

    def _restart_gateway(self) -> dict[str, Any]:
        gateway = self._gateway_state()
        pid = gateway.get("pid")
        if not gateway.get("running") or not isinstance(pid, int):
            return gateway
        self.repository.mark_gateway_restart(pid)
        try:
            os.kill(pid, signal.SIGUSR1)
        except OSError:
            self.repository.clear_gateway_restart()
            return self._gateway_state()
        return self._gateway_state()

    def _channel_state(
        self,
        profile: Path,
        config: Mapping[str, Any],
        gateway: Mapping[str, Any],
    ) -> dict[str, Any]:
        enabled = bool(self._zalo_config(config).get("enabled"))
        env = self.repository.env(profile)
        allowed_users = [
            item.strip()
            for item in str(env.get("ZALO_ALLOWED_USERS") or "").split(",")
            if item.strip()
        ]
        home_channel = str(env.get("ZALO_HOME_CHANNEL") or "").strip() or None
        own_id: str | None = None
        if not enabled:
            status = "disabled"
        elif gateway.get("pending_restart"):
            status = "pending_restart"
        elif not gateway.get("running"):
            status = "gateway_down"
        else:
            health = zalo_channels.bridge_health(self._bridge_port(profile, config))
            status = zalo_channels.bridge_status(health.get("status")) if health else "closed"
            if health:
                own_id = str(health.get("ownId") or "").strip() or None
        return {
            "platform": "zalo",
            "enabled": enabled,
            "status": status,
            "own_id": own_id,
            "allowed_users": allowed_users,
            "dm_policy": self._dm_policy(config),
            "home_channel": home_channel,
            "history_retention_days": self._retention_days_from_config(config, "zalo"),
            "token_set": False,
        }

    def _bot_runtime_connected(self, profile: Path, platform: str = "zalo_bot") -> bool:
        """Read the owning plugin's per-profile connected heartbeat.

        ``gateway_state.json`` keys platforms by name only, so it cannot say
        which profile owns the bot under multiplexing. The bundled plugin
        writes the profile-scoped state file instead; trust only a fresh
        ``connected`` record so a dead gateway (stale heartbeat) reports as
        configured rather than connected.
        """
        if platform != "zalo_bot":
            # shortcut: the gateway state is keyed by platform, not profile, so two
            # agents with their own bot on one platform share one status; add a
            # profile-scoped state when multiplexed bots per platform are supported.
            state = self.repository.gateway_platform_state(self._root_profile(), platform)
            return state == "connected"
        record = self.repository.zalo_bot_state(profile)
        if not record or record.get("state") != "connected":
            return False
        updated_at = record.get("updated_at")
        if not isinstance(updated_at, (int, float)) or isinstance(updated_at, bool):
            return False
        return abs(time.time() - float(updated_at)) <= _BOT_STATE_STALE_SECONDS

    def _bot_channel_state(
        self,
        profile: Path,
        config: Mapping[str, Any],
        gateway: Mapping[str, Any],
        platform: str = "zalo_bot",
    ) -> dict[str, Any]:
        enabled = bool(self._platform_config(config, platform).get("enabled"))
        env = self.repository.env(profile)
        prefix = self._bot_env_prefix(platform)
        token_flags = {
            field: self._read_bot_token(profile, platform, token_env) is not None
            for field, token_env, _ in self._bot_tokens(platform)
        }
        # ``token_set`` stays the primary bot token; Slack's second write-only
        # secret has its own flag and a channel needs every one of them.
        token_set = bool(token_flags.get("bot_token"))
        app_token_set = bool(token_flags.get("app_token"))
        all_tokens_set = all(token_flags.values())
        allowed_users = [
            item.strip()
            for item in str(env.get(f"{prefix}_ALLOWED_USERS") or "").split(",")
            if item.strip()
        ]
        home_channel = str(env.get(f"{prefix}_HOME_CHANNEL") or "").strip() or None
        if not enabled:
            status = "disabled"
        elif not all_tokens_set:
            status = "needs_token"
        elif gateway.get("pending_restart"):
            status = "pending_restart"
        elif not gateway.get("running"):
            status = "gateway_down"
        elif self._bot_runtime_connected(profile, platform):
            status = "connected"
        else:
            status = "configured"
        return {
            "platform": platform,
            "enabled": enabled,
            "status": status,
            "own_id": None,
            "allowed_users": allowed_users,
            "dm_policy": self._bot_dm_policy(config, platform),
            "home_channel": home_channel,
            "history_retention_days": (
                self._retention_days_from_config(config, platform)
                if platform == "zalo_bot"
                else None
            ),
            "token_set": token_set,
            "app_token_set": app_token_set,
            "plugin_artifact": (
                builtin_platform_metadata(platform) if platform == "zalo_bot" else None
            ),
        }

    @staticmethod
    def _whatsapp_session_dir(profile: Path, config: Mapping[str, Any]) -> Path:
        return whatsapp_channels.session_directory(profile, config)

    def _whatsapp_pairing_key(self, profile: Path) -> str:
        return str(Path(profile).resolve())

    def _whatsapp_pairing_record(self, profile: Path) -> _WhatsAppPairing | None:
        with self._whatsapp_pairings_lock:
            return self._whatsapp_pairings.get(self._whatsapp_pairing_key(profile))

    def _whatsapp_channel_state(
        self,
        profile: Path,
        config: Mapping[str, Any],
        gateway: Mapping[str, Any],
    ) -> dict[str, Any]:
        enabled = bool(self._platform_config(config, "whatsapp").get("enabled"))
        env = self.repository.env(profile)
        allowed_users = [
            item.strip()
            for item in str(env.get("WHATSAPP_ALLOWED_USERS") or "").split(",")
            if item.strip()
        ]
        home_channel = str(env.get("WHATSAPP_HOME_CHANNEL") or "").strip() or None
        session = self._whatsapp_session_dir(profile, config)
        if not enabled:
            status = "disabled"
        elif gateway.get("pending_restart"):
            status = "pending_restart"
        elif not gateway.get("running"):
            status = "gateway_down"
        elif self._bot_runtime_connected(profile, "whatsapp"):
            status = "connected"
        else:
            record = self._whatsapp_pairing_record(profile)
            if record is not None and record.status in {"starting", "qr"}:
                status = "qr"
            elif not whatsapp_channels.has_credentials(session):
                status = "needs_login"
            else:
                state = self.repository.gateway_platform_state(
                    self._root_profile(), "whatsapp"
                )
                status = "starting" if state in {"connecting", "starting"} else "closed"
        return {
            "platform": "whatsapp",
            "enabled": enabled,
            "status": status,
            "own_id": None,
            "allowed_users": allowed_users,
            "dm_policy": self._bot_dm_policy(config, "whatsapp"),
            "home_channel": home_channel,
            "history_retention_days": None,
            "token_set": False,
            "app_token_set": False,
        }

    def list_channels(self, agent_id: str) -> dict[str, Any]:
        self._require_feature()
        profile = self._profile(agent_id)
        config = self.repository.config(profile)
        gateway = self._gateway_state()
        states = [self._channel_state(profile, config, gateway)]
        for platform in platform_ids():
            if platform == "zalo":
                continue
            if platform == "whatsapp":
                states.append(self._whatsapp_channel_state(profile, config, gateway))
            else:
                states.append(self._bot_channel_state(profile, config, gateway, platform))
        return {
            "channels": states,
            "gateway": gateway,
            "social_tools": self.repository.social_tools(profile),
        }

    def set_social_tools(
        self, agent_id: str, enabled: bool, trusted: Any = None
    ) -> dict[str, Any]:
        """Persist the per-agent switch for tools that act on social channels."""
        self._require_feature()
        profile = self._profile(agent_id)
        binding = self._check_channel_owner(agent_id, profile, trusted)
        if binding is None:
            raise ServiceError(
                "verified channel owner is required",
                status=401,
                code="trusted_context_required",
            )
        self.repository.write_social_tools(profile, enabled)
        self._write_channel_owner(profile, binding)
        return self.list_channels(agent_id)

    def _delete_owner_when_no_channel_enabled(
        self, profile: Path, config: Mapping[str, Any] | None = None
    ) -> bool:
        """Keep the one shared owner until every channel is disabled."""
        current = config if config is not None else self.repository.config(profile)
        enabled = any(
            bool(self._platform_config(current, platform).get("enabled"))
            for platform in platform_ids()
        )
        if enabled:
            return False
        return self.repository.delete_owner(profile)

    @staticmethod
    def _has_enabled_channel(config: Mapping[str, Any]) -> bool:
        return any(
            bool(ChannelsService._platform_config(config, platform).get("enabled"))
            for platform in platform_ids()
        )

    @staticmethod
    def _packaged_social_channels_skill_files() -> dict[str, bytes]:
        root = _SOCIAL_CHANNELS_SKILL_ROOT
        if not root.is_dir():
            return {}
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file() and not path.is_symlink()
        }

    def _install_social_channels_skill(
        self, profile: Path, packaged: Mapping[str, bytes]
    ) -> None:
        skill_dir = (
            profile / "skills" / SOCIAL_CHANNELS_SKILL_CATEGORY / SOCIAL_CHANNELS_SKILL_ID
        )
        current = profile
        for part in ("skills", SOCIAL_CHANNELS_SKILL_CATEGORY, SOCIAL_CHANNELS_SKILL_ID):
            current = current / part
            if current.is_symlink():
                raise OSError("social channels skill path is a symlink")

        changed = False
        for relative, payload in packaged.items():
            try:
                installed = (skill_dir / relative).read_bytes()
            except OSError:
                changed = True
                break
            if installed != payload:
                changed = True
                break
        if changed:
            for relative, payload in packaged.items():
                self.repository.base.atomic_write(skill_dir / relative, payload)

        agents = self.platform.agents
        if SOCIAL_CHANNELS_SKILL_ID in agents._disabled_skills(
            agents._read_config(profile)
        ):
            agents._set_skill_enabled(profile, SOCIAL_CHANNELS_SKILL_ID, True)

    def sync_social_channels_skill(self) -> None:
        """Install and enable the shared skill in every profile with a channel."""
        try:
            packaged = self._packaged_social_channels_skill_files()
        except OSError:
            _LOGGER.warning("Social channels skill package could not be read")
            return
        if not packaged:
            return
        profiles = [self._root_profile()]
        profiles_root = Path(self.platform.repository.profiles_root)
        if profiles_root.is_dir():
            profiles.extend(
                entry
                for entry in sorted(profiles_root.iterdir(), key=lambda item: item.name)
                if entry.is_dir() and not entry.is_symlink()
            )
        seen: set[Path] = set()
        for profile in profiles:
            try:
                resolved = profile.resolve(strict=True)
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                config = self.repository.config(resolved)
            except Exception:
                continue
            if not self._has_enabled_channel(config):
                continue
            try:
                self._install_social_channels_skill(resolved, packaged)
            except Exception:
                _LOGGER.warning("Social channels skill sync failed")

    def _sync_root_multiplex(self) -> None:
        self.sync_social_channels_skill()
        root = self._root_profile()
        root_name = root.name
        enabled_profiles: list[str] = []
        profiles_root = Path(self.platform.repository.profiles_root)
        if profiles_root.is_dir():
            for entry in sorted(profiles_root.iterdir(), key=lambda item: item.name):
                if not entry.is_dir() or entry.is_symlink():
                    continue
                if entry.name in {root_name, "default"}:
                    continue
                try:
                    if entry.resolve() == root:
                        continue
                    config = self.repository.config(entry)
                except Exception:
                    continue
                if any(
                    bool(self._platform_config(config, platform).get("enabled"))
                    for platform in platform_ids()
                ):
                    enabled_profiles.append(entry.name)
        enabled_profiles = sorted(set(enabled_profiles))
        root_config = self.repository.config(root)
        gateway = root_config.get("gateway")
        if not isinstance(gateway, dict):
            gateway = {}
            root_config["gateway"] = gateway
        desired = {
            "multiplex_profiles": bool(enabled_profiles),
            "multiplex_profile_allowlist": enabled_profiles,
        }
        if all(gateway.get(key) == value for key, value in desired.items()):
            return
        self._snapshot_config(BIG_BROTHER_AGENT_ID, root)
        gateway.update(desired)
        self.repository.write_config(root, root_config)

    def set_channel_enabled(
        self, agent_id: str, enabled: bool, trusted: Any = None
    ) -> dict[str, Any]:
        self._require_feature()
        profile = self._profile(agent_id)
        binding = self._check_channel_owner(agent_id, profile, trusted) if enabled else None
        config = self.repository.config(profile)
        self._snapshot_config(agent_id, profile)
        platforms = config.get("platforms")
        if not isinstance(platforms, dict):
            platforms = {}
            config["platforms"] = platforms
        zalo = platforms.get("zalo")
        if not isinstance(zalo, dict):
            zalo = {}
            platforms["zalo"] = zalo
        zalo["enabled"] = bool(enabled)
        if enabled:
            extra = zalo.get("extra")
            if not isinstance(extra, dict):
                extra = {}
                zalo["extra"] = extra
            extra.setdefault("dm_policy", "disabled")
        self.repository.write_config(profile, config)
        if enabled:
            self._write_channel_owner(profile, binding)
        else:
            self._delete_owner_when_no_channel_enabled(profile, config)
        self._sync_root_multiplex()
        gateway = self._restart_gateway()
        return self._channel_state(profile, config, gateway)

    def patch_channel(
        self, agent_id: str, body: Mapping[str, Any], trusted: Any = None
    ) -> dict[str, Any]:
        self._require_feature()
        profile = self._profile(agent_id)
        binding = self._check_channel_owner(agent_id, profile, trusted)
        config = self.repository.config(profile)
        env_updates: dict[str, str | None] = {}
        config_changed = False
        if "allowed_users" in body:
            values = body.get("allowed_users")
            users = [] if values is None else self._normalize_ids(values)
            env_updates["ZALO_ALLOWED_USERS"] = ",".join(users) if users else None
        if "home_channel" in body:
            value = body.get("home_channel")
            if value is None:
                env_updates["ZALO_HOME_CHANNEL"] = None
            else:
                item = str(value).strip()
                if not _ZALO_ID.fullmatch(item):
                    raise ServiceError(
                        "home_channel must be a Zalo user ID",
                        status=422,
                        code="invalid_home_channel",
                    )
                env_updates["ZALO_HOME_CHANNEL"] = item
        if "dm_policy" in body:
            policy = str(body.get("dm_policy") or "").strip().lower()
            if policy not in _DM_POLICIES:
                raise ServiceError(
                    "dm_policy is invalid",
                    status=422,
                    code="invalid_dm_policy",
                )
            platforms = config.get("platforms")
            if not isinstance(platforms, dict):
                platforms = {}
                config["platforms"] = platforms
            zalo = platforms.get("zalo")
            if not isinstance(zalo, dict):
                zalo = {}
                platforms["zalo"] = zalo
            extra = zalo.get("extra")
            if not isinstance(extra, dict):
                extra = {}
                zalo["extra"] = extra
            extra["dm_policy"] = policy
            config_changed = True
        if "history_retention_days" in body:
            days = self._retention_days_value(body.get("history_retention_days"))
            zalo = self._ensure_platform_config(config, "zalo")
            extra = zalo.get("extra")
            if not isinstance(extra, dict):
                extra = {}
                zalo["extra"] = extra
            extra["history_retention_days"] = days
            config_changed = True
        if not config_changed and not env_updates:
            self._write_channel_owner(profile, binding)
            gateway = self._gateway_state()
            return self._channel_state(profile, config, gateway)
        self._snapshot_config(agent_id, profile)
        if config_changed:
            self.repository.write_config(profile, config)
        if env_updates:
            self.repository.update_env(profile, env_updates)
        self._write_channel_owner(profile, binding)
        gateway = self._restart_gateway()
        return self._channel_state(profile, config, gateway)

    async def channel_qr(self, agent_id: str) -> dict[str, Any]:
        self._require_feature()
        profile = self._profile(agent_id)
        config = self.repository.config(profile)
        if not bool(self._zalo_config(config).get("enabled")):
            raise ServiceError("channel is disabled", status=409, code="channel_disabled")
        gateway = self._gateway_state()
        if gateway.get("pending_restart"):
            return {"status": "pending_restart", "image_base64": None}
        if not gateway.get("running"):
            return {"status": "gateway_down", "image_base64": None}
        port = self._bridge_port(profile, config)
        health = await asyncio.to_thread(zalo_channels.bridge_health, port)
        status = zalo_channels.bridge_status(health.get("status")) if health else "closed"
        if status == "needs_login":
            await asyncio.to_thread(
                zalo_channels.bridge_start_login,
                port,
                token=self._bridge_token(profile, config),
            )
            deadline = time.monotonic() + _QR_WAIT_SECONDS
            while time.monotonic() < deadline:
                await asyncio.sleep(0.2)
                health = await asyncio.to_thread(zalo_channels.bridge_health, port)
                status = zalo_channels.bridge_status(health.get("status")) if health else "closed"
                if status in {"qr", "connected", "closed"}:
                    if status != "qr" or self.repository.qr_image(profile):
                        break
        if status != "qr":
            return {"status": status, "image_base64": None}
        image = self.repository.qr_image(profile)
        return {
            "status": "qr",
            "image_base64": (base64.b64encode(image).decode("ascii") if image else None),
        }

    def logout_channel(self, agent_id: str, trusted: Any = None) -> dict[str, Any]:
        self._require_feature()
        profile = self._profile(agent_id)
        self.repository.delete_session(profile)
        config = self.repository.config(profile)
        self._delete_owner_when_no_channel_enabled(profile, config)
        gateway = self._restart_gateway()
        return self._channel_state(profile, config, gateway)

    @staticmethod
    def _ensure_platform_config(config: dict[str, Any], platform: str) -> dict[str, Any]:
        platforms = config.get("platforms")
        if not isinstance(platforms, dict):
            platforms = {}
            config["platforms"] = platforms
        value = platforms.get(platform)
        if not isinstance(value, dict):
            value = {}
            platforms[platform] = value
        return value

    def set_zalo_bot_enabled(
        self, agent_id: str, enabled: bool, trusted: Any = None
    ) -> dict[str, Any]:
        return self._set_bot_enabled(agent_id, enabled, trusted, "zalo_bot")

    def set_telegram_enabled(
        self, agent_id: str, enabled: bool, trusted: Any = None
    ) -> dict[str, Any]:
        return self._set_bot_enabled(agent_id, enabled, trusted, "telegram")

    def set_discord_enabled(
        self, agent_id: str, enabled: bool, trusted: Any = None
    ) -> dict[str, Any]:
        return self._set_bot_enabled(agent_id, enabled, trusted, "discord")

    def set_slack_enabled(
        self, agent_id: str, enabled: bool, trusted: Any = None
    ) -> dict[str, Any]:
        return self._set_bot_enabled(agent_id, enabled, trusted, "slack")

    def _set_bot_enabled(
        self, agent_id: str, enabled: bool, trusted: Any, platform: str
    ) -> dict[str, Any]:
        self._require_feature()
        profile = self._profile(agent_id)
        binding = self._check_channel_owner(agent_id, profile, trusted) if enabled else None
        config = self.repository.config(profile)
        self._snapshot_config(agent_id, profile)
        bot = self._ensure_platform_config(config, platform)
        bot["enabled"] = bool(enabled)
        if enabled:
            extra = bot.get("extra")
            if not isinstance(extra, dict):
                extra = {}
                bot["extra"] = extra
            extra.setdefault("dm_policy", "disabled")
        self.repository.write_config(profile, config)
        if enabled:
            self._write_channel_owner(profile, binding)
        else:
            self._delete_owner_when_no_channel_enabled(profile, config)
        self._sync_root_multiplex()
        gateway = self._restart_gateway()
        return self._bot_channel_state(profile, config, gateway, platform)

    def patch_zalo_bot(
        self, agent_id: str, body: Mapping[str, Any], trusted: Any = None
    ) -> dict[str, Any]:
        return self._patch_bot(agent_id, body, trusted, "zalo_bot")

    def patch_telegram(
        self, agent_id: str, body: Mapping[str, Any], trusted: Any = None
    ) -> dict[str, Any]:
        return self._patch_bot(agent_id, body, trusted, "telegram")

    def patch_discord(
        self, agent_id: str, body: Mapping[str, Any], trusted: Any = None
    ) -> dict[str, Any]:
        return self._patch_bot(agent_id, body, trusted, "discord")

    def patch_slack(
        self, agent_id: str, body: Mapping[str, Any], trusted: Any = None
    ) -> dict[str, Any]:
        return self._patch_bot(agent_id, body, trusted, "slack")

    def _patch_bot(
        self,
        agent_id: str,
        body: Mapping[str, Any],
        trusted: Any,
        platform: str,
    ) -> dict[str, Any]:
        self._require_feature()
        profile = self._profile(agent_id)
        binding = self._check_channel_owner(agent_id, profile, trusted)
        config = self.repository.config(profile)
        env_updates: dict[str, str | None] = {}
        prefix = self._bot_env_prefix(platform)
        entry = get_platform(platform)
        label = entry.label if entry is not None else "channel"
        config_changed = False
        for field, token_env, pattern in self._bot_tokens(platform):
            if field not in body:
                continue
            value = body.get(field)
            if value is None:
                env_updates[token_env] = None
                continue
            token = str(value).strip()
            if not pattern.fullmatch(token):
                # Never include the submitted value in the error.
                raise ServiceError(
                    f"{field} is invalid",
                    status=422,
                    code="invalid_bot_token",
                )
            if self._bot_token_in_use(token, profile, platform):
                raise ServiceError(
                    f"{field} is already used by another agent",
                    status=409,
                    code="bot_token_in_use",
                )
            env_updates[token_env] = token
        if "allowed_users" in body:
            values = body.get("allowed_users")
            users = (
                []
                if values is None
                else self._normalize_channel_ids(values, platform)
            )
            env_updates[f"{prefix}_ALLOWED_USERS"] = ",".join(users) if users else None
        if "home_channel" in body:
            value = body.get("home_channel")
            if value is None:
                env_updates[f"{prefix}_HOME_CHANNEL"] = None
            else:
                item = str(value).strip()
                if not matches_id_pattern(platform, item):
                    raise ServiceError(
                        f"home_channel must be a {label} chat ID",
                        status=422,
                        code="invalid_home_channel",
                    )
                env_updates[f"{prefix}_HOME_CHANNEL"] = item
        if "dm_policy" in body:
            policy = str(body.get("dm_policy") or "").strip().lower()
            if policy not in _DM_POLICIES:
                raise ServiceError(
                    "dm_policy is invalid",
                    status=422,
                    code="invalid_dm_policy",
                )
            bot = self._ensure_platform_config(config, platform)
            extra = bot.get("extra")
            if not isinstance(extra, dict):
                extra = {}
                bot["extra"] = extra
            extra["dm_policy"] = policy
            config_changed = True
        if "history_retention_days" in body:
            days = self._retention_days_value(body.get("history_retention_days"))
            bot = self._ensure_platform_config(config, platform)
            extra = bot.get("extra")
            if not isinstance(extra, dict):
                extra = {}
                bot["extra"] = extra
            extra["history_retention_days"] = days
            config_changed = True
        if not config_changed and not env_updates:
            self._write_channel_owner(profile, binding)
            return self._bot_channel_state(
                profile, config, self._gateway_state(), platform
            )
        self._snapshot_config(agent_id, profile)
        if config_changed:
            self.repository.write_config(profile, config)
        if env_updates:
            self.repository.update_env(profile, env_updates)
        self._write_channel_owner(profile, binding)
        gateway = self._restart_gateway()
        return self._bot_channel_state(profile, config, gateway, platform)

    def logout_zalo_bot(self, agent_id: str, trusted: Any = None) -> dict[str, Any]:
        return self._logout_bot(agent_id, trusted, "zalo_bot")

    def logout_telegram(self, agent_id: str, trusted: Any = None) -> dict[str, Any]:
        return self._logout_bot(agent_id, trusted, "telegram")

    def logout_discord(self, agent_id: str, trusted: Any = None) -> dict[str, Any]:
        return self._logout_bot(agent_id, trusted, "discord")

    def logout_slack(self, agent_id: str, trusted: Any = None) -> dict[str, Any]:
        return self._logout_bot(agent_id, trusted, "slack")

    def _logout_bot(
        self, agent_id: str, trusted: Any, platform: str
    ) -> dict[str, Any]:
        self._require_feature()
        profile = self._profile(agent_id)
        config = self.repository.config(profile)
        self._snapshot_config(agent_id, profile)
        self._ensure_platform_config(config, platform)["enabled"] = False
        self.repository.write_config(profile, config)
        self._clear_bot_tokens(profile, platform)
        if platform == "zalo_bot":
            # Disconnecting the channel drops every stored conversation.
            self._delete_bot_history(profile)
        self._delete_owner_when_no_channel_enabled(profile, config)
        self._sync_root_multiplex()
        gateway = self._restart_gateway()
        return self._bot_channel_state(profile, config, gateway, platform)

    def set_whatsapp_enabled(
        self, agent_id: str, enabled: bool, trusted: Any = None
    ) -> dict[str, Any]:
        self._require_feature()
        profile = self._profile(agent_id)
        binding = self._check_channel_owner(agent_id, profile, trusted) if enabled else None
        config = self.repository.config(profile)
        self._snapshot_config(agent_id, profile)
        entry = self._ensure_platform_config(config, "whatsapp")
        entry["enabled"] = bool(enabled)
        if enabled:
            extra = entry.get("extra")
            if not isinstance(extra, dict):
                extra = {}
                entry["extra"] = extra
            extra.setdefault("dm_policy", "disabled")
        self.repository.write_config(profile, config)
        env_updates: dict[str, str | None] = {
            "WHATSAPP_ENABLED": "true" if enabled else "false"
        }
        if enabled:
            env_updates["WHATSAPP_MODE"] = "bot"
            env_updates["WHATSAPP_DM_POLICY"] = self._bot_dm_policy(config, "whatsapp")
        else:
            self._cancel_whatsapp_pairing(self._whatsapp_pairing_key(profile))
        self.repository.update_env(profile, env_updates)
        if enabled:
            self._write_channel_owner(profile, binding)
        else:
            self._delete_owner_when_no_channel_enabled(profile, config)
        self._sync_root_multiplex()
        gateway = self._restart_gateway()
        return self._whatsapp_channel_state(profile, config, gateway)

    def patch_whatsapp(
        self, agent_id: str, body: Mapping[str, Any], trusted: Any = None
    ) -> dict[str, Any]:
        self._require_feature()
        profile = self._profile(agent_id)
        binding = self._check_channel_owner(agent_id, profile, trusted)
        config = self.repository.config(profile)
        env_updates: dict[str, str | None] = {}
        config_changed = False
        if "allowed_users" in body:
            values = body.get("allowed_users")
            users = (
                []
                if values is None
                else self._normalize_channel_ids(values, "whatsapp")
            )
            env_updates["WHATSAPP_ALLOWED_USERS"] = ",".join(users) if users else None
        if "home_channel" in body:
            value = body.get("home_channel")
            if value is None:
                env_updates["WHATSAPP_HOME_CHANNEL"] = None
            else:
                item = str(value).strip()
                if not matches_id_pattern("whatsapp", item):
                    raise ServiceError(
                        "home_channel must be a WhatsApp chat ID",
                        status=422,
                        code="invalid_home_channel",
                    )
                env_updates["WHATSAPP_HOME_CHANNEL"] = item
        if "dm_policy" in body:
            policy = str(body.get("dm_policy") or "").strip().lower()
            if policy not in _DM_POLICIES:
                raise ServiceError(
                    "dm_policy is invalid",
                    status=422,
                    code="invalid_dm_policy",
                )
            entry = self._ensure_platform_config(config, "whatsapp")
            extra = entry.get("extra")
            if not isinstance(extra, dict):
                extra = {}
                entry["extra"] = extra
            extra["dm_policy"] = policy
            env_updates["WHATSAPP_DM_POLICY"] = policy
            config_changed = True
        if not config_changed and not env_updates:
            self._write_channel_owner(profile, binding)
            return self._whatsapp_channel_state(
                profile, config, self._gateway_state()
            )
        self._snapshot_config(agent_id, profile)
        if config_changed:
            self.repository.write_config(profile, config)
        if env_updates:
            self.repository.update_env(profile, env_updates)
        self._write_channel_owner(profile, binding)
        gateway = self._restart_gateway()
        return self._whatsapp_channel_state(profile, config, gateway)

    async def _watch_whatsapp_pairing(
        self, key: str, record: _WhatsAppPairing
    ) -> None:
        process = record.process
        try:

            async def consume() -> None:
                stream = getattr(process, "stdout", None)
                if stream is None:
                    return
                while True:
                    raw = await stream.readline()
                    if not raw:
                        return
                    if len(raw) > whatsapp_channels.MAX_QR_PAYLOAD_BYTES * 2:
                        continue
                    try:
                        payload = json.loads(raw.decode("utf-8", "replace").strip())
                    except (UnicodeDecodeError, ValueError):
                        continue
                    if not isinstance(payload, dict):
                        continue
                    event = str(payload.get("event") or "").strip()
                    if event == "qr":
                        qr = str(payload.get("qr") or "").strip()
                        if qr:
                            record.qr_png = await asyncio.to_thread(
                                whatsapp_channels.render_qr_png, qr
                            )
                            record.status = "qr"
                    elif event == "connected":
                        record.status = "connected"
                        return
                    elif event == "error":
                        record.error = str(payload.get("error") or "")[:200]
                        record.status = "error"
                        return

            await asyncio.wait_for(
                consume(), timeout=whatsapp_channels.PAIRING_TIMEOUT_SECONDS
            )
            if record.status in {"starting", "qr"}:
                record.status = "error"
                record.error = "pairing process exited"
        except (asyncio.TimeoutError, TimeoutError):
            record.status = "timeout"
        except asyncio.CancelledError:
            record.status = "cancelled"
            raise
        except Exception as error:
            record.status = "error"
            record.error = type(error).__name__
        finally:
            if record.status == "connected":
                try:
                    await asyncio.wait_for(process.wait(), timeout=3)
                except (asyncio.TimeoutError, TimeoutError):
                    await whatsapp_channels.terminate_pairing(process)
                if whatsapp_channels.has_credentials(record.session):
                    gateway = self._restart_gateway()
                    record.status = (
                        "pending_restart"
                        if gateway.get("pending_restart")
                        else "connected"
                    )
                else:
                    record.status = "error"
                    record.error = "pairing completed without credentials"
            else:
                await whatsapp_channels.terminate_pairing(process)
            with self._whatsapp_pairings_lock:
                if self._whatsapp_pairings.get(key) is record:
                    # Keep the terminal record for the current QR request and
                    # replace it on the next attempt.
                    record.task = None

    async def _start_whatsapp_pairing(
        self, key: str, session: Path
    ) -> _WhatsAppPairing | None:
        try:
            process = await whatsapp_channels.spawn_pairing(session, "bot")
        except Exception as error:
            _LOGGER.warning(
                "whatsapp pairing unavailable: %s", type(error).__name__
            )
            return None
        record = _WhatsAppPairing(process=process, session=session)
        with self._whatsapp_pairings_lock:
            existing = self._whatsapp_pairings.get(key)
            if existing is not None and existing.status in {"starting", "qr"}:
                duplicate = record
                record = existing
            else:
                duplicate = None
                self._whatsapp_pairings[key] = record
        if duplicate is not None:
            await whatsapp_channels.terminate_pairing(duplicate.process)
            return record
        record.task = asyncio.create_task(
            self._watch_whatsapp_pairing(key, record),
            name="whatsapp-pairing",
        )
        return record

    async def _stop_whatsapp_pairing(self, key: str) -> None:
        with self._whatsapp_pairings_lock:
            record = self._whatsapp_pairings.pop(key, None)
        if record is None:
            return
        if record.task is not None and not record.task.done():
            record.status = "cancelled"
            record.task.cancel()
            await whatsapp_channels.terminate_pairing(record.process)
            try:
                await record.task
            except (asyncio.CancelledError, Exception):
                pass
        else:
            await whatsapp_channels.terminate_pairing(record.process)

    def _cancel_whatsapp_pairing(self, key: str) -> None:
        """Synchronous best-effort stop used by the disable path."""
        with self._whatsapp_pairings_lock:
            record = self._whatsapp_pairings.pop(key, None)
        if record is None:
            return
        record.status = "cancelled"
        if record.task is not None and not record.task.done():
            record.task.cancel()
        if getattr(record.process, "returncode", None) is None:
            try:
                record.process.terminate()
            except (ProcessLookupError, OSError):
                pass

    async def channel_qr_whatsapp(self, agent_id: str) -> dict[str, Any]:
        self._require_feature()
        profile = self._profile(agent_id)
        config = self.repository.config(profile)
        if not bool(self._platform_config(config, "whatsapp").get("enabled")):
            raise ServiceError("channel is disabled", status=409, code="channel_disabled")
        gateway = self._gateway_state()
        if gateway.get("pending_restart"):
            return {"status": "pending_restart", "image_base64": None}
        if not gateway.get("running"):
            return {"status": "gateway_down", "image_base64": None}
        session = self._whatsapp_session_dir(profile, config)
        if whatsapp_channels.has_credentials(session):
            state = self.repository.gateway_platform_state(
                self._root_profile(), "whatsapp"
            )
            if state == "connected":
                status = "connected"
            elif state in {"connecting", "starting"}:
                status = "starting"
            else:
                status = "closed"
            return {"status": status, "image_base64": None}

        key = self._whatsapp_pairing_key(profile)
        with self._whatsapp_pairings_lock:
            record = self._whatsapp_pairings.get(key)
        if (
            record is None
            or record.status in {"error", "timeout", "cancelled"}
            or (record.task is None and record.status != "starting")
        ):
            await self._stop_whatsapp_pairing(key)
            record = await self._start_whatsapp_pairing(key, session)
            if record is None:
                return {"status": "needs_login", "image_base64": None}
        deadline = time.monotonic() + _QR_WAIT_SECONDS
        while time.monotonic() < deadline:
            if record.status in {"qr", "connected", "pending_restart", "error", "timeout"}:
                break
            if record.task is not None and record.task.done():
                break
            await asyncio.sleep(0.05)
        if record.status == "pending_restart":
            return {"status": "pending_restart", "image_base64": None}
        if record.status == "connected":
            return {"status": "connected", "image_base64": None}
        if record.status == "qr":
            return {
                "status": "qr",
                "image_base64": (
                    base64.b64encode(record.qr_png).decode("ascii")
                    if record.qr_png
                    else None
                ),
            }
        if record.status in {"error", "timeout", "cancelled"}:
            return {"status": "needs_login", "image_base64": None}
        # The bridge is still starting; keep the UI polling for the first QR.
        return {"status": "qr", "image_base64": None}

    async def logout_whatsapp(
        self, agent_id: str, trusted: Any = None
    ) -> dict[str, Any]:
        self._require_feature()
        profile = self._profile(agent_id)
        config = self.repository.config(profile)
        self._snapshot_config(agent_id, profile)
        await self._stop_whatsapp_pairing(self._whatsapp_pairing_key(profile))
        session = self._whatsapp_session_dir(profile, config)
        self.repository.delete_session_directory(profile, session)
        self._ensure_platform_config(config, "whatsapp")["enabled"] = False
        self.repository.write_config(profile, config)
        self.repository.update_env(profile, {"WHATSAPP_ENABLED": "false"})
        self._delete_owner_when_no_channel_enabled(profile, config)
        self._sync_root_multiplex()
        gateway = self._restart_gateway()
        return self._whatsapp_channel_state(profile, config, gateway)

    async def shutdown(self) -> None:
        """Stop every pairing subprocess owned by this service instance."""
        with self._whatsapp_pairings_lock:
            keys = list(self._whatsapp_pairings)
        for key in keys:
            await self._stop_whatsapp_pairing(key)
