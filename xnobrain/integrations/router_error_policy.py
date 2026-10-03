"""Recovery policy for explicit centralized-routing failures."""

from __future__ import annotations

import threading

from .llm_router_support import LLM_ROUTER_PROVIDER, LLM_ROUTER_PROVIDER_KEY

_HOOK = "transform_api_error_classification"
_LOCK = threading.Lock()


def classify_router_error(
    *,
    provider: str = "",
    status_code: int | None = None,
    error_code: str = "",
    **_kwargs: object,
) -> dict[str, object] | None:
    """Stop a rejected route without treating a temporary outage as bad auth."""
    if provider not in {LLM_ROUTER_PROVIDER, LLM_ROUTER_PROVIDER_KEY}:
        return None
    if status_code != 503 or error_code != "no_credentials":
        return None
    return {
        "reason": "server_error",
        "retryable": False,
        "should_compress": False,
        "should_rotate_credential": False,
        "should_fallback": False,
        "message": (
            "No usable model-provider account is available. Reconnect a provider account "
            "or choose another model. (HTTP 503: no healthy credentials available)"
        ),
    }


def install_router_error_policy() -> None:
    """Register once per profile through the embedded engine's plugin API."""
    from hermes_cli.plugins import PluginContext, PluginManifest, get_plugin_manager

    manager = get_plugin_manager()
    with _LOCK:
        manager.discover_and_load()
        if classify_router_error in manager.iter_hook_callbacks(_HOOK):
            return
        context = PluginContext(
            PluginManifest(name="xnobrain-router-errors", version="1.0.0"),
            manager,
        )
        context.register_hook(_HOOK, classify_router_error)
