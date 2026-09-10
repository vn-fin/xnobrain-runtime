"""Install only the workspace's scoped Router credential in native profile turns."""

import os
from contextlib import contextmanager
from pathlib import Path

from .accounting_context import current_accounting
from .llm_router_support import LLM_ROUTER_KEY_ENV, LLM_ROUTER_PROVIDER


def workspace_router_key() -> str:
    """Read the existing workload secret without rotating or persisting it."""
    context = current_accounting()
    if context is not None:
        return context["binding"]["workload_key"]
    token = os.environ.get("RUNTIME_LLM_API_KEY", "").strip()
    token_file = os.environ.get("RUNTIME_LLM_API_KEY_FILE", "").strip()
    if token_file:
        try:
            token = Path(token_file).read_text(encoding="utf-8").strip() or token
        except OSError:
            pass
    return token


@contextmanager
def conversation_profile_scope(profile_dir: Path):
    """Keep native profile isolation while admitting the workspace workload key.

    Native background/goal turns install an authoritative profile secret scope.
    A key injected by Control into the workspace environment/file otherwise need
    not exist in that profile's .env. Never merge the process environment: it can
    contain other profiles' credentials or private service/management secrets.
    """
    from agent.secret_scope import current_secret_scope, reset_secret_scope, set_secret_scope
    from gateway.run import _profile_runtime_scope

    with _profile_runtime_scope(profile_dir):
        secrets = dict(current_secret_scope() or {})
        key = workspace_router_key()
        if key:
            secrets[LLM_ROUTER_KEY_ENV] = key
        token = set_secret_scope(secrets)
        try:
            yield
        finally:
            reset_secret_scope(token)


def conversation_model_route(model: str, base_url: str) -> dict[str, str]:
    """Use the same server-owned inference endpoint for chat and resumed goals."""
    route = {"model": model} if model else {}
    if base_url:
        route.update(provider=LLM_ROUTER_PROVIDER, base_url=base_url)
        key = workspace_router_key()
        if key:
            route["api_key"] = key
    return route
