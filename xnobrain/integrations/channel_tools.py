"""Run-scoped ``send_channel_message`` tool for personal messaging channels."""

from __future__ import annotations

import asyncio
import copy
import json
import threading
from contextlib import contextmanager
from pathlib import Path

from pydantic import ValidationError

from ..models.channels import SendChannelMessageRequest
from . import channel_tool_common, zalo_channels
from .zalo_channels import ChannelDeliveryError

_LOCK = threading.RLock()
_BINDINGS: dict[str, tuple] = {}
_PLATFORMS: dict[str, frozenset[str]] = {}
_SCHEMA: dict = {}


def _error(code: str, **extra) -> str:
    """Share the base envelope and keep the tool's legacy top-level extras."""
    payload = json.loads(channel_tool_common.error(code))
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


def _request_approval(run_id: str, notify_cb, approval_data: dict) -> dict:
    from tools.approval import _await_gateway_decision

    return _await_gateway_decision(
        run_id,
        notify_cb,
        approval_data,
        surface="api_server",
    )


def _deliver(
    binding: tuple,
    thread_id: str,
    name: str,
    text: str,
    platform: str = zalo_channels.ZALO_PLATFORM,
    thread_type: str = "dm",
) -> dict:
    service, agent_id, conversation_id, _run_id, _notify_cb, loop = binding
    future = asyncio.run_coroutine_threadsafe(
        service.send_message(
            agent_id,
            conversation_id,
            thread_id,
            name,
            text,
            platform=platform,
            thread_type=thread_type,
        ),
        loop,
    )
    return future.result(timeout=60)


def run_binding(session_id: str) -> tuple:
    """Return the run binding and its enabled platforms, or ``(None, None)``."""
    with _LOCK:
        key = str(session_id or "")
        return _BINDINGS.get(key), _PLATFORMS.get(key)


def register_tools() -> None:
    from tools.registry import registry

    with _LOCK:
        if not _SCHEMA:
            limit = zalo_channels.max_message_length()
            function = {
                "name": "send_channel_message",
                "description": (
                    "Send plain text on a messaging channel after the user approves it. "
                    "Platforms: 'zalo' (personal account, direct messages or approved "
                    "groups) and 'zalo_bot' (bot, direct messages or approved groups). "
                    "Pass an exact Zalo thread id or an exact contact name seen in the "
                    "agent's Zalo history; bot sends require an exact chat id. The user "
                    "must approve every send, and every group must be approved first."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "platform": {
                            "type": "string",
                            "enum": ["zalo", "zalo_bot"],
                            "default": "zalo",
                        },
                        "thread_type": {
                            "type": "string",
                            "enum": ["dm", "group"],
                            "default": "dm",
                        },
                        "recipient": {
                            "type": "string",
                            "description": "Exact Zalo thread id/chat id or exact contact name.",
                        },
                        "text": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": limit,
                            "description": f"Plain text, at most {limit} characters.",
                        },
                    },
                    "required": ["recipient", "text"],
                    "additionalProperties": False,
                },
            }
            registry.register(
                name="send_channel_message",
                toolset="xnobrain_channels",
                schema=function,
                handler=call,
                check_fn=lambda: False,
            )
            _SCHEMA.update({"type": "function", "function": function})
    from . import zalo_tools

    zalo_tools.register_tools()


@contextmanager
def bind_run(
    service,
    profile_dir: Path,
    agent_id: str,
    conversation_id: str,
    run_id: str,
    approval_notify_callback,
):
    """Expose the tool only while a run can use an enabled channel plugin."""
    from ..feature_flags import FEATURE_AGENT_CHANNELS
    from ..feature_flags import enabled as feature_enabled

    enabled_platforms: list[str] = []
    try:
        if (
            zalo_channels.profile_enables_zalo(profile_dir)
            and zalo_channels.zalo_plugin_registered()
        ):
            enabled_platforms.append(zalo_channels.ZALO_PLATFORM)
        if zalo_channels.profile_enables_platform(
            profile_dir, zalo_channels.ZALO_BOT_PLATFORM
        ) and zalo_channels.platform_plugin_registered(
            zalo_channels.ZALO_BOT_PLATFORM
        ):
            enabled_platforms.append(zalo_channels.ZALO_BOT_PLATFORM)
        enabled = bool(
            feature_enabled(FEATURE_AGENT_CHANNELS)
            and service is not None
            and enabled_platforms
        )
    except Exception:
        enabled = False
    if not enabled:
        yield False
        return
    try:
        register_tools()
    except Exception:
        yield False
        return
    binding = (
        service,
        str(agent_id),
        str(conversation_id),
        str(run_id),
        approval_notify_callback,
        asyncio.get_running_loop(),
    )
    with _LOCK:
        if conversation_id in _BINDINGS:
            yield False
            return
        _BINDINGS[str(conversation_id)] = binding
        _PLATFORMS[str(conversation_id)] = frozenset(enabled_platforms)
    try:
        yield True
    finally:
        with _LOCK:
            if _BINDINGS.get(str(conversation_id)) is binding:
                _BINDINGS.pop(str(conversation_id), None)
                _PLATFORMS.pop(str(conversation_id), None)


def install_tools(agent, session_id: str) -> None:
    with _LOCK:
        if session_id not in _BINDINGS or int(getattr(agent, "_delegate_depth", 0) or 0):
            return
        schema = copy.deepcopy(_SCHEMA)
        platforms = _PLATFORMS.get(str(session_id)) or frozenset()
    if schema:
        existing = {
            tool.get("function", {}).get("name") for tool in getattr(agent, "tools", [])
        }
        if "send_channel_message" not in existing:
            agent.tools = list(getattr(agent, "tools", []) or []) + [schema]
        agent.valid_tool_names = set(getattr(agent, "valid_tool_names", set())) | {
            "send_channel_message"
        }
    if zalo_channels.ZALO_PLATFORM in platforms:
        from . import zalo_tools

        zalo_tools.install_tools(agent, session_id)


def call(args, **kwargs):
    session_id = str(kwargs.get("session_id") or "")
    with _LOCK:
        binding = _BINDINGS.get(session_id)
        platforms = _PLATFORMS.get(session_id)
    if binding is None or platforms is None:
        return _error("channel_authority_required")
    try:
        request = SendChannelMessageRequest.model_validate(args)
    except ValidationError:
        return _error("channel_invalid_request")
    platform = request.platform
    if platform not in platforms:
        return _error("channel_platform_not_configured")
    service, agent_id, _conversation_id, run_id, notify_cb, _loop = binding
    try:
        if platform == zalo_channels.ZALO_PLATFORM:
            if request.thread_type == "group":
                resolution = service.resolve_zalo_group(agent_id, request.recipient)
            else:
                resolution = service.resolve_recipient(agent_id, request.recipient)
        else:
            resolution = service.resolve_bot_recipient(
                agent_id, request.recipient, request.thread_type
            )
    except Exception as error:
        return _error(str(getattr(error, "code", "channel_resolution_failed")))
    status = resolution.get("status")
    if platform == zalo_channels.ZALO_PLATFORM:
        if status == "ambiguous":
            return _error(
                "channel_recipient_ambiguous",
                candidates=resolution.get("candidates") or [],
            )
        if status == "group_not_approved":
            return _error("channel_group_not_approved")
        if status == "invalid":
            return _error("channel_recipient_invalid")
        if status != "ok":
            return _error(
                "channel_recipient_not_found",
                known_names=resolution.get("known_names") or [],
            )
    else:
        if status == "invalid":
            return _error("channel_recipient_invalid")
        if status == "group_not_approved":
            return _error("channel_group_not_approved")
        if status != "ok":
            return _error("channel_recipient_not_found")
    thread_id = str(resolution.get("thread_id") or "")
    name = str(resolution.get("name") or thread_id)
    limit = zalo_channels.max_message_length(platform)
    if len(request.text) > limit:
        return _error("channel_message_too_long")
    if platform == zalo_channels.ZALO_PLATFORM:
        group_send = request.thread_type == "group"
        label = "Zalo group" if group_send else "Zalo"
        pattern_key = "zalo.send_group_message" if group_send else "zalo.send_message"
    else:
        label = "Zalo Bot"
        pattern_key = "zalo_bot.send_message"
    preview = request.text if len(request.text) <= 300 else request.text[:300] + "…"
    preview = " ".join(preview.split())
    decision = _request_approval(
        run_id,
        notify_cb,
        {
            "command": f"{label} → {name}: {preview}",
            "description": f"Send this message on {label}?",
            "pattern_key": pattern_key,
            "pattern_keys": [pattern_key],
            "allow_permanent": False,
            "allow_session": False,
        },
    )
    choice = str(decision.get("choice") or "")
    if not decision.get("resolved") or choice not in {"once", "always"}:
        return _error("channel_send_denied")
    try:
        result = _deliver(
            binding,
            thread_id,
            name,
            request.text,
            platform=platform,
            thread_type=request.thread_type,
        )
    except ChannelDeliveryError as error:
        return _error(error.code)
    except Exception:
        return _error("channel_send_failed")
    return json.dumps(
        {
            "success": True,
            "data": {
                "thread_id": thread_id,
                "name": name,
                "message_id": result.get("message_id"),
            },
        },
        ensure_ascii=False,
    )
