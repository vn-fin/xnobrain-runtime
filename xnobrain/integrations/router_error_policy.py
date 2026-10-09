"""Recovery policy for explicit centralized-routing failures."""

from __future__ import annotations

import threading

from .llm_router_support import LLM_ROUTER_PROVIDER, LLM_ROUTER_PROVIDER_KEY

_HOOK = "transform_api_error_classification"
_LOCK = threading.Lock()
PROVIDER_MAX_RETRIES = 5


def install_provider_retry_progress(agent, progress) -> None:
    """Expose bounded retries through this agent's run-scoped callback."""
    if getattr(agent, "_xnobrain_retry_progress", None) is not None:
        return
    original = getattr(agent, "_invoke_api_request_error_hook", None)
    if not callable(original):
        return
    # The engine counts the initial request as an attempt.
    agent._api_max_retries = PROVIDER_MAX_RETRIES + 1
    # Transport recovery otherwise starts a second full retry cycle.
    agent._try_recover_primary_transport = lambda *_args, **_kwargs: False
    agent._xnobrain_retry_progress = progress

    def report_error(**kwargs):
        original(**kwargs)
        attempt = kwargs.get("retry_count")
        if (
            kwargs.get("retryable") is True
            and isinstance(attempt, int)
            and 0 <= attempt < PROVIDER_MAX_RETRIES
        ):
            status = kwargs.get("status_code")
            progress(
                "provider.retrying",
                status_code=status if isinstance(status, int) and 400 <= status <= 599 else None,
                retry_attempt=attempt + 1,
                max_retries=PROVIDER_MAX_RETRIES,
            )

    agent._invoke_api_request_error_hook = report_error


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
