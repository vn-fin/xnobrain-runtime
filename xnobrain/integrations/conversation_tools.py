"""Correlate embedded tool lifecycle events with their persisted call IDs."""

from __future__ import annotations

import hashlib
import threading
import time
from typing import Any, Callable

from xnobrain.integrations.skill_usage import _tool_failed


class ConversationToolCallbacks:
    """Project structured engine callbacks once, preserving progress metadata.

    The pinned executor emits a progress callback immediately before each
    structured start/complete callback, on the same thread. Keep that metadata
    per thread until the structured callback supplies the authoritative ID.
    Completion order need not match start order, even for the same tool name.
    """

    def __init__(self, progress: Callable, start: Callable, complete: Callable):
        self._progress = progress
        self._start = start
        self._complete = complete
        self._pending = threading.local()
        self._started: dict[str, float] = {}
        self._lock = threading.Lock()
        self._image_outcomes: dict[str, str] = {}

    def progress(
        self,
        event_type: str,
        tool_name: str | None = None,
        preview: str | None = None,
        args: Any = None,
        **kwargs: Any,
    ) -> None:
        if event_type in {"tool.started", "tool.completed"}:
            setattr(self._pending, event_type, (tool_name, preview, args, kwargs))
            return
        self._progress(event_type, tool_name, preview, args, **kwargs)

    def _take(self, event_type: str, tool_name: str) -> tuple | None:
        pending = getattr(self._pending, event_type, None)
        if pending is not None:
            delattr(self._pending, event_type)
        return pending if pending is not None and pending[0] == tool_name else None

    def start(self, call_id: str, tool_name: str, args: Any) -> None:
        pending = self._take("tool.started", tool_name)
        if not call_id:
            return
        with self._lock:
            if call_id in self._started:
                return
            self._started[call_id] = time.monotonic()
        if pending is None:
            from agent.display import build_tool_preview

            preview = build_tool_preview(tool_name, args or {})
        else:
            preview = pending[1]
        if tool_name == "vision_analyze":
            preview = "Inspect image"
        try:
            self._progress("tool.started", tool_name, preview, args, tool_call_id=call_id)
        finally:
            self._start(call_id, tool_name, args)

    def complete(self, call_id: str, tool_name: str, args: Any, result: Any) -> None:
        pending = self._take("tool.completed", tool_name)
        with self._lock:
            started = self._started.pop(call_id, None)
        try:
            if started is None:
                return
            metadata = dict(pending[3]) if pending is not None else {}
            metadata.setdefault("duration", max(0, time.monotonic() - started))
            metadata.setdefault("is_error", _tool_failed(result))
            metadata["result"] = (
                {"summary": result.get("text_summary", "Image loaded"), "delivery": "native"}
                if tool_name == "vision_analyze"
                and isinstance(result, dict)
                and result.get("_multimodal")
                else result
            )
            if tool_name == "vision_analyze":
                source = str((args or {}).get("image_url") or call_id)
                identity = hashlib.sha256(source.encode()).hexdigest()[:16]
                native = isinstance(result, dict) and result.get("_multimodal")
                outcome = "failed" if metadata["is_error"] else "loaded" if native else "analyzed"
                with self._lock:
                    self._image_outcomes[identity] = outcome
                    coverage = {
                        status: list(self._image_outcomes.values()).count(status)
                        for status in ("loaded", "analyzed", "failed")
                    }
                    coverage["requested"] = len(self._image_outcomes)
                # Loaded pixels are not a claim that the model has inspected them.
                metadata["image_coverage"] = coverage
            metadata["tool_call_id"] = call_id
            self._progress("tool.completed", tool_name, None, None, **metadata)
        finally:
            self._complete(call_id, tool_name, args, result)
