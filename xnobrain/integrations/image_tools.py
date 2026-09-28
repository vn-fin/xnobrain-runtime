"""Runtime-owned vision adapter; the pinned engine remains unchanged."""

from __future__ import annotations

import base64
import copy
import json
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote, urlparse

from ..chat_images import read_image
from ..services.base import ServiceError

_LOCK = threading.RLock()
_BINDINGS = {}


def router_image_messages(messages, model):
    """Adapt Codex image parts to the pinned GoRouter v0.2.5 wire contract.

    Its translator forwards image_url without unwrapping {url: ...}, and
    flattens tool results to text. Keep canonical history intact and project
    tool pixels into a user image message after the contiguous tool results.
    """
    if not str(model).startswith(("cx/", "codex/")):
        return messages
    result = []
    tool_images = []

    def flush_tool_images():
        if tool_images:
            result.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Images returned by the preceding tool results."},
                        *tool_images,
                    ],
                }
            )
            tool_images.clear()

    for original in messages:
        if not isinstance(original, dict):
            flush_tool_images()
            result.append(original)
            continue
        message = copy.deepcopy(original)
        if message.get("role") != "tool":
            flush_tool_images()
        content = message.get("content")
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") in {"image_url", "input_image"}:
                    source = part.get("image_url")
                    if isinstance(source, dict):
                        part["image_url"] = source.get("url", "")
                    if message.get("role") == "tool":
                        tool_images.append(part)
                        continue
                parts.append(part)
            message["content"] = (
                "Image returned for visual inspection."
                if not parts and len(parts) != len(content)
                else parts
            )
        result.append(message)
    flush_tool_images()
    return result


def _vision_support(model, config):
    from agent.image_routing import _lookup_supports_vision, _supports_vision_override

    override = _supports_vision_override(config, "custom:xnobrain", model)
    if override is not None:
        return override
    owner, separator, name = model.partition("/")
    provider = {
        "cc": "anthropic",
        "claude": "anthropic",
        "cx": "openai",
        "codex": "openai",
        "gemini": "google",
    }.get(owner, owner)
    return _lookup_supports_vision(provider, name if separator else model, {})


def local_image_source(root: Path, cwd: Path, source: str) -> str:
    """Resolve a tool-local path inside its workspace, with no global chdir."""
    if source.startswith("file://"):
        parsed = urlparse(source)
        if parsed.netloc not in {"", "localhost"}:
            raise ServiceError(
                "Image location is not permitted", status=422, code="invalid_chat_image_path"
            )
        source = unquote(parsed.path)
    path = Path(source)
    if not path.is_absolute():
        path = cwd / path
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ServiceError(
            "Image is outside this agent workspace", status=422, code="invalid_chat_image_path"
        ) from exc
    data, _extension, mime = read_image(root, relative)
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


async def inspect_image(args, binding, *, task_id=None):
    from hermes_cli.config import load_config
    from tools import terminal_tool, vision_tools

    agent, root = binding
    roots = [root, *getattr(agent, "_xnobrain_image_artifact_roots", [])]
    source = args.get("image_url", "")
    if not isinstance(source, str) or not source:
        return json.dumps({"error": "Choose an image to inspect", "code": "image_source_required"})
    try:
        config = load_config()
        if not source.startswith(("https://", "http://", "data:")):
            from agent.runtime_cwd import resolve_context_cwd

            cwd = Path(
                terminal_tool.get_session_cwd(task_id)
                or resolve_context_cwd()
                or (config.get("terminal") or {}).get("cwd")
                or root
            )
            path = (
                Path(unquote(urlparse(source).path))
                if source.startswith("file://")
                else Path(source)
            )
            target = path if path.is_absolute() else cwd / path
            approved = next(
                (candidate for candidate in roots if target.is_relative_to(candidate)), None
            )
            if approved is None:
                raise ServiceError(
                    "Image is outside the permitted artifact directories",
                    status=422,
                    code="invalid_chat_image_path",
                )
            source = local_image_source(approved, cwd, source)
        support = _vision_support(str(agent.model or ""), config)
        if support is False:
            auxiliary = (config.get("auxiliary") or {}).get("vision") or {}
            if not any(
                auxiliary.get(key) not in (None, "", "auto")
                for key in ("model", "provider", "base_url")
            ):
                return json.dumps(
                    {
                        "error": "The selected model does not support images. Select a vision model.",
                        "code": "image_model_unsupported",
                        "success": False,
                    }
                )
            # The existing client retains profile-scoped credentials and accounting.
            result = await vision_tools._handle_vision_analyze(
                {**args, "image_url": source}, task_id=task_id
            )
        else:
            # Unknown catalog entries (including new router aliases) are sent to
            # the selected model; its provider can reject them explicitly. Never
            # silently replace that model with an auxiliary service.
            result = await vision_tools._vision_analyze_native(
                source, args.get("question", ""), task_id=task_id, region=args.get("region")
            )
        if isinstance(result, dict) and result.get("_multimodal"):
            result["meta"] = {"delivery": "native", "image_loaded": True}
            result["text_summary"] = "Image loaded for visual inspection by the active model."
            return result
        parsed = json.loads(result) if isinstance(result, str) else result
        if isinstance(parsed, dict) and parsed.get("error"):
            return json.dumps(
                {
                    "error": "Image inspection failed. Check the image and selected vision route, then retry this page.",
                    "code": "image_inspection_failed",
                    "success": False,
                }
            )
        return result
    except ServiceError as exc:
        return json.dumps({"error": str(exc), "code": exc.code, "success": False})
    except Exception:
        return json.dumps(
            {
                "error": "Image inspection failed. Retry this page; it remains unverified.",
                "code": "image_inspection_failed",
                "success": False,
            }
        )


def _install_dispatch():
    from tools import vision_tools  # noqa: F401 -- register the native tool
    from tools.registry import registry

    with _LOCK:
        entry = registry.get_entry("vision_analyze")
        if getattr(entry.handler, "_xnobrain_images", False):
            return
        original = entry.handler

        async def dispatch(args, **kwargs):
            with _LOCK:
                binding = _BINDINGS.get("task:" + str(kwargs.get("task_id") or ""))
                if binding is None:
                    binding = _BINDINGS.get("session:" + str(kwargs.get("session_id") or ""))
            if binding is None:
                return await original(args, **kwargs)
            return await inspect_image(args, binding, task_id=kwargs.get("task_id"))

        dispatch._xnobrain_images = True
        entry.handler = dispatch


@contextmanager
def image_tool_scope(agent, root, task_id):
    _install_dispatch()
    keys = []
    if task_id:
        keys.append("task:" + str(task_id))
    if getattr(agent, "session_id", None):
        keys.append("session:" + str(agent.session_id))
    binding = (agent, Path(root))
    with _LOCK:
        previous = {key: _BINDINGS.get(key) for key in keys}
        for key in keys:
            _BINDINGS[key] = binding
    try:
        yield
    finally:
        with _LOCK:
            for key in keys:
                if _BINDINGS.get(key) is binding:
                    if previous[key] is None:
                        _BINDINGS.pop(key, None)
                    else:
                        _BINDINGS[key] = previous[key]


def install_image_tools(agent, root, artifact_roots=()):
    """Bind parent and delegated agent execution, including resumed turns."""
    if getattr(agent, "_xnobrain_image_root", None):
        return
    agent._xnobrain_image_root = Path(root)
    agent._xnobrain_image_artifact_roots = [Path(path) for path in artifact_roots]

    def supports_native_images():
        from hermes_cli.config import load_config

        # The engine's API-bound preprocessing checks this separately from the
        # vision tool. An unknown custom-router alias must not strip pixels and
        # silently invoke the engine's default auxiliary model.
        return _vision_support(str(agent.model or ""), load_config()) is not False

    agent._model_supports_vision = supports_native_images
    describe = getattr(agent, "_describe_image_for_anthropic_fallback", None)
    if callable(describe):

        def describe_with_explicit_auxiliary(image_url, role):
            from hermes_cli.config import load_config

            auxiliary = (load_config().get("auxiliary") or {}).get("vision") or {}
            if not any(
                auxiliary.get(key) not in (None, "", "auto")
                for key in ("model", "provider", "base_url")
            ):
                raise ServiceError(
                    "The selected model does not support images. Select a vision model.",
                    status=422,
                    code="image_model_unsupported",
                )
            return describe(image_url, role)

        agent._describe_image_for_anthropic_fallback = describe_with_explicit_auxiliary
    run = agent.run_conversation

    def run_with_images(*args, **kwargs):
        with image_tool_scope(agent, root, kwargs.get("task_id")):
            return run(*args, **kwargs)

    agent.run_conversation = run_with_images
