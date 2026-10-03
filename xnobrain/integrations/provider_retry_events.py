"""Expose safe retry progress without changing the embedded engine's policy."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from .llm_router_support import LLM_ROUTER_PROVIDER, LLM_ROUTER_PROVIDER_KEY

_HOOK = "api_request_error"
_LOCK = threading.Lock()
_OBSERVER: ContextVar[tuple[str, Callable[..., None]] | None] = ContextVar(
    "xnobrain_provider_retry_observer", default=None
)


def retry_event_fields(*, status_code: object = None, **_kwargs: object) -> dict[str, int]:
    """Only an HTTP status may cross the stream boundary; never raw errors."""
    if type(status_code) is int and 400 <= status_code <= 599:
        return {"status_code": status_code}
    return {}


def _on_api_error(
    *,
    session_id: str = "",
    provider: str = "",
    model: str = "",
    retryable: bool = False,
    retry_count: int | None = None,
    max_retries: int | None = None,
    status_code: int | None = None,
    **_kwargs: object,
) -> None:
    observer = _OBSERVER.get()
    if (
        observer is None
        or not session_id
        or observer[0] != session_id
        or provider not in {LLM_ROUTER_PROVIDER, LLM_ROUTER_PROVIDER_KEY}
        or not model.startswith("ag/")
        or retryable is not True
        or type(retry_count) is not int
        or type(max_retries) is not int
        or not 0 <= retry_count < max_retries - 1
    ):
        return
    observer[1]("provider.retrying", **retry_event_fields(status_code=status_code))


@contextmanager
def observe_provider_retries(session_id: str, callback: Callable[..., None]) -> Iterator[None]:
    """Bind lifecycle events to this conversation worker, including cleanup."""
    from hermes_cli.plugins import PluginContext, PluginManifest, get_plugin_manager

    manager = get_plugin_manager()
    with _LOCK:
        manager.discover_and_load()
        if _on_api_error not in manager.iter_hook_callbacks(_HOOK):
            context = PluginContext(
                PluginManifest(name="xnobrain-provider-retries", version="1.0.0"),
                manager,
            )
            context.register_hook(_HOOK, _on_api_error)
    token = _OBSERVER.set((session_id, callback))
    try:
        yield
    finally:
        _OBSERVER.reset(token)
