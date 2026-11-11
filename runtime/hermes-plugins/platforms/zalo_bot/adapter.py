"""
Zalo Bot Platform Adapter for Hermes Agent.

A plugin-based gateway adapter that connects to Zalo via the Bot API
(bot.zaloplatforms.com) and relays messages between Zalo DMs and the
Hermes agent. Supports long-polling (default) and webhook mode.

Configuration in config.yaml:

    gateway:
      platforms:
        zalo_bot:
          enabled: true
          extra:
            bot_token: "12345678:abc-def-xyz"
            dm_policy: "pairing"    # disabled | allowlist | pairing
            allowed_users: []       # comma string or list; required for allowlist
            webhook_url: ""         # optional, overrides long-polling
            webhook_secret: ""      # required if webhook_url is set
            webhook_port: 8443      # port for webhook listener
            webhook_host: "127.0.0.1"  # bind address (use "0.0.0.0" for all interfaces)

Or via environment variables (overrides config.yaml):
    ZALO_BOT_TOKEN, ZALO_BOT_ALLOWED_USERS, ZALO_BOT_ALLOW_ALL_USERS,
    ZALO_BOT_DM_POLICY, ZALO_BOT_HOME_CHANNEL, ZALO_BOT_HOME_CHANNEL_NAME
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import re
import tempfile
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy imports — Hermes gateway dependencies
# ---------------------------------------------------------------------------

from gateway.platforms.base import (
    BasePlatformAdapter,
    SendResult,
    MessageEvent,
    MessageType,
    cache_image_from_url,
)
from gateway.session import SessionSource
from gateway.config import PlatformConfig, Platform

from hermes_constants import get_hermes_home

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ZALO_API_BASE = "https://bot-api.zaloplatforms.com/bot{}"
MAX_MESSAGE_LENGTH = 2000
POLLING_TIMEOUT = 30  # seconds for long-poll
POLLING_INTERVAL = 2  # seconds between polls when no updates
RECONNECT_DELAY = 5  # seconds before reconnecting after error
WEBHOOK_SECRET_HEADER = "X-Bot-Api-Secret-Token"
# Per-profile runtime state read by the Runtime channels service. It contains
# only the connection state, a heartbeat timestamp, and the writer PID.
STATE_FILE = "zalo_bot_state.json"
STATE_HEARTBEAT_SECONDS = 30.0

# ── Moderation constants ──────────────────────────────────────────
ANTI_SPAM_MAX_PER_MINUTE = 5
WARN_MAX_WARNINGS = 3
WARN_EXPIRY_SECONDS = 604800  # 1 week

# Per-profile plugin state directories. The environment overrides stay
# authoritative when set; otherwise the adapter resolves the directory below
# the profile home at connect time so multiplexed profiles never share files.
SETTINGS_DIR_ENV = "ZALO_BOT_SETTINGS_DIR"
SETTINGS_DIRNAME = "zalo-bot-settings"
CRM_DIR_ENV = "ZALO_BOT_CRM_DIR"
CRM_DIRNAME = "zalo-bot-crm"

# ── Conversation store constants ──────────────────────────────────
# The store lives below the profile home resolved at connect time (not at
# import time), so multiplexed profiles keep separate files. One JSONL file
# per chat: <profile home>/zalo_bot/history/<chat_id>.jsonl.
BOT_HISTORY_DIRNAME = "zalo_bot"
BOT_HISTORY_SUBDIR = "history"
BOT_CHANNELS_DIRNAME = "zalo"
BOT_GROUPS_FILE = "groups.json"
# Runtime rewrites the group approval file from time to time; claim nothing
# large when reading it.
BOT_GROUPS_MAX_BYTES = 1024 * 1024
HISTORY_TRIM_INTERVAL_SECONDS = 86400
HISTORY_MAX_BYTES = 50 * 1024 * 1024
HISTORY_TEXT_LIMIT = 4000
HISTORY_RETENTION_DEFAULT_DAYS = 30
HISTORY_RETENTION_MIN_DAYS = 1
HISTORY_RETENTION_MAX_DAYS = 365

# Chat/group IDs come from the wire; restrict them before they are used to
# build local file paths (defence against traversal / odd filenames).
_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_-]")

# ── Direct-message policy constants ───────────────────────────────
# Only these dm_policy values are supported. Anything else (including the
# legacy "open" value) is coerced to "disabled" so a typo can never open DMs.
DM_POLICIES = ("disabled", "allowlist", "pairing")
# Bounded, ordered dedupe for real provider update/message IDs. Polling and
# webhook delivery share this set.
MAX_SEEN_MESSAGE_IDS = 4096


def _safe_path_component(identifier: str) -> str:
    """Reduce a chat/group ID to [A-Za-z0-9_-] for local file names."""
    sanitized = _SAFE_ID_RE.sub("_", str(identifier))[:128]
    return sanitized or "unknown"


def _write_private_json(path: Path, value: Any) -> None:
    """Atomically write one JSON file readable only by its owner (0600)."""
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = ""
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _coerce_bool(value: Any, default: bool = False) -> bool:
    """Coerce config/env values to bool without treating "false" as true."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("1", "true", "yes", "on"):
            return True
        if normalized in ("0", "false", "no", "off", ""):
            return False
    return default


def _coerce_allowed_users(value: Any) -> set:
    """Normalize an allowlist from a comma string or an iterable of IDs."""
    if value is None:
        return set()
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, (list, tuple, set, frozenset)):
        items = value
    else:
        return set()
    return {str(item).strip() for item in items if str(item).strip()}


def _coerce_dm_policy(value: Any) -> str:
    """Return a supported dm_policy, failing closed to "disabled"."""
    normalized = str(value or "").strip().lower()
    return normalized if normalized in DM_POLICIES else "disabled"


def _coerce_retention_days(value: Any) -> int:
    """Clamp ``history_retention_days`` to 1..365; invalid -> 30."""
    if isinstance(value, bool):
        return HISTORY_RETENTION_DEFAULT_DAYS
    try:
        days = int(value)
    except (TypeError, ValueError):
        return HISTORY_RETENTION_DEFAULT_DAYS
    if days < HISTORY_RETENTION_MIN_DAYS:
        return HISTORY_RETENTION_MIN_DAYS
    if days > HISTORY_RETENTION_MAX_DAYS:
        return HISTORY_RETENTION_MAX_DAYS
    return days


def _redact_secret_text(text: Any, token: str) -> str:
    """Replace the bot token in any text destined for a log or an error."""
    rendered = str(text)
    if token:
        rendered = rendered.replace(token, "***")
    return rendered


def _redact_secret_data(value: Any, token: str) -> Any:
    """Recursively redact the bot token from API response data."""
    if not token:
        return value
    if isinstance(value, str):
        return value.replace(token, "***")
    if isinstance(value, dict):
        return {key: _redact_secret_data(item, token) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_secret_data(item, token) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_secret_data(item, token) for item in value)
    return value


def _quiet_http_loggers() -> None:
    """Stop httpx/httpcore from logging request URLs that embed the token."""
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


# ── Slash commands registry ───────────────────────────────────────
SLASH_COMMANDS = {
    "/menu": {
        "description": "Hiển thị menu lệnh",
        "usage": "/menu",
        "admin_only": False,
    },
    "/huong-dan": {
        "description": "Hướng dẫn sử dụng bot",
        "usage": "/huong-dan",
        "admin_only": False,
    },
    "/warn": {
        "description": "Cảnh báo thành viên",
        "usage": "/warn @user <reason>",
        "admin_only": True,
    },
    "/unwarn": {
        "description": "Xóa cảnh báo thành viên",
        "usage": "/unwarn @user",
        "admin_only": True,
    },
    "/report": {
        "description": "Báo cáo vi phạm",
        "usage": "/report @user <reason>",
        "admin_only": True,
    },
    "/rules": {
        "description": "Quy tắc nhóm",
        "usage": "/rules",
        "admin_only": False,
    },
    "/silent": {
        "description": "Silent mode (chỉ reply khi @tag)",
        "usage": "/silent",
        "admin_only": True,
    },
    "/welcome": {
        "description": "Bật/tắt welcome message",
        "usage": "/welcome",
        "admin_only": True,
    },
    "/welcome-text": {
        "description": "Đặt welcome text",
        "usage": "/welcome-text <nội dung>",
        "admin_only": True,
    },
    "/follow": {
        "description": "Bật/tắt lưu lịch sử trò chuyện nhóm",
        "usage": "/follow",
        "admin_only": True,
    },
    "/name-trigger": {
        "description": "Thêm name trigger",
        "usage": "/name-trigger <tên> <reply>",
        "admin_only": True,
    },
    "/settings": {
        "description": "Xem cài đặt nhóm",
        "usage": "/settings",
        "admin_only": False,
    },
}

# ---------------------------------------------------------------------------
# Zalo API Client (thin HTTP wrapper)
# ---------------------------------------------------------------------------


class _ZaloClient:
    """Async HTTP client for the Zalo Bot API using httpx."""

    def __init__(self, bot_token: str):
        _quiet_http_loggers()
        self.bot_token = bot_token
        self._base = ZALO_API_BASE.format(bot_token)
        self._client = None

    def _redact(self, value: Any) -> Any:
        return _redact_secret_data(value, self.bot_token)

    async def _ensure_client(self):
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0))

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _post(self, endpoint: str, data: dict = None) -> dict:
        """POST to an API endpoint and return parsed JSON."""
        await self._ensure_client()
        url = f"{self._base}/{endpoint}"
        try:
            resp = await self._client.post(url, json=data or {})
            resp.raise_for_status()
        except Exception as e:
            # httpx error text includes the request URL, which embeds the
            # bot token (/bot{token}/...). Re-raise with the token scrubbed.
            msg = _redact_secret_text(e, self.bot_token)
            raise RuntimeError(f"{type(e).__name__}: {msg}") from None
        return self._redact(resp.json())

    async def get_me(self) -> dict:
        """Verify bot token and get bot info."""
        return await self._post("getMe")

    async def get_updates(self, timeout: int = POLLING_TIMEOUT) -> list:
        """Long-poll for new updates.

        Returns a list of update objects (handles both single-object and
        array response formats from the Zalo API).
        """
        result = await self._post("getUpdates", {"timeout": timeout})
        if not result.get("ok"):
            return []
        raw = result.get("result")
        if isinstance(raw, list):
            return raw
        if isinstance(raw, dict):
            # Single object — wrap in list for uniform handling
            return [raw]
        return []

    async def send_message(self, chat_id: str, text: str, parse_mode: Optional[str] = None) -> dict:
        """Send a text message (max 2000 chars).

        When ``parse_mode`` ("markdown" or "html") is set, the Zalo server
        parses the markup in ``text`` and strips it from the displayed
        content, applying native rich-text styles. See
        https://docs.zaloplatforms.com/docs/BOT/apis/sendMessage.
        We never send ``text_styles`` alongside it — if both are present the
        server prefers ``parse_mode`` and ignores ``text_styles``.
        """
        # Truncate if too long
        text = text[:MAX_MESSAGE_LENGTH]
        payload = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        return await self._post("sendMessage", payload)

    async def send_photo(self, chat_id: str, photo_url: str, caption: str = "", parse_mode: Optional[str] = None) -> dict:
        """Send a photo from a URL."""
        payload = {"chat_id": chat_id, "photo": photo_url}
        if caption:
            payload["caption"] = caption[:MAX_MESSAGE_LENGTH]
            if parse_mode:
                payload["parse_mode"] = parse_mode
        return await self._post("sendPhoto", payload)

    async def send_chat_action(self, chat_id: str, action: str = "typing") -> dict:
        """Send a chat action indicator."""
        return await self._post("sendChatAction", {"chat_id": chat_id, "action": action})

    async def send_sticker(self, chat_id: str, sticker: str) -> dict:
        """Send a sticker by id (Bot API ``sendSticker``)."""
        return await self._post("sendSticker", {"chat_id": chat_id, "sticker": sticker})

    async def send_voice(self, chat_id: str, voice_url: str) -> dict:
        """Send a voice message from an ``.aac`` URL (Bot API ``sendVoice``)."""
        return await self._post(
            "sendVoice", {"chat_id": chat_id, "voice_url": voice_url}
        )


# ---------------------------------------------------------------------------
# Moderation Classes
# ---------------------------------------------------------------------------


class _AntiSpam:
    """Zero-token anti-spam detection."""

    def __init__(self) -> None:
        self._store: Dict[str, List[float]] = {}

    def check(self, group_id: str, user_id: str, text: str) -> Dict[str, Any]:
        now = time.time()
        key = f"{group_id}:{user_id}"
        if key not in self._store:
            self._store[key] = []
        self._store[key] = [t for t in self._store[key] if now - t < 60]
        self._store[key].append(now)
        if len(self._store[key]) > ANTI_SPAM_MAX_PER_MINUTE:
            return {"spam": True, "reason": "rate_limit_exceeded"}
        text_lower = text.lower()
        for pattern in ["bit.ly", "tinyurl.com", "t.me/", "tiktok.com",
                        "facebook.com/", "instagram.com/", "twitter.com/"]:
            if pattern in text_lower:
                return {"spam": True, "reason": "suspicious_link"}
        return {"spam": False, "reason": ""}


class _WarnSystem:
    """Zero-token warn tracking."""

    def __init__(self) -> None:
        self._store: Dict[str, List[Dict[str, Any]]] = {}

    def warn(self, group_id: str, user_id: str, reason: str = "") -> Dict[str, Any]:
        key = f"{group_id}:{user_id}"
        if key not in self._store:
            self._store[key] = []
        now = time.time()
        self._store[key] = [
            w for w in self._store[key]
            if now - w["timestamp"] < WARN_EXPIRY_SECONDS
        ]
        entry = {"timestamp": now, "reason": reason[:200],
                 "count": len(self._store[key]) + 1}
        self._store[key].append(entry)
        if entry["count"] >= WARN_MAX_WARNINGS:
            return {"ok": True, "action": "max_warnings_reached",
                    "warning": entry,
                    "message": f"⚠️ {user_id} đã đạt {WARN_MAX_WARNINGS} cảnh báo."}
        return {"ok": True, "action": "warned", "warning": entry}

    def get_warnings(self, group_id: str, user_id: str) -> List[Dict[str, Any]]:
        return self._store.get(f"{group_id}:{user_id}", [])

    def clear_warnings(self, group_id: str, user_id: str) -> Dict[str, Any]:
        self._store.pop(f"{group_id}:{user_id}", None)
        return {"ok": True, "action": "cleared"}


class _GroupSettings:
    """Per-group toggle settings (muted, silent, welcome, follow).

    The directory is resolved lazily through ``directory_provider`` so each
    multiplexed profile reads and writes below its own home. It is created with
    mode ``0700`` on the first write and every file is written ``0600``.
    """

    def __init__(self, directory_provider) -> None:
        self._directory_provider = directory_provider
        self._dir: Optional[Path] = None

    def _directory(self, *, create: bool) -> Optional[Path]:
        if self._dir is None:
            try:
                directory = self._directory_provider()
            except Exception:
                directory = None
            if directory is None:
                return None
            self._dir = Path(directory)
        if create:
            try:
                self._dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                os.chmod(self._dir, 0o700)
            except OSError as error:
                logger.debug(
                    "Zalo: settings directory unavailable: %s",
                    type(error).__name__,
                )
                return None
        return self._dir

    def _filepath(self, group_id: str, *, create: bool = False) -> Optional[Path]:
        directory = self._directory(create=create)
        if directory is None:
            return None
        return directory / f"{_safe_path_component(group_id)}.json"

    def _load(self, group_id: str) -> Dict[str, Any]:
        filepath = self._filepath(group_id)
        if filepath is None or not filepath.exists():
            return {
                "muted": False,
                "silent": False,
                "welcome": False,
                "follow": True,
                "welcome_text": "",
                "name_triggers": [],
            }
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                value = json.load(f)
            return value if isinstance(value, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {"muted": False, "silent": False, "welcome": False, "follow": True}

    def _save(self, group_id: str, settings: Dict[str, Any]) -> None:
        filepath = self._filepath(group_id, create=True)
        if filepath is None:
            return
        _write_private_json(filepath, settings)

    def get(self, group_id: str) -> Dict[str, Any]:
        return self._load(group_id)

    def set(self, group_id: str, key: str, value: Any) -> Dict[str, Any]:
        settings = self._load(group_id)
        settings[key] = value
        self._save(group_id, settings)
        return {"ok": True, "key": key, "value": value}

    def set_multiple(self, group_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        settings = self._load(group_id)
        settings.update(updates)
        self._save(group_id, settings)
        return {"ok": True, "updated": list(updates.keys())}

    def toggle(self, group_id: str, key: str) -> Dict[str, Any]:
        settings = self._load(group_id)
        current = settings.get(key, False)
        settings[key] = not current
        self._save(group_id, settings)
        return {"ok": True, "key": key, "value": settings[key]}


class _BotHistoryStore:
    """Append-only per-chat conversation store for the Bot channel.

    Every record is one JSON line written immediately (no buffer) to
    ``<profile home>/zalo_bot/history/<chat_id>.jsonl``. Directories are
    ``0700`` and files ``0600``; writes through a symlink are skipped. Storage
    is diagnostic data for the Runtime, so any failure is logged at debug
    level without content and never interrupts message handling.
    """

    def __init__(self, home: Path) -> None:
        self._home = Path(home)

    # ── Paths ─────────────────────────────────────────────────────────────

    def _directory(self, *, create: bool) -> Optional[Path]:
        """Return the resolved history directory, or None when unsafe."""
        base = self._home / BOT_HISTORY_DIRNAME
        if base.is_symlink():
            logger.debug("Zalo: bot history base is a symlink; skipping")
            return None
        directory = base / BOT_HISTORY_SUBDIR
        if directory.is_symlink():
            logger.debug("Zalo: bot history directory is a symlink; skipping")
            return None
        if create:
            try:
                base.mkdir(parents=True, exist_ok=True, mode=0o700)
                os.chmod(base, 0o700)
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                os.chmod(directory, 0o700)
            except OSError as error:
                logger.debug(
                    "Zalo: bot history directory unavailable: %s",
                    type(error).__name__,
                )
                return None
        if not directory.is_dir():
            return None
        return directory

    def _path(self, chat_id: str) -> Optional[Path]:
        if not chat_id:
            return None
        directory = self._directory(create=False)
        if directory is None:
            return None
        return directory / f"{_safe_path_component(chat_id)}.jsonl"

    @staticmethod
    def _history_files(directory: Path) -> List[Path]:
        """Return regular ``*.jsonl`` files in ``directory``; never symlinks."""
        files: List[Path] = []
        try:
            entries = list(directory.iterdir())
        except OSError:
            return files
        for path in entries:
            try:
                if path.is_symlink() or not path.is_file():
                    continue
            except OSError:
                continue
            if path.suffix == ".jsonl":
                files.append(path)
        return files

    # ── Records ───────────────────────────────────────────────────────────

    @staticmethod
    def _normalize(record: Dict[str, Any]) -> Dict[str, Any]:
        message_type = str(record.get("type") or "text").strip().lower()
        if message_type not in ("text", "image", "sticker", "unsupported"):
            message_type = "unsupported"
        try:
            timestamp = float(record.get("ts"))
        except (TypeError, ValueError):
            timestamp = time.time()
        normalized = {
            "message_id": str(record.get("message_id") or ""),
            "chat_id": str(record.get("chat_id") or ""),
            "chat_type": "group" if record.get("chat_type") == "group" else "dm",
            "direction": "out" if record.get("direction") == "out" else "in",
            "sender_id": str(record.get("sender_id") or ""),
            "sender_name": str(record.get("sender_name") or "")[:200],
            "type": message_type,
            "text": str(record.get("text") or "")[:HISTORY_TEXT_LIMIT],
            "ts": timestamp,
            # Untrusted external chat content. Consumers must keep it out of
            # instruction and system-prompt positions.
            "trust": "untrusted_external",
        }
        if message_type in ("image", "sticker"):
            normalized["media_url"] = str(record.get("media_url") or "")
        return normalized

    def append(self, record: Dict[str, Any]) -> None:
        """Write exactly one JSON line immediately; never raise."""
        try:
            chat_id = str(record.get("chat_id") or "")
            if not chat_id:
                return
            directory = self._directory(create=True)
            if directory is None:
                return
            path = directory / f"{_safe_path_component(chat_id)}.jsonl"
            if path.is_symlink():
                logger.debug("Zalo: bot history file is a symlink; skipping")
                return
            line = json.dumps(self._normalize(record), ensure_ascii=False) + "\n"
            fd = os.open(
                str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600
            )
            try:
                os.write(fd, line.encode("utf-8"))
            finally:
                os.close(fd)
        except OSError as error:
            logger.debug("Zalo: bot history write failed: %s", type(error).__name__)
        except Exception as error:  # never break message handling
            logger.debug("Zalo: bot history write failed: %s", type(error).__name__)

    def has(self, chat_id: str) -> bool:
        path = self._path(chat_id)
        return path is not None and path.is_file() and not path.is_symlink()

    # ── Deletion and retention ────────────────────────────────────────────

    def delete(self, chat_id: str) -> int:
        """Delete one chat file; never follow a symlink."""
        path = self._path(chat_id)
        if path is None or path.is_symlink() or not path.is_file():
            return 0
        try:
            path.unlink()
        except OSError as error:
            logger.debug("Zalo: bot history delete failed: %s", type(error).__name__)
            return 0
        return 1

    def delete_all(self) -> int:
        """Delete every chat file in the store; never follow symlinks."""
        directory = self._directory(create=False)
        if directory is None:
            return 0
        removed = 0
        for path in self._history_files(directory):
            try:
                path.unlink()
            except OSError as error:
                logger.debug(
                    "Zalo: bot history delete failed: %s", type(error).__name__
                )
                continue
            removed += 1
        return removed

    @staticmethod
    def _replace_lines(path: Path, lines: List[str]) -> None:
        """Atomically replace ``path`` with ``lines``, or delete it if empty."""
        directory = path.parent
        if not lines:
            try:
                path.unlink()
            except OSError:
                pass
            return
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(directory))
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                fd = -1
                for line in lines:
                    handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            temporary = ""
        finally:
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def _trim_file(self, path: Path, cutoff: float) -> None:
        """Drop records older than ``cutoff`` and malformed lines."""
        if path.is_symlink() or not path.is_file():
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                raw_lines = handle.readlines()
        except OSError:
            return
        kept: List[str] = []
        dropped = False
        for raw in raw_lines:
            line = raw.strip()
            if not line:
                dropped = True
                continue
            try:
                value = json.loads(line)
            except (ValueError, TypeError):
                dropped = True
                continue
            if not isinstance(value, dict):
                dropped = True
                continue
            try:
                timestamp = float(value.get("ts"))
            except (TypeError, ValueError):
                timestamp = 0.0
            if timestamp < cutoff:
                dropped = True
                continue
            kept.append(line)
        if not dropped:
            return
        try:
            self._replace_lines(path, kept)
        except OSError as error:
            logger.debug("Zalo: bot history trim failed: %s", type(error).__name__)

    @staticmethod
    def _trim_size(directory: Path, max_bytes: int) -> None:
        """Delete whole chat files oldest-mtime-first until under the cap."""
        files = []
        for path in _BotHistoryStore._history_files(directory):
            try:
                files.append((path.stat().st_mtime, path.name, path))
            except OSError:
                continue
        total = 0
        for _, _, path in files:
            try:
                total += path.stat().st_size
            except OSError:
                continue
        if total <= max_bytes:
            return
        files.sort(key=lambda item: (item[0], item[1]))
        for _, _, path in files:
            if total <= max_bytes:
                break
            try:
                size = path.stat().st_size
            except OSError:
                continue
            try:
                path.unlink()
            except OSError:
                continue
            total -= size

    def trim(self, retention_days: int, max_bytes: int = HISTORY_MAX_BYTES) -> None:
        """Apply age retention, then the per-profile size cap; never raise."""
        try:
            directory = self._directory(create=False)
            if directory is None:
                return
            cutoff = time.time() - int(retention_days) * 86400
            for path in self._history_files(directory):
                self._trim_file(path, cutoff)
            self._trim_size(directory, max_bytes)
        except Exception as error:  # diagnostic only
            logger.debug("Zalo: bot history trim failed: %s", type(error).__name__)


class _CRMContacts:
    """CRM contact management for Zalo groups.

    The directory is resolved lazily through ``directory_provider`` so each
    multiplexed profile keeps its own contacts. Files are written ``0600``.
    """

    def __init__(self, directory_provider) -> None:
        self._directory_provider = directory_provider
        self._dir: Optional[Path] = None

    def _directory(self, *, create: bool) -> Optional[Path]:
        if self._dir is None:
            try:
                directory = self._directory_provider()
            except Exception:
                directory = None
            if directory is None:
                return None
            self._dir = Path(directory)
        if create:
            try:
                self._dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                os.chmod(self._dir, 0o700)
            except OSError as error:
                logger.debug(
                    "Zalo: CRM directory unavailable: %s", type(error).__name__
                )
                return None
        return self._dir

    def _filepath(self, group_id: str, *, create: bool = False) -> Optional[Path]:
        directory = self._directory(create=create)
        if directory is None:
            return None
        return directory / f"{_safe_path_component(group_id)}_contacts.json"

    def _load(self, group_id: str) -> Dict[str, Any]:
        filepath = self._filepath(group_id)
        if filepath is None or not filepath.exists():
            return {}
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                value = json.load(f)
            return value if isinstance(value, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, group_id: str, contacts: Dict[str, Any]) -> None:
        filepath = self._filepath(group_id, create=True)
        if filepath is None:
            return
        _write_private_json(filepath, contacts)

    def add_contact(self, group_id: str, user_id: str, phone: str = "",
                    name: str = "", labels: Optional[List[str]] = None) -> Dict[str, Any]:
        contacts = self._load(group_id)
        contact = contacts.get(user_id, {})
        contact.update({"user_id": user_id, "phone": phone, "name": name,
                        "labels": labels or [], "updated_at": time.time()})
        contacts[user_id] = contact
        self._save(group_id, contacts)
        return {"ok": True, "contact": contact}

    def get_contact(self, group_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        return self._load(group_id).get(user_id)

    def list_contacts(self, group_id: str, label: str = "") -> List[Dict[str, Any]]:
        contacts = list(self._load(group_id).values())
        if label:
            contacts = [c for c in contacts if label in c.get("labels", [])]
        return sorted(contacts, key=lambda c: c.get("updated_at", 0), reverse=True)

    def import_csv(self, group_id: str, csv_path: str) -> Dict[str, Any]:
        import csv as _csv
        contacts = self._load(group_id)
        imported = 0
        with open(csv_path, "r", encoding="utf-8") as f:
            for row in _csv.DictReader(f):
                uid = row.get("user_id", "")
                if not uid:
                    continue
                c = contacts.get(uid, {})
                c.update({"user_id": uid, "phone": row.get("phone", ""),
                          "name": row.get("name", ""),
                          "labels": [l.strip() for l in row.get("labels", "").split(",") if l.strip()],
                          "updated_at": time.time()})
                contacts[uid] = c
                imported += 1
        self._save(group_id, contacts)
        return {"ok": True, "imported": imported}

    def export_csv(self, group_id: str, csv_path: str) -> Dict[str, Any]:
        import csv as _csv
        contacts = self._load(group_id)
        exported = 0
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=["user_id", "phone", "name", "labels"])
            w.writeheader()
            for c in contacts.values():
                w.writerow({"user_id": c.get("user_id", ""),
                            "phone": c.get("phone", ""),
                            "name": c.get("name", ""),
                            "labels": ",".join(c.get("labels", []))})
                exported += 1
        return {"ok": True, "exported": exported}


# ---------------------------------------------------------------------------
# Zalo Adapter
# ---------------------------------------------------------------------------


class ZaloAdapter(BasePlatformAdapter):
    """Async Zalo adapter implementing the BasePlatformAdapter interface.

    Connects to the Zalo Bot API via long-polling and relays DMs
    between Zalo users and the Hermes agent.
    """

    def __init__(self, config, **kwargs):
        _quiet_http_loggers()
        platform = Platform("zalo_bot")
        super().__init__(config=config, platform=platform)

        extra = getattr(config, "extra", {}) or {}

        # Bot token (env overrides config.yaml)
        self.bot_token = _resolve_zalo_secret("ZALO_BOT_TOKEN", extra.get("bot_token", ""))
        self.bot_token = self.bot_token.strip()

        # DM policy (env var overrides config.yaml). Only disabled, allowlist
        # and pairing are supported; unknown values fail closed to disabled.
        self.dm_policy = _coerce_dm_policy(
            os.getenv("ZALO_BOT_DM_POLICY") or extra.get("dm_policy", "pairing")
        )
        # Hermes gateway authorization reads the private alias.
        self._dm_policy = self.dm_policy

        # Allowed users (authorisation). A string is always comma-split; it is
        # never iterated character by character.
        allowed_env = os.getenv("ZALO_BOT_ALLOWED_USERS", "").strip()
        if allowed_env:
            self.allowed_users: set = _coerce_allowed_users(allowed_env)
        else:
            self.allowed_users: set = _coerce_allowed_users(extra.get("allowed_users", []))

        allow_all_env = os.getenv("ZALO_BOT_ALLOW_ALL_USERS", "").strip()
        if allow_all_env:
            self.allow_all = _coerce_bool(allow_all_env)
        else:
            self.allow_all = _coerce_bool(extra.get("allow_all", False))

        # Webhook mode
        self.webhook_url = extra.get("webhook_url", "")
        self.webhook_secret = extra.get("webhook_secret", "")
        self.webhook_port = int(extra.get("webhook_port", 8443))
        # Loopback by default: run behind a TLS reverse proxy. Set
        # webhook_host: "0.0.0.0" to listen on all interfaces.
        self.webhook_host = str(extra.get("webhook_host", "127.0.0.1"))

        # Local conversation store retention (days), clamped to 1..365.
        self.history_retention_days = _coerce_retention_days(
            extra.get("history_retention_days", HISTORY_RETENTION_DEFAULT_DAYS)
        )

        # Runtime state
        self._client: Optional[_ZaloClient] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._webhook_runner: Optional[asyncio.Task] = None
        self._history_trim_task: Optional[asyncio.Task] = None
        self._bot_history: Optional[_BotHistoryStore] = None
        self._bot_info: Optional[dict] = None
        self._bot_id: str = ""
        self._bot_name: str = ""
        self._bot_display_name: str = ""
        self._seen_message_ids: "OrderedDict[str, None]" = OrderedDict()
        # Resolved at connect() inside the profile's runtime scope, when Hermes
        # multiplexing installs a context-local HERMES_HOME override.
        self._profile_home: Optional[Path] = None
        self._state_last_write = 0.0

        # Moderation instances. Zalo's Bot API exposes no group management, so
        # there is no group manager and no server-side settings source.
        self._anti_spam = _AntiSpam()
        self._warn_system = _WarnSystem()
        # Both stores resolve their directory lazily from the profile home
        # (or their environment override) and are rebuilt on connect().
        self._group_settings = _GroupSettings(self._settings_directory)
        self._crm_contacts = _CRMContacts(self._crm_directory)

    def _resolve_state_home(self) -> Optional[Path]:
        """Return this adapter's profile home for its runtime state file.

        ``get_hermes_home()`` is the single source of truth: a single-profile
        gateway reads ``HERMES_HOME``, while a multiplexed gateway installs a
        context-local override for the profile before it creates and connects
        the adapter. Resolving on the adapter (not at import time) keeps one
        process's adapters writing to their own profile directories.
        """
        if self._profile_home is None:
            try:
                self._profile_home = Path(get_hermes_home())
            except Exception:
                return None
        return self._profile_home

    def _plugin_state_directory(self, env_name: str, dirname: str) -> Optional[Path]:
        """Return one plugin state directory, honouring its env override."""
        override = os.environ.get(env_name, "").strip()
        if override:
            return Path(override)
        home = self._resolve_state_home()
        if home is None:
            try:
                home = Path(get_hermes_home())
            except Exception:
                return None
        return home / dirname

    def _settings_directory(self) -> Optional[Path]:
        return self._plugin_state_directory(SETTINGS_DIR_ENV, SETTINGS_DIRNAME)

    def _crm_directory(self) -> Optional[Path]:
        return self._plugin_state_directory(CRM_DIR_ENV, CRM_DIRNAME)


    # ── Conversation store ────────────────────────────────────────────────

    def _history_store(self) -> Optional["_BotHistoryStore"]:
        """Return this profile's store, resolving the home on first use."""
        if self._bot_history is None:
            home = self._resolve_state_home()
            if home is None:
                try:
                    home = Path(get_hermes_home())
                except Exception:
                    return None
            self._bot_history = _BotHistoryStore(home)
        return self._bot_history

    def _group_is_approved(self, chat_id: str) -> bool:
        """Return True only for a Runtime-approved ``zalo_bot`` group.

        The Runtime channels service owns ``<profile>/zalo/groups.json``. The
        file is small, so it is read on every call; any error, symlink, or
        malformed record fails closed.
        """
        if not chat_id:
            return False
        try:
            home = self._resolve_state_home()
            if home is None:
                return False
            channels_dir = home / BOT_CHANNELS_DIRNAME
            path = channels_dir / BOT_GROUPS_FILE
            if channels_dir.is_symlink() or path.is_symlink() or not path.is_file():
                return False
            with open(path, "r", encoding="utf-8") as handle:
                value = json.loads(handle.read(BOT_GROUPS_MAX_BYTES))
            platforms = value.get("platforms") if isinstance(value, dict) else None
            record = platforms.get("zalo_bot") if isinstance(platforms, dict) else None
            groups = record.get("groups") if isinstance(record, dict) else None
            entry = groups.get(chat_id) if isinstance(groups, dict) else None
            return (
                isinstance(entry, dict)
                and str(entry.get("status") or "").strip().lower() == "approved"
            )
        except Exception:
            return False

    def _is_paired(self, user_id: str) -> bool:
        """Return True when the owner paired this sender in the gateway store."""
        try:
            from gateway.pairing import PairingStore
            from hermes_constants import get_default_hermes_root

            home = self._resolve_state_home()
            profile = "default"
            if home is not None:
                try:
                    default_root = Path(get_default_hermes_root()).resolve()
                except Exception:
                    default_root = None
                if default_root is None or Path(home).resolve() != default_root:
                    profile = Path(home).name
            return bool(
                PairingStore(profile=profile).is_approved("zalo_bot", str(user_id))
            )
        except Exception:
            return False

    def _dm_history_allowed(self, user_id: str) -> bool:
        """Store DMs only from senders the policy allows or the owner paired."""
        if self.dm_policy == "disabled":
            return False
        if not self._passes_allowlist(user_id):
            return False
        if self.allow_all or user_id in self.allowed_users:
            return True
        return self._is_paired(user_id)

    def _record_history(
        self,
        *,
        chat_id: str,
        chat_type: str,
        direction: str,
        sender_id: str,
        sender_name: str,
        message_type: str,
        text: str,
        message_id: str = "",
        media_url: str = "",
    ) -> None:
        """Append one conversation record; storage never breaks handling."""
        if not chat_id:
            return
        store = self._history_store()
        if store is None:
            return
        store.append(
            {
                "message_id": message_id,
                "chat_id": chat_id,
                "chat_type": chat_type,
                "direction": direction,
                "sender_id": sender_id,
                "sender_name": sender_name,
                "type": message_type,
                "text": text,
                "ts": time.time(),
                "media_url": media_url,
            }
        )

    def _outbound_history_scope(self, chat_id: str) -> tuple:
        """Return ``(store, chat_type)`` for one bot reply.

        Store replies in approved groups with follow on, in chats that already
        have a stored DM file, and for allowlisted or paired DMs; never create
        a file for an unapproved group.
        """
        if self._group_is_approved(chat_id):
            settings = self._group_settings.get(chat_id)
            return settings.get("follow", True), "group"
        store = self._history_store()
        if store is not None and store.has(chat_id):
            return True, "dm"
        if self.allow_all or chat_id in self.allowed_users or self._is_paired(chat_id):
            return True, "dm"
        return False, "dm"

    def _record_outbound(
        self,
        chat_id: str,
        text: str,
        message_id: str = "",
        message_type: str = "text",
        media_url: str = "",
    ) -> None:
        """Store one successful bot reply when the chat is in scope."""
        allowed, chat_type = self._outbound_history_scope(chat_id)
        if not allowed:
            return
        self._record_history(
            chat_id=chat_id,
            chat_type=chat_type,
            direction="out",
            sender_id=self._bot_id,
            sender_name=self._bot_display_name or self._bot_name,
            message_type=message_type,
            text=text,
            message_id=message_id,
            media_url=media_url,
        )

    def _write_state(self, state: str) -> None:
        """Atomically persist this profile's Zalo Bot connection state.

        The Runtime channels service reads ``<profile>/zalo_bot_state.json``;
        it must never contain a token, bot ID, or message content. A failed
        write is diagnostic only and never interrupts the adapter.
        """
        temporary = ""
        try:
            home = self._resolve_state_home()
            if home is None:
                return
            path = home / STATE_FILE
            payload = json.dumps(
                {
                    "state": state,
                    "updated_at": time.time(),
                    "pid": os.getpid(),
                },
                sort_keys=True,
            )
            fd, temporary = tempfile.mkstemp(
                prefix=f".{STATE_FILE}.", dir=str(home)
            )
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    fd = -1
                    handle.write(payload + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
                temporary = ""
            except Exception:
                if fd >= 0:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                raise
            self._state_last_write = time.monotonic()
        except Exception as error:
            logger.debug("Zalo: state write failed: %s", type(error).__name__)
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def _refresh_state_heartbeat(self) -> None:
        """Refresh ``connected`` at most once per heartbeat interval."""
        if time.monotonic() - self._state_last_write < STATE_HEARTBEAT_SECONDS:
            return
        self._write_state("connected")

    def _redact_token(self, token: str) -> str:
        """Placeholder kept for callers wanting a display form of the token.

        Note: the plugin's actual redaction path replaces the raw token with
        ``***`` in error text (see ``_ZaloClient._post``); this helper is not
        part of any log output.
        """
        if len(token) <= 12:
            return token[:4] + "..." + token[-4:] if len(token) > 8 else "***"
        return token[:4] + "..." + token[-4:]

    def _redact(self, value: Any) -> Any:
        """Redact the bot token from API data before logging it."""
        return _redact_secret_data(value, self.bot_token)

    def _is_duplicate_message(self, chat_id: str, message_id: str) -> bool:
        """Return True for a repeated real provider ID; never use timestamps.

        Both long-polling and webhook delivery call ``_handle_incoming`` with
        the provider's message (or update) ID, so they share this bounded set.
        A missing ID is not deduped and the message is preserved.
        """
        identifier = str(message_id or "").strip()
        if not identifier:
            return False
        key = f"{chat_id}:{identifier}"
        if key in self._seen_message_ids:
            self._seen_message_ids.move_to_end(key)
            return True
        self._seen_message_ids[key] = None
        while len(self._seen_message_ids) > MAX_SEEN_MESSAGE_IDS:
            self._seen_message_ids.popitem(last=False)
        return False

    @property
    def name(self) -> str:
        return "Zalo"

    # ── Connection lifecycle ──────────────────────────────────────────────

    async def connect(self, is_reconnect: bool = False) -> bool:
        """Connect to Zalo Bot API: verify token and start polling/webhook."""
        self._profile_home = None
        self._bot_history = None
        # Rebuild the lazy directory users after the profile-home reset.
        self._group_settings = _GroupSettings(self._settings_directory)
        self._crm_contacts = _CRMContacts(self._crm_directory)
        self._write_state("connecting")
        if not self.bot_token:
            logger.error("Zalo: bot_token must be configured")
            self._set_fatal_error(
                "config_missing",
                "ZALO_BOT_TOKEN must be set",
                retryable=False,
            )
            self._write_state("disconnected")
            return False

        self._client = _ZaloClient(self.bot_token)

        # Verify token
        try:
            me = await self._client.get_me()
            if not me.get("ok"):
                safe_me = self._redact(me)
                logger.error("Zalo: token verification failed: %s", safe_me)
                self._set_fatal_error(
                    "auth_failed",
                    f"Zalo token rejected: {safe_me}",
                    retryable=False,
                )
                self._write_state("disconnected")
                return False
            self._bot_info = me.get("result", {})
            self._bot_id = str(self._bot_info.get("id", ""))
            self._bot_name = self._bot_info.get("account_name", "")
            self._bot_display_name = self._bot_info.get("display_name", "")
            logger.info(
                "Zalo: verified bot '%s' (id=%s, type=%s)",
                self._bot_info.get("account_name", "?"),
                self._bot_info.get("id", "?"),
                self._bot_info.get("account_type", "?"),
            )
        except Exception as e:
            safe_error = _redact_secret_text(e, self.bot_token)
            logger.error("Zalo: token verification error: %s", safe_error)
            self._set_fatal_error("auth_error", safe_error, retryable=True)
            self._write_state("disconnected")
            return False

        # Start receiving messages (polling or webhook)
        if self.webhook_url and self.webhook_secret:
            # Run webhook listener as a background task so is_connected can be
            # marked before its keep-alive loop is evaluated (fix: while loop
            # on is_connected previously saw False -> cleanup closed listener).
            self._webhook_runner = asyncio.create_task(self._start_webhook())
        else:
            await self._start_polling()

        self._mark_connected()
        self._write_state("connected")
        # Retention applies from the first connected moment, then daily.
        store = self._history_store()
        if store is not None:
            store.trim(self.history_retention_days)
        self._history_trim_task = asyncio.create_task(self._history_trim_loop())
        logger.info(
            "Zalo: connected as '%s' (%s)",
            self._bot_info.get("account_name", "?"),
            "webhook" if self.webhook_url else "long-polling",
        )
        return True

    async def disconnect(self) -> None:
        """Stop polling/webhook and close the API client."""
        self._write_state("disconnected")
        self._mark_disconnected()

        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        self._poll_task = None

        if self._webhook_runner and not self._webhook_runner.done():
            self._webhook_runner.cancel()
            try:
                await self._webhook_runner
            except asyncio.CancelledError:
                pass
        self._webhook_runner = None

        if self._history_trim_task and not self._history_trim_task.done():
            self._history_trim_task.cancel()
            try:
                await self._history_trim_task
            except asyncio.CancelledError:
                pass
        self._history_trim_task = None

        if self._client:
            await self._client.close()
            self._client = None

    # ── Polling mode ──────────────────────────────────────────────────────

    async def _start_polling(self) -> None:
        """Start the long-polling receive loop."""
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def _poll_loop(self) -> None:
        """Continuously poll for updates and refresh the connected heartbeat."""
        consecutive_errors = 0

        try:
            while self.is_connected:
                try:
                    updates = await self._client.get_updates(timeout=POLLING_TIMEOUT)
                    # The receive loop is healthy after every successful poll,
                    # including an empty long-poll response.
                    self._refresh_state_heartbeat()

                    if not updates:
                        await asyncio.sleep(POLLING_INTERVAL)
                        continue

                    consecutive_errors = 0

                    for update in updates:
                        event_name = update.get("event_name", "")
                        message = update.get("message") or update.get("result", {}).get(
                            "message", {}
                        )

                        if not message:
                            continue

                        update_id = (
                            update.get("update_id")
                            or update.get("message_id")
                            or update.get("result", {}).get("update_id")
                        )
                        await self._handle_incoming(
                            event_name, message, update_id=update_id
                        )

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    consecutive_errors += 1
                    delay = min(RECONNECT_DELAY * consecutive_errors, 60)
                    logger.warning(
                        "Zalo: poll error (attempt %d): %s — retrying in %ds",
                        consecutive_errors, e, delay,
                    )
                    await asyncio.sleep(delay)
        finally:
            self._write_state("disconnected")

    async def _history_trim_loop(self) -> None:
        """Trim the conversation store once per day while connected."""
        try:
            while self.is_connected:
                await asyncio.sleep(HISTORY_TRIM_INTERVAL_SECONDS)
                store = self._history_store()
                if store is not None:
                    store.trim(self.history_retention_days)
        except asyncio.CancelledError:
            raise

    # ── Webhook mode ──────────────────────────────────────────────────────

    async def _start_webhook(self) -> None:
        """Start the webhook listener using aiohttp."""
        try:
            from aiohttp import web
        except ImportError:
            logger.error(
                "Zalo: webhook mode requires aiohttp. "
                "Install it with: pip install aiohttp"
            )
            # Fall back to polling
            logger.info("Zalo: falling back to long-polling")
            await self._start_polling()
            return

        app = web.Application()

        async def webhook_handler(request):
            # Verify secret token
            secret = request.headers.get(WEBHOOK_SECRET_HEADER, "")
            if not hmac.compare_digest(
                secret.encode("utf-8"), self.webhook_secret.encode("utf-8")
            ):
                return web.Response(status=403, text="Unauthorized")

            try:
                body = await request.json()
                if body.get("ok"):
                    result = body.get("result", {})
                    event_name = result.get("event_name", "")
                    message = result.get("message", {})
                    if message:
                        update_id = (
                            result.get("update_id")
                            or body.get("update_id")
                            or result.get("message_id")
                        )
                        await self._handle_incoming(
                            event_name, message, update_id=update_id
                        )
                return web.json_response({"ok": True})
            except Exception as e:
                logger.warning("Zalo: webhook handler error: %s", e)
                return web.json_response({"ok": False, "error": "internal error"}, status=500)

        app.router.add_post("/webhook/zalo", webhook_handler)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, self.webhook_host, self.webhook_port)
        await site.start()
        logger.info("Zalo: webhook listening on %s:%d", self.webhook_host, self.webhook_port)

        # Keep the task alive (runs as background task; cancelled on disconnect)
        # and refresh the connected heartbeat from this same loop.
        try:
            while True:
                await asyncio.sleep(STATE_HEARTBEAT_SECONDS)
                self._refresh_state_heartbeat()
        except asyncio.CancelledError:
            raise
        finally:
            await runner.cleanup()
            self._write_state("disconnected")

    # ── Incoming message handling ─────────────────────────────────────────

    async def _handle_incoming(
        self, event_name: str, message: dict, update_id: Optional[str] = None
    ) -> None:
        """Process an incoming message from Zalo."""
        from_info = message.get("from", {})
        chat_info = message.get("chat", {})

        user_id = str(from_info.get("id", ""))
        user_name = from_info.get("display_name", user_id)
        chat_id = str(chat_info.get("id", user_id))
        # Hermes only offers pairing codes for chat_type "dm"; Zalo sends "PRIVATE".
        chat_type = "group" if chat_info.get("chat_type") == "GROUP" else "dm"
        raw_text = (message.get("text") or message.get("caption") or "").strip()
        message_id = str(message.get("message_id") or update_id or "").strip()

        # Polling and webhook delivery can both observe the same provider
        # update. Dedupe on the real provider ID before any side effect.
        if self._is_duplicate_message(chat_id, message_id):
            logger.debug("Zalo: duplicate update ignored for chat %s", chat_id)
            return

        # ── Group member joined event → welcome message ────────────────
        if "join" in event_name.lower() or "member_joined" in event_name.lower():
            if not self._is_user_authorized(user_id):
                logger.debug(
                    "Zalo: ignoring join welcome for unauthorized user %s", user_id
                )
                return
            settings = self._group_settings.get(chat_id)
            if settings.get("welcome", False):
                welcome_text = settings.get("welcome_text", "") or (
                    f"Chào mừng {user_name} đến với nhóm! 🎉\n"
                    "Gõ /menu để xem danh sách lệnh."
                )
                await self._send_text(chat_id, welcome_text)
            return

        # Ignore bot's own messages
        if from_info.get("is_bot", False):
            return

        # Determine message type and extract text/media
        message_type = MessageType.TEXT
        media_url = None
        text = raw_text
        # Conversation-store form of the same fact; stickers keep their URL as
        # media while the dispatch path keeps the legacy text behavior.
        history_type = "text"
        history_media_url = ""

        if event_name == "message.image.received":
            message_type = MessageType.IMAGE
            media_url = message.get("photo", "")
            if not text and message.get("caption"):
                text = message["caption"]
            history_type = "image"
            history_media_url = str(media_url or "")
        elif event_name == "message.sticker.received":
            message_type = MessageType.STICKER
            text = message.get("sticker", "") or message.get("url", "") or "[Sticker]"
            history_type = "sticker"
            history_media_url = str(message.get("sticker") or message.get("url") or "")
        elif event_name == "message.unsupported.received":
            text = "[Unsupported message type]"
            history_type = "unsupported"

        # ── Group message handling ──────────────────────────────────────
        is_group = chat_type == "group"
        if is_group:
            settings = self._group_settings.get(chat_id)

            # 1. Muted check — group muted → ignore all messages
            if settings.get("muted", False):
                logger.debug("Zalo: group %s muted, ignoring message", chat_id)
                return

            # 2. Conversation store — keep every member's message in an
            #    approved group with follow on, before per-sender
            #    authorization and mention gating, so a summary is complete.
            #    Slash commands are control traffic and are not stored.
            if (
                self._group_is_approved(chat_id)
                and settings.get("follow", True)
                and parse_slash_command(text) is None
            ):
                self._record_history(
                    chat_id=chat_id,
                    chat_type="group",
                    direction="in",
                    sender_id=user_id,
                    sender_name=user_name,
                    message_type=history_type,
                    text=text,
                    message_id=message_id,
                    media_url=history_media_url,
                )

            # 3. Authorization before any name-trigger side effect. With no
            #    adapter allowlist this defers to Hermes core, but it still
            #    rejects a configured allowlist mismatch here.
            if not self._group_member_allowed(user_id):
                logger.debug("Zalo: ignoring group message from unauthorized user %s", user_id)
                return

            # 4. Slash commands work regardless of silent mode (admin ops).
            #    The slash handler performs its own authorization and live
            #    owner/admin lookup before any privileged action.
            slash = parse_slash_command(text)
            if slash:
                await self._handle_slash_command(
                    slash, chat_id, user_id, user_name, message_type, media_url, chat_type
                )
                return

            # 5. Anti-spam check (BEFORE dispatch)
            spam = self._anti_spam.check(chat_id, user_id, text)
            if spam["spam"]:
                logger.debug("Zalo: spam blocked for %s in group %s", user_id, chat_id)
                return

            # 6. Side-effect paths require explicit adapter authorization.
            #    Deferring group delivery to Hermes core is not enough here:
            #    name triggers never reach Hermes authorization.
            explicitly_authorized = self._is_user_authorized(user_id)
            if explicitly_authorized:
                triggers = settings.get("name_triggers", [])
                for t in triggers:
                    tname = str(t.get("name", "")).lower()
                    if tname and tname in text.lower():
                        await self._send_text(chat_id, str(t.get("reply", "")))
                        return

            # 7. Check if bot is mentioned: @mention or reply to bot
            mentioned = False

            # 7a. Check @mention (account_name OR display_name)
            if (self._bot_name and self._bot_name in text) or \
               (self._bot_display_name and self._bot_display_name in text):
                mentioned = True

            # 7b. Check reply
            if message.get("reply_to"):
                reply_to = message["reply_to"]
                if isinstance(reply_to, dict) and reply_to.get("from", {}).get("id") == self._bot_id:
                    mentioned = True

            # 7c. Silent mode: only reply when mentioned
            #     (default behavior = silent ON; silent OFF still requires mention
            #      to avoid burning tokens on every group message)
            if not mentioned:
                logger.debug("Zalo: ignoring group message — bot not mentioned")
                return

            # Strip bot mention from text
            if self._bot_name:
                text = text.replace(f"@{self._bot_name}", "").replace(f"{self._bot_name}", "").strip()
            if self._bot_display_name:
                text = text.replace(f"@{self._bot_display_name}", "").replace(f"{self._bot_display_name}", "").strip()

            # Slash commands are recognized after mention stripping so
            # "@bot /menu" still reaches the adapter. Any other leading-slash
            # text is a Hermes command, which is DM-only; drop it in groups
            # instead of forwarding it to the agent.
            remaining_slash = parse_slash_command(text)
            if remaining_slash is not None:
                await self._handle_slash_command(
                    remaining_slash,
                    chat_id,
                    user_id,
                    user_name,
                    message_type,
                    media_url,
                    chat_type,
                )
                return
            if text.startswith("/"):
                logger.debug(
                    "Zalo: dropping Hermes slash command in group %s", chat_id
                )
                return

            # An empty mention carries nothing the agent can answer. Staying
            # silent also prevents a native fallback for empty group text.
            if not text.strip():
                logger.debug("Zalo: ignoring empty group message in %s", chat_id)
                return

            # Dispatch group message. dm_policy is a direct-message policy, so
            # it must not gate group traffic.
            await self._dispatch_message(
                text=text,
                chat_id=chat_id,
                user_id=user_id,
                user_name=user_name,
                message_type=message_type,
                media_url=media_url,
                chat_type="group",
                message_id=message_id,
            )
            return

        # ── DM handling ─────────────────────────────────────────────────
        # DM policy gate. Unknown policies were normalized to "disabled" in
        # __init__, so a typo can never turn into an open DM bot.
        if self.dm_policy == "disabled":
            logger.debug("Zalo: DM policy disabled; ignoring message from %s", user_id)
            return

        # DM authorization. Under "pairing", unknown senders pass this
        # pre-filter and Hermes core pairing sends the pairing request; the
        # adapter never runs its own approval flow.
        if not self._passes_allowlist(user_id):
            logger.info("Zalo: ignoring DM from user %s (not allowed by dm_policy); add this ID to allowed users", user_id)
            return

        # Store an allowed DM before slash-command handling so a summary is
        # complete. Slash commands are control traffic and are not stored.
        if parse_slash_command(text) is None and self._dm_history_allowed(user_id):
            self._record_history(
                chat_id=chat_id,
                chat_type="dm",
                direction="in",
                sender_id=user_id,
                sender_name=user_name,
                message_type=history_type,
                text=text,
                message_id=message_id,
                media_url=history_media_url,
            )

        # ── Slash command handling ──────────────────────────────
        # Works in both group and DM (owner/admin control from DM)
        slash = parse_slash_command(text)
        if slash:
            await self._handle_slash_command(
                slash, chat_id, user_id, user_name, message_type, media_url, chat_type
            )
            return

        # ── Anti-spam check for group messages ──────────────────
        if is_group:
            spam = self._anti_spam.check(chat_id, user_id, text)
            if spam["spam"]:
                logger.debug("Zalo: spam blocked for %s in group %s", user_id, chat_id)
                return

        # Dispatch
        await self._dispatch_message(
            text=text,
            chat_id=chat_id,
            user_id=user_id,
            user_name=user_name,
            message_type=message_type,
            media_url=media_url,
            chat_type=chat_type,
            message_id=message_id,
        )

    async def _is_live_group_admin(self, chat_id: str, user_id: str) -> bool:
        """Fail closed: the Zalo Bot API has no group role lookup.

        Verified against the live API on 2026-11-11: every group endpoint
        returns ``404 Not Found`` and bots cannot be group admins. Admin-only
        local commands therefore stay denied; there is no allowlist fallback.
        """
        return False

    async def _handle_slash_command(
        self,
        slash: Dict[str, Any],
        chat_id: str,
        user_id: str,
        user_name: str,
        message_type: MessageType,
        media_url: Optional[str],
        chat_type: str = "dm",
    ) -> None:
        """Handle a slash command in a group chat."""
        cmd = slash["command"]
        args = slash["args"]
        spec = slash["spec"]

        # Adapter-handled commands never reach Hermes' gateway authorization,
        # so gate them here: only allowlisted users (or allow_all) may run them.
        if not self._is_user_authorized(user_id):
            logger.debug("Zalo: ignoring /%s from unauthorized user %s", cmd, user_id)
            return

        # Admin-only commands must be backed by a live platform owner/admin
        # role. A failed lookup, malformed response, missing chat type, or
        # roster miss all deny; never fall back to the allowlist.
        if spec["admin_only"]:
            if not await self._is_live_group_admin(chat_id, user_id):
                await self._send_text(chat_id, "❌ Bạn không có quyền thực hiện lệnh này.")
                return

        try:
            if cmd == "/menu":
                await self._send_text(chat_id, format_menu())
            elif cmd == "/rules":
                await self._send_text(chat_id, format_rules())
            elif cmd == "/huong-dan":
                await self._send_text(chat_id, format_help())
            elif cmd == "/silent":
                settings = self._group_settings.get(chat_id)
                new_val = not settings.get("silent", False)
                self._group_settings.set(chat_id, "silent", new_val)
                status = "BẬT" if new_val else "TẮT"
                await self._send_text(chat_id, f"🔕 Silent mode {status} — {'chỉ reply khi @tag/gọi tên' if new_val else 'reply tất cả'}")
            elif cmd == "/welcome":
                settings = self._group_settings.get(chat_id)
                new_val = not settings.get("welcome", False)
                self._group_settings.set(chat_id, "welcome", new_val)
                status = "BẬT" if new_val else "TẮT"
                await self._send_text(chat_id, f"🎉 Welcome message {status}")
            elif cmd == "/follow":
                settings = self._group_settings.get(chat_id)
                new_val = not settings.get("follow", True)
                self._group_settings.set(chat_id, "follow", new_val)
                status = "BẬT" if new_val else "TẮT"
                await self._send_text(chat_id, f"📋 Lưu lịch sử trò chuyện nhóm {status}")
            elif cmd == "/welcome-text":
                text = args.strip()
                if not text:
                    await self._send_text(chat_id, "⚠️ Dùng: `/welcome-text <nội dung>`")
                    return
                self._group_settings.set(chat_id, "welcome_text", text)
                await self._send_text(chat_id, "✅ Đã cập nhật welcome text")
            elif cmd == "/name-trigger":
                parts = args.strip().split(None, 1)
                if len(parts) < 2:
                    await self._send_text(chat_id, "⚠️ Dùng: `/name-trigger <tên> <reply>`")
                    return
                name = parts[0]
                reply = parts[1]
                triggers = self._group_settings.get(chat_id).get("name_triggers", [])
                triggers.append({"name": name, "reply": reply})
                self._group_settings.set(chat_id, "name_triggers", triggers)
                await self._send_text(chat_id, f"✅ Đã thêm trigger: @{name} → {reply[:50]}")
            elif cmd == "/settings":
                settings = self._group_settings.get(chat_id)
                lines = [
                    "⚙️ **Cài đặt nhóm:**",
                    f"  🔇 Mute: {'BẬT' if settings.get('muted') else 'TẮT'}",
                    f"  🔕 Silent: {'BẬT' if settings.get('silent') else 'TẮT'}",
                    f"  🎉 Welcome: {'BẬT' if settings.get('welcome') else 'TẮT'}",
                    f"  📋 Follow: {'BẬT' if settings.get('follow', True) else 'TẮT'}",
                ]
                await self._send_text(chat_id, "\n".join(lines))
            else:
                await self._send_text(chat_id, f"❓ Không biết lệnh `{cmd}`. Dùng `/menu` để xem danh sách.")
        except Exception as e:
            logger.error("Zalo: slash command error: %s", e)
            await self._send_text(chat_id, f"❌ Lỗi xử lý lệnh: {str(e)[:100]}")

    def _is_user_authorized(self, user_id: str) -> bool:
        """Check if a user may run adapter-handled commands (default deny)."""
        if self.allow_all:
            return True
        if not self.allowed_users:
            return False
        return user_id in self.allowed_users

    def _group_member_allowed(self, user_id: str) -> bool:
        """Group pre-filter for agent-bound messages.

        ``dm_policy`` only governs direct messages, so group traffic keeps the
        legacy behavior: defer to Hermes core when no adapter allowlist exists.
        """
        if self.allow_all:
            return True
        if not self.allowed_users:
            return True
        return user_id in self.allowed_users

    def _passes_allowlist(self, user_id: str) -> bool:
        """DM pre-filter for agent-bound messages.

        ``disabled`` denies every DM, ``allowlist`` requires a configured
        allowlist that contains the user, and ``pairing`` defers to Hermes'
        gateway authorization when no adapter allowlist is configured.
        """
        if self.dm_policy == "disabled":
            return False
        if self.dm_policy == "allowlist":
            return bool(self.allowed_users) and user_id in self.allowed_users
        if self.allow_all:
            return True
        if not self.allowed_users:
            return True
        return user_id in self.allowed_users

    async def _dispatch_message(
        self,
        text: str,
        chat_id: str,
        user_id: str,
        user_name: str,
        message_type: MessageType = MessageType.TEXT,
        media_url: str = None,
        chat_type: str = "dm",
        message_id: str = "",
    ) -> None:
        """Build a MessageEvent and hand it to the base class handler."""
        if not self._message_handler:
            return

        source = self.build_source(
            chat_id=chat_id,
            chat_name=chat_id if chat_type == "group" else user_name,
            chat_type=chat_type,
            user_id=user_id,
            user_name=user_name,
        )

        event = MessageEvent(
            text=text,
            message_type=message_type,
            source=source,
            message_id=str(message_id or ""),
            timestamp=datetime.now(),
        )

        await self.handle_message(event)

    # ── Sending ───────────────────────────────────────────────────────────

    async def _send_text(self, chat_id: str, text: str) -> SendResult:
        """Send a text message via the Zalo API."""
        if not self._client:
            return SendResult(success=False, error="Not connected")
        try:
            result = await self._client.send_message(chat_id, text)
            if result.get("ok"):
                msg_id = result.get("result", {}).get("message_id", "")
                self._record_outbound(chat_id, text, msg_id)
                return SendResult(success=True, message_id=msg_id)
            else:
                return SendResult(success=False, error=str(result))
        except Exception as e:
            return SendResult(success=False, error=str(e))

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """Send a text message to a Zalo chat (chunked at 2000 chars).

        Content is transformed by ``_prepare_for_zalo`` and sent with
        ``parse_mode="markdown"`` so Zalo renders bold/italic/strike,
        headings, lists and blockquotes natively (the server strips the
        markup itself). Constructs Zalo does NOT render (pipe tables,
        task-list checkboxes, ``<details>``, ``$$math$$``) are converted to
        plain text beforehand.
        """
        if not self._client:
            return SendResult(success=False, error="Not connected")

        prepared = self._prepare_for_zalo(content)
        if not prepared:
            return SendResult(success=False, error="Empty message after formatting")

        chunks = self._split_chunks(prepared)

        last_result = None
        for chunk in chunks:
            try:
                result = await self._client.send_message(chat_id, chunk, parse_mode="markdown")
                last_result = result
                await asyncio.sleep(0.3)
            except Exception as e:
                return SendResult(success=False, error=str(e))

        if last_result and last_result.get("ok"):
            msg_id = last_result.get("result", {}).get("message_id", "")
            # Store the full reply once, not one line per chunk.
            self._record_outbound(chat_id, prepared, msg_id)
            return SendResult(success=True, message_id=msg_id)
        return SendResult(success=False, error=str(last_result or "Send failed"))

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        """Send typing indicator via the Zalo API."""
        if not self._client:
            return
        try:
            await self._client.send_chat_action(chat_id, "typing")
        except Exception:
            pass

    async def send_image(self, chat_id: str, image_url: str, caption: str = "") -> SendResult:
        """Send an image message (caption rendered via parse_mode markdown)."""
        if not self._client:
            return SendResult(success=False, error="Not connected")
        try:
            prepared_caption = self._prepare_for_zalo(caption) if caption else ""
            result = await self._client.send_photo(
                chat_id,
                image_url,
                prepared_caption,
                parse_mode="markdown" if prepared_caption else None,
            )
            if result.get("ok"):
                msg_id = result.get("result", {}).get("message_id", "")
                self._record_outbound(
                    chat_id,
                    prepared_caption,
                    msg_id,
                    message_type="image",
                    media_url=str(image_url or ""),
                )
                return SendResult(success=True, message_id=msg_id)
            return SendResult(success=False, error=str(result))
        except Exception as e:
            return SendResult(success=False, error=str(e))

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """Get chat info."""
        return {
            "name": chat_id,
            "type": "dm",
        }

    # ── Rich-text formatting for parse_mode=markdown ──────────────────────

    # Spans that Zalo's server-side markdown parser renders natively and
    # that our stray-marker cleanup must never touch.
    _KEEP_SPAN_RE = re.compile(r"(\*\*\*.+?\*\*\*|\*\*.+?\*\*|~~.+?~~|`[^`\n]+`)")

    @staticmethod
    def _prepare_for_zalo(text: str) -> str:
        """Transform agent markdown into the subset Zalo renders with parse_mode="markdown".

        Zalo's sendMessage accepts ``parse_mode: "markdown"`` — the server
        parses the markup, strips it from the displayed text, and applies the
        style (live-verified 2026-10-12, https://docs.zaloplatforms.com/docs/BOT/apis/sendMessage).

        PRESERVED (rendered natively): ``**bold**``/``__bold__``,
        ``*italic*``/``_italic_``, ``***bold italic***``, ``~~strike~~``,
        inline `` `code` `` (kept literal), ``#``–``####`` headings,
        ``-``/``*``/``+`` bullets, ``1.`` ordered lists, ``>`` blockquotes,
        indentation, ``{red}``-style color tags, ``{big}``, ``{underline}``.

        CONVERTED TO PLAIN (accepted by the API but NOT rendered — verified
        by operator screenshot 2026-10-12: raw markup leaks to the client):
        pipe tables (rows joined with " — "), GFM task-list checkboxes
        (``- [ ] x`` -> ``- x``), ``<details>``/``<summary>`` tags (inner
        text kept), ``$$math$$`` / ``\\[..\\]`` (delimiters stripped,
        expression text kept), ``#####``+ deep headings (-> bold).

        Links/images keep the old plain conversion: ``[t](u)`` -> ``t (u)``,
        ``![a](u)`` -> ``u``. Stray ``*``/``_`` runs of 3+ outside a matched
        emphasis pair are removed so unpaired dividers don't leak raw markup
        now that markers are no longer blanket-stripped.

        NEVER leaves raw ``|`` table rows, ``$$``, ``<details>`` or ``[ ]``
        checkboxes in the output.
        """
        # Images: ![alt](url) -> url (before any emphasis handling so URLs
        # with _ or - are not mis-parsed).
        text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"\2", text)
        # Links: [text](url) -> text (url)
        text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
        # <details>/<summary> (optionally with attributes): drop the tags,
        # keep the inner text — Zalo renders the raw tags as literal text.
        # Replace with "\n", not "": stripping to nothing glues the summary
        # label to the body ("Bỏ quaNội dung ẩn"). The blank-line collapse at
        # the end of this function squashes any excess newlines. (t_ded994c7)
        text = re.sub(r"</?(?:details|summary)\b[^>]*>", "\n", text, flags=re.IGNORECASE)
        # Math: $$..$$ and \[..] -> keep the expression, strip delimiters.
        text = re.sub(r"\$\$(.+?)\$\$", r"\1", text, flags=re.DOTALL)
        text = re.sub(r"\\\[([\s\S]+?)\\\]", r"\1", text)
        # GFM task lists: "- [ ] x" / "* [x] x" / "1. [x] x" -> "- x" —
        # the checkbox brackets otherwise show as raw text.
        text = re.sub(
            r"^([ \t]*(?:[-*+]|\d+[.)])[ \t]+)\[[ xXXx]\][ \t]+",
            r"\1",
            text,
            flags=re.MULTILINE,
        )
        # Headings deeper than #### (Zalo documents up to h4, and requires
        # space+content after the marker) -> bold text. Handles no-space
        # forms like "#####Tiêu đề" so the raw run can never leak.
        text = re.sub(r"^#{5,}[ \t]*(.+?)[ \t]*$", r"**\1**", text, flags=re.MULTILINE)
        # Tables: drop separator rows (|---|---|), then join data rows into
        # "cell — cell" lines (same conversion the old stripper used).
        text = re.sub(r"^[ \t]*\|[\s:|-]+\|[ \t]*$", "", text, flags=re.MULTILINE)

        def _fmt_table(m):
            inner = m.group(0).strip().strip("|")
            cells = [c.strip() for c in inner.split("|") if c.strip()]
            return " — ".join(cells)

        text = re.sub(r"^[ \t]*\|.*\|[ \t]*$", _fmt_table, text, flags=re.MULTILINE)
        # Horizontal-rule-only lines (---, ***, ___, and spaced variants like
        # "* * *"): Zalo renders them literally, so swap in a plain divider.
        text = re.sub(
            r"^[ \t]*(?:(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})[ \t]*$",
            "———", text, flags=re.MULTILINE,
        )
        # Plus runs: `+` is a Zalo bullet marker only as "+ <content>".
        # A line of 3+ pluses ("++++++", "+ + +") is junk -> plain divider.
        text = re.sub(r"^[ \t]*(?:\+[ \t]*){3,}[ \t]*$", "———", text, flags=re.MULTILINE)
        # "++ item"/"+++item" (run of pluses before content) is not a valid
        # marker -> collapse to a single "+ " bullet.
        text = re.sub(r"^([ \t]*)\+{2,}[ \t]*(\S.*)$", r"\1+ \2", text, flags=re.MULTILINE)
        # Stray emphasis cleanup: collapse runs of 3+ * or _ that sit OUTSIDE
        # a matched bold/strike/code span (those spans are kept intact), plus
        # empty ~~ pairs ("~~~~") and bare 3+ | runs the table pass missed.
        # Single/double markers outside pairs are left alone — conservative,
        # per spec; over-eager stripping mangles legit emphasis. Load-bearing:
        # customers used to SEE "++++++" / "****" / "#####" raw in chat, so
        # no marker run may ever reach the client as literal characters.
        # Stray marker normalization (t_ded994c7 FIX-2): Zalo renders only
        # 2-star bold, 3-star bold-italic and 2-tilde strike; any run of 4+
        # '*' or 3+ '~' is malformed and must never reach the client. Such
        # runs are replaced by a sentinel BEFORE the protected-span split —
        # _KEEP_SPAN_RE otherwise matches only *part* of the run (e.g.
        # '****bold****' -> keeps '****bold**') and the leftover escapes
        # cleanup. Sentinels are stripped at the end, leaving clean text.
        # Runs inside `code` spans are masked out first so raw content like
        # `code **** raw` stays verbatim.
        code_parts = re.split(r"(`[^`\n]+`)", text)
        for i in range(0, len(code_parts), 2):  # even = outside code spans
            code_parts[i] = re.sub(r"\*{4,}", "\x01", code_parts[i])
            code_parts[i] = re.sub(r"~{3,}", "\x02", code_parts[i])
        text = "".join(code_parts)
        parts = ZaloAdapter._KEEP_SPAN_RE.split(text)
        for i in range(0, len(parts), 2):  # even indices = unprotected text
            parts[i] = re.sub(r"[*_]{3,}", "", parts[i])
            parts[i] = re.sub(r"~{2,}", "", parts[i])
            parts[i] = re.sub(r"\|{3,}", "", parts[i])
            # 3+ pluses are never a valid "+ <content>" bullet anywhere in a
            # line, and 5+ hashes exceed the documented #..#### heading
            # range — drop both so no raw run can ever reach the client.
            parts[i] = re.sub(r"\+{3,}", "", parts[i])
            parts[i] = re.sub(r"#{5,}", "", parts[i])
        text = "".join(parts)
        # Drop the FIX-2 sentinels (malformed 4+ '*' / 3+ '~' runs).
        text = text.replace("\x01", "").replace("\x02", "")
        # Collapse 3+ blank lines to a single blank line, trim edges.
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def _rebalance_bold_spans(chunks: List[str]) -> List[str]:
        """Drop stray ``**``/``***`` bold markers that cannot close inside
        their own chunk, so every chunk satisfies the send-time invariants:
        ``len <= MAX_MESSAGE_LENGTH``, an even ``**`` count, and no raw 3+
        star run outside the constructs ``_prepare_for_zalo`` protects.

        A bold span can straddle a chunk boundary (a multi-line span cut
        between its lines, or a hard split of an over-long line). v2.2.1
        tried to *insert* a closing ``**`` at the end of the straddling
        chunk and reopen the span at the start of the next; review
        t_bbb53fdf round 2 proved that blind insertion (a) fuses with
        markers already adjacent to the boundary into raw ``****`` /
        ``*****`` runs shipped to the client — the exact garbage class this
        plugin exists to eliminate — (b) could push a chunk past the 2000
        cap, and (c) cascades: one stray marker re-opens a span that every
        following chunk inherits, bolding half a 13k-char message.

        v2.2.2 (t_155aaa0d FIX-3a/3b/3c, reviewer-recommended option 1)
        instead DROPS the stray marker ("stray → plain"). An unclosed span
        renders literally on Zalo anyway, so dropping it is a visual no-op.

        Implementation: per chunk, loop on masked views of the raw string
        that preserve length (code spans — and, for the run check, completed
        ``***bold-italic***`` spans, the two constructs ``_prepare_for_zalo``
        is approved to preserve verbatim — are replaced by equal-length
        spaces), so offsets map 1:1 back onto ``raw`` and no character ever
        moves. Each iteration either (1) finds an odd number of
        non-overlapping ``**`` pairs in the code-masked view — the last pair
        has no closer, its span cannot close inside this message, so delete
        it — or (2) deletes every 3+ star run visible outside the masked
        constructs, then re-enters to re-verify parity (a run of 3 stars
        pairs as ``**`` + stray ``*``, so scrubbing can flip parity; the
        loop absorbs that). Deletions only shrink the chunk (cap holds),
        touch no sibling (no cascade). Every iteration removes ≥1 character,
        so the loop terminates by construction — no round budget, no
        fail-open bailout — and the exit state is exactly the invariant:
        even ``**`` pairs and no ``\\*{3,}`` run in the visible text.
        (Single-marker ``*italic*``, code spans, and completed BI spans are
        never altered.)
        """
        code_span = re.compile(r"`[^`\n]+`")
        bi_span = re.compile(r"\*\*\*.+?\*\*\*")

        def _mask(text: str, pat: re.Pattern) -> str:
            return pat.sub(lambda mm: " " * len(mm.group(0)), text)

        out: List[str] = []
        for raw in chunks:
            while True:
                vis = _mask(raw, code_span)  # code spans → spaces, 1:1
                # Greedy non-overlapping '**' pairing (str.count semantics).
                last_pair = -1
                n_pairs = 0
                i = vis.find("**")
                while i >= 0:
                    n_pairs += 1
                    last_pair = i
                    i = vis.find("**", i + 2)
                if n_pairs % 2:
                    raw = raw[:last_pair] + raw[last_pair + 2:]
                    continue
                w = _mask(vis, bi_span)  # completed BI spans → spaces
                runs = [mm.span() for mm in re.finditer(r"\*{3,}", w)]
                if not runs:
                    break
                for a, b in reversed(runs):
                    raw = raw[:a] + raw[b:]
            out.append(raw)
        return [x for x in out if x.strip()]

    @staticmethod
    def _split_chunks(text: str) -> List[str]:
        """Split prepared text into <=2000-char chunks on paragraph boundaries.

        Splits greedily at blank-line paragraph boundaries, then at line
        boundaries. A ``**bold**`` span can still straddle a boundary (a
        multi-line span cut between its lines, or a hard split of an
        over-long line); ``_rebalance_bold_spans`` then drops the stray
        marker so every chunk handed to the API satisfies the send-time
        invariants (cap, even ``**``, no raw star run). Single-chunk
        messages route through the same rebalancer — the invariants gate
        EVERY payload, not only split ones (v2.2.2; a stray/unclosed
        construct in a short message would otherwise ship raw).
        (t_ded994c7 FIX-3, t_155aaa0d v2.2.2)
        """
        cap = MAX_MESSAGE_LENGTH
        if len(text) <= cap:
            return ZaloAdapter._rebalance_bold_spans([text])

        def _hard_split(line: str) -> List[str]:
            """Last resort for one over-long line: cut at the nearest space."""
            out: List[str] = []
            while len(line) > cap:
                cut = line.rfind(" ", 0, cap)
                if cut <= 0:
                    cut = cap  # no whitespace: unavoidable mid-token cut
                out.append(line[:cut].rstrip())
                line = line[cut:].lstrip()
            if line:
                out.append(line)
            return out

        chunks: List[str] = []
        current = ""
        for para in re.split(r"\n\s*\n", text):
            para = para.strip("\n")
            if not para:
                continue
            candidate = f"{current}\n\n{para}" if current else para
            if len(candidate) <= cap:
                current = candidate
                continue
            if current:
                chunks.append(current)
                current = ""
            # Paragraph doesn't fit alone: pack its lines, split long lines.
            for piece in para.split("\n"):
                if len(piece) > cap:
                    if current:
                        chunks.append(current)
                        current = ""
                    chunks.extend(_hard_split(piece))
                    continue
                if current and len(current) + len(piece) + 1 > cap:
                    chunks.append(current)
                    current = piece
                else:
                    current = f"{current}\n{piece}" if current else piece
        if current:
            chunks.append(current)
        return ZaloAdapter._rebalance_bold_spans([c for c in chunks if c.strip()])


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------



def _resolve_zalo_secret(name: str, default: str = "") -> str:
    """Read a Zalo env var honoring the multiplex profile secret scope first.

    Under a multiplexed gateway the profile's .env is NOT loaded into os.environ
    (per-profile credential isolation), so plain os.getenv() sees nothing and the
    adapter never passes check_requirements. agent.secret_scope.get_secret()
    resolves profile-scoped secrets; fall back to os.getenv for standalone
    gateways (launcher loads .env into the process env there). 2026-09-30.
    """
    val = ""
    try:
        from agent.secret_scope import get_secret as _scope_get
        val = _scope_get(name) or ""
    except Exception:
        val = ""
    return (val or os.getenv(name) or default).strip()

def check_requirements() -> bool:
    """Check if Zalo is configured."""
    token = _resolve_zalo_secret("ZALO_BOT_TOKEN")
    return bool(token)


def validate_config(config) -> bool:
    """Validate that the platform config has enough info to connect."""
    extra = getattr(config, "extra", {}) or {}
    token = _resolve_zalo_secret("ZALO_BOT_TOKEN", extra.get("bot_token", ""))
    return bool(token and token.strip())


def is_connected(config) -> bool:
    """Check whether Zalo is configured (env or config.yaml)."""
    extra = getattr(config, "extra", {}) or {}
    token = _resolve_zalo_secret("ZALO_BOT_TOKEN", extra.get("bot_token", ""))
    return bool(token and token.strip())


def _env_enablement() -> dict | None:
    """Seed PlatformConfig.extra from env vars during gateway config load."""
    token = _resolve_zalo_secret("ZALO_BOT_TOKEN")
    if not token:
        return None
    seed: dict = {
        "bot_token": token,
    }
    home = os.getenv("ZALO_BOT_HOME_CHANNEL") or ""
    if home:
        seed["home_channel"] = {
            "chat_id": home,
            "name": os.getenv("ZALO_BOT_HOME_CHANNEL_NAME", home),
        }
    return seed


async def _standalone_send(
    pconfig,
    chat_id: str,
    message: str,
    *,
    thread_id: Optional[str] = None,
    media_files: Optional[List[str]] = None,
    force_document: bool = False,
) -> Dict[str, Any]:
    """Send a message via an ephemeral Zalo API client.

    Used for cron delivery when the gateway is not running in-process.
    """
    extra = getattr(pconfig, "extra", {}) or {}
    token = _resolve_zalo_secret("ZALO_BOT_TOKEN", extra.get("bot_token", ""))
    if not token:
        return {"error": "Zalo standalone send: ZALO_BOT_TOKEN must be configured"}

    client = _ZaloClient(token.strip())
    try:
        # Send media if provided
        if media_files:
            for media_url in media_files[:1]:  # only send first media
                caption = message[:MAX_MESSAGE_LENGTH] if message else ""
                result = await client.send_photo(chat_id, media_url, caption)
                if result.get("ok"):
                    return {"success": True, "message_id": result.get("result", {}).get("message_id", "")}

        # Send text
        result = await client.send_message(chat_id, message[:MAX_MESSAGE_LENGTH])
        if result.get("ok"):
            return {"success": True, "message_id": result.get("result", {}).get("message_id", "")}
        return {"error": str(result)}
    except Exception as e:
        return {"error": f"Zalo standalone send failed: {e}"}
    finally:
        await client.close()


_STANDALONE_API_ACTIONS = frozenset(
    {"get_me", "send_photo", "send_sticker", "send_voice"}
)


async def _standalone_api(pconfig, action: str, **kwargs) -> Dict[str, Any]:
    """Run one allowlisted Bot API action outside the gateway process.

    Used by the Runtime channel tools for read-only status and media sends.
    Returns ``{"success": True, ...}`` or a content-free ``{"error": code}``;
    it never falls back to a text send. Message text, URLs, and the bot token
    are never echoed in an error.
    """
    if action not in _STANDALONE_API_ACTIONS:
        return {"error": "unsupported_action"}
    extra = getattr(pconfig, "extra", {}) or {}
    token = _resolve_zalo_secret("ZALO_BOT_TOKEN", extra.get("bot_token", ""))
    if not token:
        return {"error": "config_missing"}

    client = _ZaloClient(token.strip())
    try:
        if action == "get_me":
            result = await client.get_me()
            if result.get("ok"):
                return {
                    "success": True,
                    "result": _redact_secret_data(result.get("result"), token),
                }
            return {"error": "auth_failed"}
        if action == "send_photo":
            result = await client.send_photo(
                str(kwargs.get("chat_id") or ""),
                str(kwargs.get("photo_url") or ""),
                str(kwargs.get("caption") or "")[:MAX_MESSAGE_LENGTH],
            )
        elif action == "send_sticker":
            result = await client.send_sticker(
                str(kwargs.get("chat_id") or ""),
                str(kwargs.get("sticker") or ""),
            )
        else:  # send_voice
            result = await client.send_voice(
                str(kwargs.get("chat_id") or ""),
                str(kwargs.get("voice_url") or ""),
            )
        if result.get("ok"):
            return {
                "success": True,
                "message_id": result.get("result", {}).get("message_id", ""),
            }
        return {"error": "send_failed"}
    except Exception:
        # Exception text can embed the token-bearing request URL.
        return {"error": "api_error"}
    finally:
        await client.close()


def parse_slash_command(text: str) -> Optional[Dict[str, Any]]:
    """Parse a slash command from message text. Returns command dict or None."""
    text = text.strip()
    if not text.startswith("/"):
        return None
    parts = text.split(None, 1)
    command = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""
    if command not in SLASH_COMMANDS:
        return None
    return {"command": command, "args": args.strip(), "spec": SLASH_COMMANDS[command]}


def format_menu() -> str:
    """Format the command menu for display."""
    lines = ["📋 **Menu lệnh Zalo Bot:**\n"]
    for cmd, spec in SLASH_COMMANDS.items():
        visibility = "🔒 Admin" if spec["admin_only"] else "🌐 Mọi người"
        lines.append(f"  `{spec['usage']}` — {spec['description']} ({visibility})")
    return "\n".join(lines)


def format_rules() -> str:
    """Format default group rules."""
    return (
        "📜 **Quy tắc nhóm:**\n\n"
        "1. 🗣️ Lịch sự, không spam hay xúc phạm\n"
        "2. 🚫 Không chia sẻ link đáng ngờ\n"
        "3. 📌 Tuân thủ nội quy đã được thiết lập\n"
        "4. 💬 Sử dụng `/menu` để xem danh sách lệnh\n"
        "5. 🛡️ Vi phạm sẽ bị cảnh báo, 3 lần → xử lý\n\n"
        "_Quy tắc này được bot Zalo tự động thực thi._"
    )


def format_help() -> str:
    """Format help text."""
    return (
        "🤖 **Hướng dẫn sử dụng Zalo Bot:**\n\n"
        "Bot hỗ trợ các lệnh slash để quản lý nhóm:\n"
        "• `/menu` — Xem menu lệnh\n"
        "• `/rules` — Xem quy tắc nhóm\n"
        "• `/huong-dan` — Hướng dẫn chi tiết\n\n"
        "Admin còn có thêm:\n"
        "• `/warn @user <lí do>` — Cảnh báo\n\n"
        "Nhắn trực tiếp cho bot để bắt đầu cuộc trò chuyện."
    )


def register(ctx):
    """Plugin entry point: called by the Hermes plugin system."""
    ctx.register_platform(
        name="zalo_bot",
        label="Zalo Bot",
        adapter_factory=lambda cfg: ZaloAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        is_connected=is_connected,
        required_env=["ZALO_BOT_TOKEN"],
        install_hint="No extra packages needed (uses httpx, already a Hermes dependency)",
        env_enablement_fn=_env_enablement,
        cron_deliver_env_var="ZALO_BOT_HOME_CHANNEL",
        standalone_sender_fn=_standalone_send,
        allowed_users_env="ZALO_BOT_ALLOWED_USERS",
        allow_all_env="ZALO_BOT_ALLOW_ALL_USERS",
        max_message_length=MAX_MESSAGE_LENGTH,
        emoji="💬",
        pii_safe=False,
        allow_update_command=True,
        platform_hint=(
            "You are chatting via Zalo. Zalo renders markdown natively: "
            "bold, italic, strikethrough, headings, lists, blockquotes, "
            "inline code. Tables, task lists, and math are NOT supported — "
            "convert tables to plain lines before answering. "
            "Messages are limited to 2000 characters per message "
            "(long messages are automatically split). "
            "Keep responses concise and conversational. "
            "The user is likely Vietnamese — respond in Vietnamese when appropriate."
        ),
    )
