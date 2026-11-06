"""Hermes Zalo delivery and local channel discovery for Runtime tools."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

ZALO_PLATFORM = "zalo"
DEFAULT_MAX_MESSAGE_LENGTH = 2000
MAX_KNOWN_NAMES = 20
_THREAD_ID = re.compile(r"^[0-9]{1,32}$")
_PLUGIN_READY: bool | None = None


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
    global _PLUGIN_READY
    if _PLUGIN_READY:
        return True
    try:
        from tools.send_message_tool import prepare_send_message_platforms

        prepare_send_message_platforms()
        from gateway.platform_registry import platform_registry

        _PLUGIN_READY = platform_registry.get(ZALO_PLATFORM) is not None
    except Exception:
        _PLUGIN_READY = False
    return bool(_PLUGIN_READY)


def max_message_length() -> int:
    try:
        from gateway.platform_registry import platform_registry

        entry = platform_registry.get(ZALO_PLATFORM)
        if entry is not None and int(entry.max_message_length or 0) > 0:
            return int(entry.max_message_length)
    except Exception:
        pass
    return DEFAULT_MAX_MESSAGE_LENGTH


def profile_enables_zalo(profile: Path) -> bool:
    """Read ``platforms.zalo.enabled`` from the profile config (plan 015 D4)."""
    try:
        import yaml

        value = yaml.safe_load((Path(profile) / "config.yaml").read_text(encoding="utf-8"))
        platforms = value.get("platforms") if isinstance(value, dict) else None
        zalo = platforms.get(ZALO_PLATFORM) if isinstance(platforms, dict) else None
        return bool(isinstance(zalo, dict) and zalo.get("enabled"))
    except (OSError, ValueError, TypeError):
        return False


async def send_text(thread_id: str, text: str) -> dict[str, Any]:
    """Send text through Hermes' plugin standalone sender, not raw bridge HTTP."""
    if not _THREAD_ID.fullmatch(str(thread_id or "")):
        raise ChannelDeliveryError("channel_recipient_invalid")
    limit = max_message_length()
    if len(text) > limit:
        raise ChannelDeliveryError("channel_message_too_long")
    if not text.strip():
        raise ChannelDeliveryError("channel_message_empty")
    try:
        from tools.send_message_tool import _send_to_platform, prepare_send_message_platforms

        prepare_send_message_platforms()
        from gateway.config import Platform, load_gateway_config
        from gateway.platform_registry import platform_registry

        if platform_registry.get(ZALO_PLATFORM) is None:
            raise ChannelDeliveryError("channel_platform_unavailable")
        platform = Platform(ZALO_PLATFORM)
        config = load_gateway_config()
        pconfig = None
        for key, value in dict(getattr(config, "platforms", {}) or {}).items():
            if getattr(key, "value", str(key)) == ZALO_PLATFORM:
                pconfig = value
                break
        if pconfig is None or not getattr(pconfig, "enabled", False):
            raise ChannelDeliveryError("channel_platform_not_configured")
        result = await _send_to_platform(platform, pconfig, str(thread_id), text)
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
