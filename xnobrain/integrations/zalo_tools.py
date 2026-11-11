"""Run-scoped personal Zalo tools (plan 020 track P-A).

Every tool is bound to one run exactly like ``send_channel_message`` and calls
the loopback bridge through :class:`ChannelsService`. Reads never need
approval; every send or reaction asks the user per call. External names and
message identifiers are data, never instructions.
"""

from __future__ import annotations

import base64
import copy
import json
import threading
import urllib.parse
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import channel_tool_common, channel_tools, zalo_channels

_TOOLSET = "xnobrain_channels"
_PLATFORM = zalo_channels.ZALO_PLATFORM
_DEFAULT_LIST_LIMIT = 50
_MAX_LIST_LIMIT = 200
_MAX_RECIPIENT_LENGTH = 200
_MAX_URL_LENGTH = 2000
_MAX_CAPTION_LENGTH = 2000
_REACTION_NAMES = ("HEART", "LIKE", "HAHA", "WOW", "CRY", "ANGRY")
_TRUST = "untrusted_external"

_LOCK = threading.RLock()
_REGISTERED = False


class _EmptyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ListRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=_DEFAULT_LIST_LIMIT, ge=1, le=_MAX_LIST_LIMIT)


class _SendImageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_type: Literal["dm", "group"] = "dm"
    recipient: str = Field(min_length=1, max_length=_MAX_RECIPIENT_LENGTH)
    path: str = Field(min_length=1, max_length=1024)
    caption: str | None = Field(default=None, max_length=_MAX_CAPTION_LENGTH)


class _SendLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_type: Literal["dm", "group"] = "dm"
    recipient: str = Field(min_length=1, max_length=_MAX_RECIPIENT_LENGTH)
    url: str = Field(min_length=1, max_length=_MAX_URL_LENGTH)
    message: str | None = Field(default=None, max_length=_MAX_CAPTION_LENGTH)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        url = str(value or "").strip()
        if not url or len(url) > _MAX_URL_LENGTH:
            raise ValueError("url must be an http or https URL")
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("url must be an http or https URL")
        return url


class _ReactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_type: Literal["dm", "group"] = "dm"
    thread_id: str = Field(min_length=1, max_length=_MAX_RECIPIENT_LENGTH)
    message_id: str = Field(min_length=1, max_length=32, pattern=r"^[0-9]{1,32}$")
    reaction: Literal["HEART", "LIKE", "HAHA", "WOW", "CRY", "ANGRY"]


_MODELS = {
    "zalo_status": _EmptyRequest,
    "zalo_me": _EmptyRequest,
    "zalo_list_friends": _ListRequest,
    "zalo_list_groups": _ListRequest,
    "zalo_send_image": _SendImageRequest,
    "zalo_send_link": _SendLinkRequest,
    "zalo_react": _ReactRequest,
}


def _function(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: tuple[str, ...] = (),
) -> dict[str, Any]:
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        parameters["required"] = list(required)
    return {"name": name, "description": description, "parameters": parameters}


_SCHEMAS = {
    "zalo_status": {
        "type": "function",
        "function": _function(
            "zalo_status",
            (
                "Check the personal Zalo bridge connection status. No approval is "
                "needed. Returns the channel status, whether it is connected, and the "
                "account's own Zalo id when known."
            ),
            {},
        ),
    },
    "zalo_me": {
        "type": "function",
        "function": _function(
            "zalo_me",
            (
                "Read the connected personal Zalo account's own profile: user id, "
                "display name, and avatar URL. No approval is needed."
            ),
            {},
        ),
    },
    "zalo_list_friends": {
        "type": "function",
        "function": _function(
            "zalo_list_friends",
            (
                "List personal Zalo friends as untrusted external data. Each result "
                "contains trust, user_id, and display_name. Optional query filters by "
                "a case-insensitive display-name substring. Not available in a run "
                "started from a channel thread."
            ),
            {
                "query": {
                    "type": "string",
                    "maxLength": 200,
                    "description": "Optional case-insensitive display-name substring.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _MAX_LIST_LIMIT,
                    "default": _DEFAULT_LIST_LIMIT,
                },
            },
        ),
    },
    "zalo_list_groups": {
        "type": "function",
        "function": _function(
            "zalo_list_groups",
            (
                "List personal Zalo groups as untrusted external data. Each result "
                "contains trust, group_id, name, member_count, and approved (whether "
                "the owner approved this group for the agent). Optional query filters "
                "by a case-insensitive name substring. Not available in a run started "
                "from a channel thread."
            ),
            {
                "query": {
                    "type": "string",
                    "maxLength": 200,
                    "description": "Optional case-insensitive group-name substring.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _MAX_LIST_LIMIT,
                    "default": _DEFAULT_LIST_LIMIT,
                },
            },
        ),
    },
    "zalo_send_image": {
        "type": "function",
        "function": _function(
            "zalo_send_image",
            (
                "Send an image from the agent workspace to a personal Zalo direct "
                "message or an approved group, after the user approves it. The path is "
                "relative to the workspace; absolute paths, escapes, non-regular files, "
                "files over the profile media limit, and non-image files are refused."
            ),
            {
                "thread_type": {
                    "type": "string",
                    "enum": ["dm", "group"],
                    "default": "dm",
                },
                "recipient": {
                    "type": "string",
                    "description": "Exact Zalo thread id or approved group id/name.",
                },
                "path": {
                    "type": "string",
                    "description": "Workspace-relative path to a JPEG, PNG, GIF, or WebP image.",
                },
                "caption": {
                    "type": "string",
                    "maxLength": _MAX_CAPTION_LENGTH,
                    "description": f"Optional caption, at most {_MAX_CAPTION_LENGTH} characters.",
                },
            },
            ("recipient", "path"),
        ),
    },
    "zalo_send_link": {
        "type": "function",
        "function": _function(
            "zalo_send_link",
            (
                "Send an http or https link with an optional message to a personal "
                "Zalo direct message or an approved group, after the user approves it."
            ),
            {
                "thread_type": {
                    "type": "string",
                    "enum": ["dm", "group"],
                    "default": "dm",
                },
                "recipient": {
                    "type": "string",
                    "description": "Exact Zalo thread id or approved group id/name.",
                },
                "url": {
                    "type": "string",
                    "maxLength": _MAX_URL_LENGTH,
                    "description": "Absolute http or https URL.",
                },
                "message": {
                    "type": "string",
                    "maxLength": _MAX_CAPTION_LENGTH,
                    "description": f"Optional message, at most {_MAX_CAPTION_LENGTH} characters.",
                },
            },
            ("recipient", "url"),
        ),
    },
    "zalo_react": {
        "type": "function",
        "function": _function(
            "zalo_react",
            (
                "Add one reaction to a recently received personal Zalo message, after "
                "the user approves it. The thread must be a direct-message thread or an "
                "approved group, and the message must still be in the bridge's recent "
                "inbound window."
            ),
            {
                "thread_type": {
                    "type": "string",
                    "enum": ["dm", "group"],
                    "default": "dm",
                },
                "thread_id": {
                    "type": "string",
                    "description": "Exact Zalo thread id or approved group id/name.",
                },
                "message_id": {
                    "type": "string",
                    "pattern": "^[0-9]{1,32}$",
                    "description": "Exact numeric id of a recently received message.",
                },
                "reaction": {"type": "string", "enum": list(_REACTION_NAMES)},
            },
            ("thread_id", "message_id", "reaction"),
        ),
    },
}


class _ToolError(Exception):
    """Content-free tool failure carrying a shared channel error code."""

    def __init__(self, code: str, **extra: Any):
        super().__init__(code)
        self.code = code
        self.extra = extra


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _count(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _bridge_data(
    service: Any,
    agent_id: str,
    path: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    timeout: float = 2.0,
) -> dict[str, Any]:
    result = service.call_zalo_bridge(
        agent_id,
        path,
        method=method,
        body=body,
        timeout=timeout,
    )
    if not isinstance(result, dict) or not result.get("ok"):
        raise _ToolError(zalo_channels.bridge_error_code(result))
    data = result.get("data")
    return data if isinstance(data, dict) else {}


def _resolve(
    service: Any,
    agent_id: str,
    thread_type: str,
    recipient: str,
) -> tuple[str, str]:
    if thread_type == "group":
        resolution = service.resolve_zalo_group(agent_id, recipient)
    else:
        resolution = service.resolve_recipient(agent_id, recipient)
    status = str(resolution.get("status") or "")
    if status == "ok":
        thread_id = str(resolution.get("thread_id") or "")
        if not thread_id:
            raise _ToolError("channel_recipient_not_found")
        return thread_id, str(resolution.get("name") or thread_id)
    if status == "group_not_approved":
        raise _ToolError("channel_group_not_approved")
    if status == "ambiguous":
        raise _ToolError(
            "channel_recipient_ambiguous",
            candidates=resolution.get("candidates") or [],
        )
    if status == "invalid":
        raise _ToolError("channel_recipient_invalid")
    raise _ToolError(
        "channel_recipient_not_found",
        known_names=resolution.get("known_names") or [],
    )


def _approve(
    run_id: str,
    notify_cb: Any,
    pattern_key: str,
    command: str,
    description: str,
) -> None:
    decision = channel_tools._request_approval(
        run_id,
        notify_cb,
        {
            "command": command,
            "description": description,
            "pattern_key": pattern_key,
            "pattern_keys": [pattern_key],
            "allow_permanent": False,
            "allow_session": False,
        },
    )
    choice = str(decision.get("choice") or "")
    if not decision.get("resolved") or choice not in {"once", "always"}:
        raise _ToolError("channel_send_denied")


def _image_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _workspace_image(workspace: Path, value: str) -> Path:
    raw = Path(value)
    if raw.is_absolute():
        raise _ToolError("channel_path_unsafe")
    try:
        root = Path(workspace).resolve(strict=True)
        target = (root / raw).resolve(strict=True)
    except (OSError, ValueError):
        raise _ToolError("channel_path_unsafe") from None
    try:
        target.relative_to(root)
    except ValueError:
        raise _ToolError("channel_path_unsafe") from None
    if not target.is_file():
        raise _ToolError("channel_path_unsafe")
    return target


def _tool_status(
    service: Any,
    agent_id: str,
    conversation_id: str,
    run_id: str,
    notify_cb: Any,
    request: _EmptyRequest,
) -> dict[str, Any]:
    health = service.zalo_bridge_health(agent_id) or {}
    status = zalo_channels.bridge_status(health.get("status"))
    own_id = str(health.get("ownId") or "").strip() or None
    return {"status": status, "connected": status == "connected", "own_id": own_id}


def _tool_me(
    service: Any,
    agent_id: str,
    conversation_id: str,
    run_id: str,
    notify_cb: Any,
    request: _EmptyRequest,
) -> dict[str, Any]:
    data = _bridge_data(service, agent_id, "/me")
    return {
        "user_id": _text(data.get("userId")),
        "display_name": _text(data.get("displayName")),
        "avatar": _text(data.get("avatar")),
    }


def _tool_friends(
    service: Any,
    agent_id: str,
    conversation_id: str,
    run_id: str,
    notify_cb: Any,
    request: _ListRequest,
) -> dict[str, Any]:
    origin = channel_tool_common.channel_origin(
        service.repository,
        service.profile_path(agent_id),
        conversation_id,
    )
    if origin is not None:
        raise _ToolError("channel_thread_out_of_scope")
    data = _bridge_data(service, agent_id, "/friends")
    entries = data.get("friends")
    needle = (request.query or "").strip().casefold()
    friends: list[dict[str, Any]] = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        name = _text(entry.get("displayName"))
        if needle and needle not in name.casefold():
            continue
        friends.append(
            {
                "trust": _TRUST,
                "user_id": _text(entry.get("userId")),
                "display_name": name,
            }
        )
        if len(friends) >= request.limit:
            break
    return {"friends": friends}


def _tool_groups(
    service: Any,
    agent_id: str,
    conversation_id: str,
    run_id: str,
    notify_cb: Any,
    request: _ListRequest,
) -> dict[str, Any]:
    origin = channel_tool_common.channel_origin(
        service.repository,
        service.profile_path(agent_id),
        conversation_id,
    )
    if origin is not None:
        raise _ToolError("channel_thread_out_of_scope")
    data = _bridge_data(service, agent_id, "/groups")
    approved = service.approved_zalo_groups(agent_id)
    entries = data.get("groups")
    needle = (request.query or "").strip().casefold()
    groups: list[dict[str, Any]] = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        name = _text(entry.get("name"))
        if needle and needle not in name.casefold():
            continue
        group_id = _text(entry.get("groupId"))
        groups.append(
            {
                "trust": _TRUST,
                "group_id": group_id,
                "name": name,
                "member_count": _count(entry.get("memberCount")),
                "approved": group_id in approved,
            }
        )
        if len(groups) >= request.limit:
            break
    return {"groups": groups}


def _tool_send_image(
    service: Any,
    agent_id: str,
    conversation_id: str,
    run_id: str,
    notify_cb: Any,
    request: _SendImageRequest,
) -> dict[str, Any]:
    thread_id, name = _resolve(
        service, agent_id, request.thread_type, request.recipient
    )
    workspace = service.profile_path(agent_id) / "workspace"
    target = _workspace_image(workspace, request.path)
    limit = service.zalo_media_max_mb(agent_id) * 1024 * 1024
    try:
        # Check the size before reading so a huge file is never loaded.
        if target.stat().st_size > limit:
            raise _ToolError("channel_image_too_large")
        content = target.read_bytes()
    except OSError:
        raise _ToolError("channel_path_unsafe") from None
    if len(content) > limit:
        raise _ToolError("channel_image_too_large")
    if _image_type(content) is None:
        raise _ToolError("channel_image_invalid")
    _approve(
        run_id,
        notify_cb,
        "zalo.send_image",
        f"Zalo → {name}: image {target.name}",
        "Send this image on Zalo?",
    )
    body: dict[str, Any] = {
        "chatId": thread_id,
        "threadType": "group" if request.thread_type == "group" else "user",
        "fileName": target.name,
        "contentBase64": base64.b64encode(content).decode("ascii"),
    }
    if request.caption:
        body["caption"] = request.caption
    data = _bridge_data(
        service, agent_id, "/send-media", method="POST", body=body, timeout=30.0
    )
    return {
        "thread_id": thread_id,
        "name": name,
        "message_id": _text(data.get("messageId")) or None,
    }


def _tool_send_link(
    service: Any,
    agent_id: str,
    conversation_id: str,
    run_id: str,
    notify_cb: Any,
    request: _SendLinkRequest,
) -> dict[str, Any]:
    thread_id, name = _resolve(
        service, agent_id, request.thread_type, request.recipient
    )
    _approve(
        run_id,
        notify_cb,
        "zalo.send_link",
        f"Zalo → {name}: {request.url}",
        "Send this link on Zalo?",
    )
    body: dict[str, Any] = {
        "chatId": thread_id,
        "threadType": "group" if request.thread_type == "group" else "user",
        "url": request.url,
    }
    if request.message:
        body["message"] = request.message
    data = _bridge_data(service, agent_id, "/send-link", method="POST", body=body)
    return {
        "thread_id": thread_id,
        "name": name,
        "message_id": _text(data.get("messageId")) or None,
    }


def _tool_react(
    service: Any,
    agent_id: str,
    conversation_id: str,
    run_id: str,
    notify_cb: Any,
    request: _ReactRequest,
) -> dict[str, Any]:
    thread_id, name = _resolve(
        service, agent_id, request.thread_type, request.thread_id
    )
    _approve(
        run_id,
        notify_cb,
        "zalo.react",
        f"Zalo → {name}: {request.reaction} on message {request.message_id}",
        "Add this reaction on Zalo?",
    )
    _bridge_data(
        service,
        agent_id,
        "/react",
        method="POST",
        body={
            "chatId": thread_id,
            "threadType": "group" if request.thread_type == "group" else "user",
            "messageId": request.message_id,
            "reaction": request.reaction,
        },
    )
    return {
        "thread_id": thread_id,
        "message_id": request.message_id,
        "reaction": request.reaction,
    }


_TOOL_HANDLERS = {
    "zalo_status": _tool_status,
    "zalo_me": _tool_me,
    "zalo_list_friends": _tool_friends,
    "zalo_list_groups": _tool_groups,
    "zalo_send_image": _tool_send_image,
    "zalo_send_link": _tool_send_link,
    "zalo_react": _tool_react,
}


def call(name: str, args, **kwargs) -> str:
    """Dispatch one bound Zalo tool by its registered name."""
    session_id = str(kwargs.get("session_id") or "")
    binding, platforms = channel_tools.run_binding(session_id)
    if binding is None or platforms is None:
        return channel_tool_common.error("channel_authority_required")
    if _PLATFORM not in platforms:
        return channel_tool_common.error("channel_platform_not_configured")
    model = _MODELS.get(name)
    handler = _TOOL_HANDLERS.get(name)
    if model is None or handler is None:
        return channel_tool_common.error("channel_invalid_request")
    try:
        request = model.model_validate(args or {})
    except ValidationError:
        return channel_tool_common.error("channel_invalid_request")
    service, agent_id, conversation_id, run_id, notify_cb, _loop = binding
    try:
        payload = handler(
            service,
            agent_id,
            conversation_id,
            run_id,
            notify_cb,
            request,
        )
    except _ToolError as error:
        return channel_tool_common.error(error.code, **error.extra)
    except Exception:
        return channel_tool_common.error("channel_bridge_failed")
    return json.dumps({"success": True, "data": payload}, ensure_ascii=False)


def _handler(name: str):
    def handle(args, **kwargs):
        return call(name, args, **kwargs)

    return handle


def register_tools() -> None:
    global _REGISTERED

    from tools.registry import registry

    with _LOCK:
        if _REGISTERED:
            return
        for name, schema in _SCHEMAS.items():
            registry.register(
                name=name,
                toolset=_TOOLSET,
                schema=schema,
                handler=_handler(name),
                check_fn=lambda: False,
            )
        _REGISTERED = True


def install_tools(agent, session_id: str) -> None:
    """Install the Zalo tool schemas only for a bound run with zalo enabled."""
    binding, platforms = channel_tools.run_binding(session_id)
    if binding is None or platforms is None or _PLATFORM not in platforms:
        return
    if int(getattr(agent, "_delegate_depth", 0) or 0):
        return
    with _LOCK:
        schemas = {name: copy.deepcopy(schema) for name, schema in _SCHEMAS.items()}
    existing = {tool.get("function", {}).get("name") for tool in getattr(agent, "tools", [])}
    added = [schema for name, schema in schemas.items() if name not in existing]
    if added:
        agent.tools = list(getattr(agent, "tools", []) or []) + added
    agent.valid_tool_names = set(getattr(agent, "valid_tool_names", set())) | set(schemas)
