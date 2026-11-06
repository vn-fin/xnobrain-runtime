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
from . import zalo_channels
from .zalo_channels import ChannelDeliveryError

_LOCK = threading.RLock()
_BINDINGS: dict[str, tuple] = {}
_SCHEMA: dict = {}


def _error(code: str, **extra) -> str:
    payload = {"success": False, "error": {"code": code}}
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


def _deliver(binding: tuple, thread_id: str, name: str, text: str) -> dict:
    service, agent_id, conversation_id, _run_id, _notify_cb, loop = binding
    future = asyncio.run_coroutine_threadsafe(
        service.send_message(agent_id, conversation_id, thread_id, name, text),
        loop,
    )
    return future.result(timeout=60)


def register_tools() -> None:
    from tools.registry import registry

    with _LOCK:
        if _SCHEMA:
            return
        limit = zalo_channels.max_message_length()
        function = {
            "name": "send_channel_message",
            "description": (
                "Send plain text on a personal messaging channel after the user approves it. "
                "Platform 'zalo' only. Pass an exact Zalo thread id or an exact contact name "
                "seen in the agent's Zalo history. The user must approve every send. "
                "After success, replies from that contact are recorded in this conversation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "platform": {
                        "type": "string",
                        "enum": ["zalo"],
                        "default": "zalo",
                    },
                    "recipient": {
                        "type": "string",
                        "description": "Exact Zalo thread id or exact contact name.",
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


@contextmanager
def bind_run(
    service,
    profile_dir: Path,
    agent_id: str,
    conversation_id: str,
    run_id: str,
    approval_notify_callback,
):
    """Expose the tool only while a run can use an enabled Zalo plugin."""
    try:
        enabled = (
            service is not None
            and zalo_channels.zalo_plugin_registered()
            and zalo_channels.profile_enables_zalo(profile_dir)
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
    try:
        yield True
    finally:
        with _LOCK:
            if _BINDINGS.get(str(conversation_id)) is binding:
                _BINDINGS.pop(str(conversation_id), None)


def install_tools(agent, session_id: str) -> None:
    with _LOCK:
        if session_id not in _BINDINGS or int(getattr(agent, "_delegate_depth", 0) or 0):
            return
        schema = copy.deepcopy(_SCHEMA)
    if not schema:
        return
    existing = {tool.get("function", {}).get("name") for tool in getattr(agent, "tools", [])}
    if "send_channel_message" not in existing:
        agent.tools = list(getattr(agent, "tools", []) or []) + [schema]
    agent.valid_tool_names = set(getattr(agent, "valid_tool_names", set())) | {
        "send_channel_message"
    }


def call(args, **kwargs):
    session_id = str(kwargs.get("session_id") or "")
    with _LOCK:
        binding = _BINDINGS.get(session_id)
    if binding is None:
        return _error("channel_authority_required")
    try:
        request = SendChannelMessageRequest.model_validate(args)
    except ValidationError:
        return _error("channel_invalid_request")
    service, agent_id, _conversation_id, run_id, notify_cb, _loop = binding
    try:
        resolution = service.resolve_recipient(agent_id, request.recipient)
    except Exception as error:
        return _error(str(getattr(error, "code", "channel_resolution_failed")))
    if resolution.get("status") == "ambiguous":
        return _error(
            "channel_recipient_ambiguous",
            candidates=resolution.get("candidates") or [],
        )
    if resolution.get("status") != "ok":
        return _error(
            "channel_recipient_not_found",
            known_names=resolution.get("known_names") or [],
        )
    thread_id = str(resolution.get("thread_id") or "")
    name = str(resolution.get("name") or thread_id)
    limit = zalo_channels.max_message_length()
    if len(request.text) > limit:
        return _error("channel_message_too_long")
    preview = request.text if len(request.text) <= 300 else request.text[:300] + "…"
    preview = " ".join(preview.split())
    decision = _request_approval(
        run_id,
        notify_cb,
        {
            "command": f"Zalo → {name}: {preview}",
            "description": "Send this message on Zalo?",
            "pattern_key": "zalo.send_message",
            "pattern_keys": ["zalo.send_message"],
            "allow_permanent": False,
            "allow_session": False,
        },
    )
    choice = str(decision.get("choice") or "")
    if not decision.get("resolved") or choice not in {"once", "always"}:
        return _error("channel_send_denied")
    try:
        result = _deliver(binding, thread_id, name, request.text)
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
