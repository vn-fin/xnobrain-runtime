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

TOOL_NAME = "send_channel_message"
TOOLSET = "xnobrain_channels"


def _error(code: str, **extra) -> str:
    """Share the base envelope and keep the tool's legacy top-level extras."""
    payload = json.loads(channel_tool_common.error(code))
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


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


def register_tools(profile_dir) -> None:
    """Register the tool in the profile's registry scope.

    The engine rebuilds ``agent.tools`` from the registry between turns and drops
    tools appended by hand, so the tool must live in the registry. A profile scope
    keeps it out of every other agent's tool list; unbound runs get an error result.
    """
    from hermes_constants import hermes_home_key
    from tools.registry import registry

    scope = hermes_home_key(profile_dir)
    with _LOCK:
        limit = zalo_channels.max_message_length()
        function = {
            "name": TOOL_NAME,
            "description": (
                "Send plain text on a messaging channel. Platforms: 'zalo' "
                "(personal account, direct messages or approved groups) and "
                "'zalo_bot' (bot, direct messages or approved groups). Pass an exact "
                "Zalo thread id or an exact contact name seen in the agent's Zalo "
                "history; bot sends require an exact chat id. Before calling, show "
                "the user the recipient and the exact text and wait for their "
                "explicit confirmation in chat. Every group must be approved first."
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
                    "required": ["recipient", "text"],
                    "additionalProperties": False,
                },
                "required": ["recipient", "text"],
                "additionalProperties": False,
            },
        }
        _SCHEMA.update({"type": "function", "function": function})
        if registry.get_entry(TOOL_NAME, scope=scope) is not None:
            return
        registry.register(
            name=TOOL_NAME,
            toolset=TOOLSET,
            schema=function,
            handler=call,
            description=function["description"],
            scope=scope,
        )


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


def install_tools(agent, session_id: str, profile_dir) -> None:
    with _LOCK:
        if session_id not in _BINDINGS or int(getattr(agent, "_delegate_depth", 0) or 0):
            return
    register_tools(profile_dir)
    with _LOCK:
        schema = copy.deepcopy(_SCHEMA)
    if not schema:
        return
    existing = {tool.get("function", {}).get("name") for tool in getattr(agent, "tools", [])}
    if TOOL_NAME not in existing:
        agent.tools = list(getattr(agent, "tools", []) or []) + [schema]
    agent.valid_tool_names = set(getattr(agent, "valid_tool_names", set())) | {TOOL_NAME}
    # Without the toolset the between-turns refresh filters the registry tool out.
    enabled = getattr(agent, "enabled_toolsets", None)
    if enabled is not None and TOOLSET not in enabled:
        agent.enabled_toolsets = sorted({*enabled, TOOLSET})
    with _LOCK:
        platforms = _PLATFORMS.get(str(session_id)) or frozenset()
    if zalo_channels.ZALO_PLATFORM in platforms:
        from . import zalo_tools

        zalo_tools.install_tools(agent, session_id, profile_dir)


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
    service, agent_id, conversation_id, _run_id, _notify_cb, _loop = binding
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
    origin = channel_tool_common.channel_origin(
        service.repository,
        service.profile_path(agent_id),
        conversation_id,
    )
    scope_error = channel_tool_common.ensure_thread_in_scope(
        origin, platform, thread_id
    )
    if scope_error is not None:
        return _error(scope_error)
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
