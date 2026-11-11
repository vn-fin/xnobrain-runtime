"""Hermes Zalo delivery and local channel discovery for Runtime tools."""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping

ZALO_PLATFORM = "zalo"
ZALO_BOT_PLATFORM = "zalo_bot"
DEFAULT_MAX_MESSAGE_LENGTH = 2000
BRIDGE_PORT_BASE = 3110
MAX_KNOWN_NAMES = 20
_THREAD_ID = re.compile(r"^[0-9]{1,32}$")
_ZALO_BOT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_THREAD_ID_PATTERNS = {
    ZALO_PLATFORM: _THREAD_ID,
    ZALO_BOT_PLATFORM: _ZALO_BOT_ID,
}
_PLATFORM_READY: dict[str, bool] = {}
BRIDGE_STATUS = {
    "connecting": "starting",
    "needs_login": "needs_login",
    "qr": "qr",
    "connected": "connected",
    "closed": "closed",
}


class ChannelDeliveryError(Exception):
    """Safe, content-free delivery failure for the model-facing tool."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def normalize_name(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def normalize_recipient(value: Any) -> str:
    text = str(value or "").strip()
    if text.casefold().startswith(f"{ZALO_PLATFORM}:"):
        text = text.split(":", 1)[1].strip()
    return text


def _history_senders(history: list[Mapping[str, Any]]) -> dict[str, dict[str, str]]:
    senders: dict[str, dict[str, str]] = {}
    for record in reversed(history):
        thread_id = str(record.get("threadId") or "").strip()
        name = str(record.get("senderName") or "").strip()
        if thread_id and name and thread_id not in senders:
            senders[thread_id] = {"thread_id": thread_id, "name": name, "type": "dm"}
    return senders


def _directory_senders(directory: list[Mapping[str, Any]]) -> dict[str, dict[str, str]]:
    senders: dict[str, dict[str, str]] = {}
    for entry in directory:
        thread_id = str(entry.get("id") or "").strip()
        name = str(entry.get("name") or "").strip()
        if thread_id and name:
            senders[thread_id] = {
                "thread_id": thread_id,
                "name": name,
                "type": str(entry.get("type") or "dm"),
            }
    return senders


def known_senders(
    history: list[Mapping[str, Any]], directory: list[Mapping[str, Any]]
) -> dict[str, dict[str, str]]:
    """Merge history and channel-directory senders, keyed by thread id."""
    merged = _history_senders(history)
    for thread_id, entry in _directory_senders(directory).items():
        merged.setdefault(thread_id, entry)
    return merged


def resolve_recipient(
    history: list[Mapping[str, Any]],
    directory: list[Mapping[str, Any]],
    recipient: Any,
) -> dict[str, Any]:
    """Resolve an exact thread id or an exact (case-insensitive) sender name."""
    raw = normalize_recipient(recipient)
    if not raw:
        return {"status": "not_found", "known_names": [], "candidates": []}
    senders = known_senders(history, directory)
    named = [item for item in senders.values() if normalize_name(item.get("name"))]
    if raw in senders:
        return {**senders[raw], "status": "ok"}
    if _THREAD_ID.fullmatch(raw):
        return {"status": "ok", "thread_id": raw, "name": "", "type": "dm"}
    needle = normalize_name(raw)
    matches = [item for item in named if normalize_name(item.get("name")) == needle]
    if len(matches) == 1:
        return {**matches[0], "status": "ok"}
    if len(matches) > 1:
        return {
            "status": "ambiguous",
            "candidates": sorted(matches, key=lambda item: item["thread_id"]),
        }
    names = sorted({item["name"] for item in named})
    return {
        "status": "not_found",
        "known_names": names[:MAX_KNOWN_NAMES],
        "candidates": [],
    }


def zalo_plugin_registered() -> bool:
    """True when Hermes loaded the bundled Zalo platform plugin."""
    return platform_plugin_registered(ZALO_PLATFORM)


def platform_plugin_registered(platform: str) -> bool:
    """True when Hermes loaded the bundled plugin for one channel platform."""
    name = str(platform or "").strip().lower()
    if name not in _THREAD_ID_PATTERNS:
        return False
    if _PLATFORM_READY.get(name):
        return True
    try:
        from tools.send_message_tool import prepare_send_message_platforms

        prepare_send_message_platforms()
        from gateway.platform_registry import platform_registry

        _PLATFORM_READY[name] = platform_registry.get(name) is not None
    except Exception:
        _PLATFORM_READY[name] = False
    return bool(_PLATFORM_READY[name])


def max_message_length(platform: str = ZALO_PLATFORM) -> int:
    try:
        from gateway.platform_registry import platform_registry

        entry = platform_registry.get(str(platform or ZALO_PLATFORM))
        if entry is not None and int(entry.max_message_length or 0) > 0:
            return int(entry.max_message_length)
    except Exception:
        pass
    return DEFAULT_MAX_MESSAGE_LENGTH


def _fallback_profile_index(profile: Path, profiles_root: Path, root_profile: Path) -> int:
    try:
        active = profile.resolve()
        root = root_profile.resolve()
        profiles = profiles_root.resolve()
    except OSError:
        return 0
    if active == root or active.parent != profiles:
        return 0
    try:
        names = sorted(entry.name for entry in profiles.iterdir() if entry.is_dir())
    except OSError:
        return 0
    try:
        return names.index(active.name)
    except ValueError:
        return 0


def bridge_port(
    profile: Path,
    *,
    profiles_root: Path,
    root_profile: Path,
    configured: Any = None,
) -> int:
    """Resolve the loopback bridge port with the bundled plugin's own rule."""
    try:
        from plugins.platforms.zalo import adapter as plugin

        index = plugin._profile_index(
            Path(profile),
            str(profiles_root),
            str(root_profile),
        )
        return int(plugin._coerce_port(configured, plugin.BRIDGE_PORT_BASE + index))
    except Exception:
        pass
    index = _fallback_profile_index(Path(profile), Path(profiles_root), Path(root_profile))
    try:
        port = int(configured)
    except (TypeError, ValueError):
        port = BRIDGE_PORT_BASE + index
    if port < 1 or port > 65535 or port == 3000:
        port = BRIDGE_PORT_BASE + index
    return port


def bridge_status(value: Any) -> str:
    """Map a bridge ``/health`` status to the public channel status contract."""
    return BRIDGE_STATUS.get(str(value or "").strip().lower(), "closed")


def _bridge_request(
    port: int,
    path: str,
    *,
    method: str = "GET",
    timeout: float = 1.0,
) -> dict[str, Any] | None:
    if not isinstance(port, int) or port < 1 or port > 65535 or port == 3000:
        return None
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        method=method,
        data=b"{}" if method == "POST" else None,
        headers={"Content-Type": "application/json"} if method == "POST" else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 0))
            if not 200 <= status < 300:
                return None
            payload = response.read(65536)
    except (OSError, urllib.error.URLError, ValueError):
        return None
    try:
        value = json.loads(payload)
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def bridge_health(port: int, *, timeout: float = 1.0) -> dict[str, Any] | None:
    return _bridge_request(port, "/health", timeout=timeout)


def bridge_start_login(port: int, *, timeout: float = 2.0) -> dict[str, Any] | None:
    return _bridge_request(port, "/login", method="POST", timeout=timeout)


def profile_enables_platform(profile: Path, platform: str) -> bool:
    """Read ``platforms.<platform>.enabled`` from the profile config."""
    name = str(platform or "").strip().lower()
    try:
        import yaml

        value = yaml.safe_load((Path(profile) / "config.yaml").read_text(encoding="utf-8"))
        platforms = value.get("platforms") if isinstance(value, dict) else None
        record = platforms.get(name) if isinstance(platforms, dict) else None
        return bool(isinstance(record, dict) and record.get("enabled"))
    except (OSError, ValueError, TypeError):
        return False


def profile_enables_zalo(profile: Path) -> bool:
    """Read ``platforms.zalo.enabled`` from the profile config (plan 015 D4)."""
    return profile_enables_platform(profile, ZALO_PLATFORM)


async def send_text(
    thread_id: str,
    text: str,
    platform: str = ZALO_PLATFORM,
) -> dict[str, Any]:
    """Send text through Hermes' plugin standalone sender, not raw bridge HTTP."""
    name = str(platform or "").strip().lower()
    pattern = _THREAD_ID_PATTERNS.get(name)
    if pattern is None:
        raise ChannelDeliveryError("channel_platform_unavailable")
    if not pattern.fullmatch(str(thread_id or "")):
        raise ChannelDeliveryError("channel_recipient_invalid")
    limit = max_message_length(name)
    if len(text) > limit:
        raise ChannelDeliveryError("channel_message_too_long")
    if not text.strip():
        raise ChannelDeliveryError("channel_message_empty")
    try:
        from tools.send_message_tool import _send_to_platform, prepare_send_message_platforms

        prepare_send_message_platforms()
        from gateway.config import Platform, load_gateway_config
        from gateway.platform_registry import platform_registry

        if platform_registry.get(name) is None:
            raise ChannelDeliveryError("channel_platform_unavailable")
        target = Platform(name)
        config = load_gateway_config()
        pconfig = None
        for key, value in dict(getattr(config, "platforms", {}) or {}).items():
            if getattr(key, "value", str(key)) == name:
                pconfig = value
                break
        if pconfig is None or not getattr(pconfig, "enabled", False):
            raise ChannelDeliveryError("channel_platform_not_configured")
        result = await _send_to_platform(target, pconfig, str(thread_id), text)
    except ChannelDeliveryError:
        raise
    except Exception as error:
        raise ChannelDeliveryError("channel_send_failed") from error
    if not isinstance(result, dict) or result.get("error") or not result.get("success"):
        raise ChannelDeliveryError("channel_send_failed")
    message_id = result.get("message_id")
    return {
        "message_id": str(message_id) if message_id not in (None, "") else None,
    }


_BOT_MEDIA_ACTIONS = {
    "photo": "send_photo",
    "sticker": "send_sticker",
    "voice": "send_voice",
}


def _bot_media_payload(kind: str, value: str, caption: str) -> dict[str, Any]:
    if kind == "photo":
        return {"photo_url": value, "caption": caption}
    if kind == "sticker":
        return {"sticker": value}
    return {"voice_url": value}


async def send_bot_media(
    chat_id: str,
    kind: str,
    url_or_id: str,
    caption: str = "",
) -> dict[str, Any]:
    """Send one Bot media message through the plugin's ``_standalone_api``.

    ``kind`` is ``photo``, ``sticker``, or ``voice``. The adapter helper keeps
    a fixed action allowlist, so this path can never fall back to text.
    """
    name = ZALO_BOT_PLATFORM
    action = _BOT_MEDIA_ACTIONS.get(str(kind or "").strip().lower())
    if action is None:
        raise ChannelDeliveryError("channel_media_unsupported")
    if not _THREAD_ID_PATTERNS[name].fullmatch(str(chat_id or "")):
        raise ChannelDeliveryError("channel_recipient_invalid")
    try:
        from tools.send_message_tool import prepare_send_message_platforms

        prepare_send_message_platforms()
        from gateway.config import load_gateway_config
        from gateway.platform_registry import platform_registry

        entry = platform_registry.get(name)
        if entry is None:
            raise ChannelDeliveryError("channel_platform_unavailable")
        sender = getattr(entry, "standalone_sender_fn", None)
        module = sys.modules.get(getattr(sender, "__module__", ""))
        helper = getattr(module, "_standalone_api", None)
        if not callable(helper):
            raise ChannelDeliveryError("channel_platform_unavailable")
        config = load_gateway_config()
        pconfig = None
        for key, value in dict(getattr(config, "platforms", {}) or {}).items():
            if getattr(key, "value", str(key)) == name:
                pconfig = value
                break
        if pconfig is None or not getattr(pconfig, "enabled", False):
            raise ChannelDeliveryError("channel_platform_not_configured")
        result = await helper(
            pconfig,
            action,
            **_bot_media_payload(str(kind).strip().lower(), str(url_or_id or ""), caption),
        )
    except ChannelDeliveryError:
        raise
    except Exception as error:
        raise ChannelDeliveryError("channel_send_failed") from error
    if not isinstance(result, dict) or result.get("error") or not result.get("success"):
        raise ChannelDeliveryError("channel_send_failed")
    message_id = result.get("message_id")
    return {
        "message_id": str(message_id) if message_id not in (None, "") else None,
    }
