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
BRIDGE_TOKEN_FILE = "bridge.token"
MAX_KNOWN_NAMES = 20
_MAX_BRIDGE_RESPONSE_BYTES = 65536
_THREAD_ID = re.compile(r"^[0-9]{1,32}$")
_ZALO_BOT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_BRIDGE_TOKEN = re.compile(r"^[0-9a-fA-F]{64}$")
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


def bridge_token(session_dir: Path) -> str | None:
    """Read the bridge bearer token, refusing an unsafe or malformed file.

    The bridge regenerates this token at every start, so callers read it on
    every request and never cache it.
    """
    path = Path(session_dir) / BRIDGE_TOKEN_FILE
    try:
        if path.is_symlink() or not path.is_file():
            return None
        value = path.read_text(encoding="utf-8", errors="replace")[:4096].strip()
    except (OSError, ValueError):
        return None
    return value if _BRIDGE_TOKEN.fullmatch(value) else None


def _bridge_error_name(value: Any) -> str:
    if isinstance(value, dict):
        code = str(value.get("error") or "").strip()
        if code:
            return code
    return "http_error"


def _bridge_http_error(status: Any, error: Any) -> dict[str, Any]:
    payload = b""
    try:
        payload = error.read(65536)
    except Exception:
        pass
    try:
        value = json.loads(payload)
    except (ValueError, TypeError):
        value = None
    result = {
        "ok": False,
        "status": int(status or 0),
        "data": None,
        "error": _bridge_error_name(value),
    }
    return result


def _bridge_request(
    port: int,
    path: str,
    *,
    method: str = "GET",
    timeout: float = 1.0,
    token: str | None = None,
    body: Mapping[str, Any] | None = None,
    max_bytes: int = _MAX_BRIDGE_RESPONSE_BYTES,
) -> dict[str, Any]:
    """Call one bridge endpoint and return a content-free structured result.

    ``{"ok", "status", "data", "error"}`` lets callers map an HTTP failure to
    a stable channel error without exposing the bridge body.
    """
    if not isinstance(port, int) or port < 1 or port > 65535 or port == 3000:
        return {
            "ok": False,
            "status": 0,
            "data": None,
            "error": "bridge_unavailable",
        }
    payload_bytes: bytes | None = b"{}" if method == "POST" else None
    headers = {"Content-Type": "application/json"} if method == "POST" else {}
    if body is not None:
        try:
            payload_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError):
            return {
                "ok": False,
                "status": 0,
                "data": None,
                "error": "invalid_request",
            }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        method=method,
        data=payload_bytes,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 0))
            payload = response.read(max(1, int(max_bytes)))
    except urllib.error.HTTPError as error:
        return _bridge_http_error(getattr(error, "code", 0), error)
    except (OSError, urllib.error.URLError, ValueError):
        return {
            "ok": False,
            "status": 0,
            "data": None,
            "error": "unreachable",
        }
    try:
        value = json.loads(payload)
    except (ValueError, TypeError):
        value = None
    if not 200 <= status < 300:
        return {
            "ok": False,
            "status": status,
            "data": None,
            "error": _bridge_error_name(value),
        }
    if not isinstance(value, dict):
        return {
            "ok": False,
            "status": status,
            "data": None,
            "error": "invalid_response",
        }
    return {"ok": True, "status": status, "data": value, "error": None}


def bridge_request(
    port: int,
    path: str,
    *,
    method: str = "GET",
    timeout: float = 1.0,
    token: str | None = None,
    body: Mapping[str, Any] | None = None,
    max_bytes: int = _MAX_BRIDGE_RESPONSE_BYTES,
) -> dict[str, Any]:
    """Public structured bridge call used by Runtime services."""
    return _bridge_request(
        port,
        path,
        method=method,
        timeout=timeout,
        token=token,
        body=body,
        max_bytes=max_bytes,
    )


def bridge_error_code(result: Any) -> str:
    """Map one bridge failure to the shared channel tool error contract."""
    if not isinstance(result, Mapping) or result.get("ok"):
        return ""
    status = result.get("status")
    code = status if isinstance(status, int) and not isinstance(status, bool) else 0
    error = str(result.get("error") or "")
    if error == "not_connected" or code == 503:
        return "channel_not_connected"
    if error in {"unauthorized", "no_token"}:
        return "channel_bridge_unauthorized"
    named = {
        "unsupported": "channel_media_unsupported",
        "not_own_message": "channel_message_not_own",
        "too_old": "channel_message_too_old",
        "recipient_not_found": "channel_recipient_not_found",
        "group_not_found": "channel_group_not_found",
        "user_not_found": "channel_user_not_found",
        "label_not_found": "channel_label_not_found",
        "alias_not_found": "channel_alias_not_found",
    }.get(error)
    if named is not None:
        return named
    return {
        401: "channel_bridge_unauthorized",
        404: "channel_message_not_found",
        429: "channel_rate_limited",
    }.get(code, "channel_bridge_failed")


def bridge_health(port: int, *, timeout: float = 1.0) -> dict[str, Any] | None:
    result = _bridge_request(port, "/health", timeout=timeout)
    return result.get("data") if result.get("ok") else None


def bridge_start_login(
    port: int,
    *,
    token: str | None = None,
    timeout: float = 2.0,
) -> dict[str, Any] | None:
    result = _bridge_request(port, "/login", method="POST", token=token, timeout=timeout)
    return result.get("data") if result.get("ok") else None


def bridge_send_text(
    port: int,
    token: str,
    thread_id: str,
    text: str,
    *,
    thread_type: str = "group",
    quote_message_id: str | None = None,
    mentions: list[str] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Send text through the authenticated bridge with an explicit thread type."""
    if not _THREAD_ID.fullmatch(str(thread_id or "")):
        raise ChannelDeliveryError("channel_recipient_invalid")
    if thread_type not in {"user", "group"}:
        raise ChannelDeliveryError("channel_recipient_invalid")
    body: dict[str, Any] = {
        "chatId": str(thread_id),
        "threadType": thread_type,
        "message": text,
    }
    if quote_message_id:
        if not _THREAD_ID.fullmatch(str(quote_message_id)):
            raise ChannelDeliveryError("channel_recipient_invalid")
        body["quoteMessageId"] = str(quote_message_id)
    if mentions:
        if thread_type != "group":
            raise ChannelDeliveryError("channel_recipient_invalid")
        ids = [str(item) for item in mentions]
        if not ids or len(ids) > 20 or not all(_THREAD_ID.fullmatch(item) for item in ids):
            raise ChannelDeliveryError("channel_recipient_invalid")
        body["mentions"] = ids
    result = _bridge_request(
        port,
        "/send",
        method="POST",
        token=token,
        timeout=timeout,
        body=body,
    )
    if not result.get("ok"):
        raise ChannelDeliveryError(bridge_error_code(result))
    data = result.get("data") or {}
    message_id = data.get("messageId")
    return {
        "message_id": str(message_id) if message_id not in (None, "") else None,
    }


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
