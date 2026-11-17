"""Per-conversation funding for delegated organization runs.

The organization connector registers how Control funded a command before it
starts the conversation run and releases it when the run ends. Accounting and
budget checks refuse an organization conversation without a registration, so
the Runtime never pays for organization work it was not told how to fund.
"""

from __future__ import annotations

import os
import threading
from typing import Any

ORGANIZATION = "organization"
OWNER = "owner"

_FUNDING: dict[str, dict[str, str]] = {}
_LOCK = threading.Lock()


def owns_organization_context(context_id: str) -> bool:
    """Match only the workspace ownership provisioned by Control.

    Connector enrollment also exists on member workspaces, so neither that
    enrollment nor a conversation's organization alone establishes ownership.
    Missing ownership configuration preserves the delegated funding checks.
    """
    organization_id = os.environ.get("RUNTIME_WORKSPACE_ORGANIZATION_ID", "").strip()
    return bool(
        os.environ.get("RUNTIME_WORKSPACE_ID", "").strip()
        and os.environ.get("RUNTIME_WORKSPACE_OWNER_KIND", "").strip() == ORGANIZATION
        and organization_id
        and organization_id == context_id
    )


def register(
    conversation_id: str,
    mode: str,
    model: str = "",
    *,
    workspace: bool = False,
    router_token: str = "",
) -> None:
    if mode not in {ORGANIZATION, OWNER} or not conversation_id:
        raise ValueError("invalid organization funding")
    # Borrowed agents use a delegated token for direct provider models. Legacy
    # ``org/...`` grants remain valid without one. Organization workspaces use
    # their own key and may retain the agent's model (empty).
    if router_token and (
        mode != ORGANIZATION
        or workspace
        or not model
        or not router_token.startswith("xno-org-run.")
    ):
        raise ValueError("invalid organization inference token")
    if mode == ORGANIZATION and not workspace and not router_token and not model.startswith("org/"):
        raise ValueError("organization funding requires an organization model")
    with _LOCK:
        _FUNDING[conversation_id] = {"mode": mode, "model": model, "router_token": router_token}


def release(conversation_id: str) -> None:
    with _LOCK:
        _FUNDING.pop(conversation_id, None)


def funding_for(conversation_id: str | None) -> dict[str, Any] | None:
    if not conversation_id:
        return None
    with _LOCK:
        value = _FUNDING.get(str(conversation_id))
    return {key: item for key, item in value.items() if key != "router_token"} if value else None


def router_token_for(conversation_id: str) -> str:
    """Private credential accessor; funding metadata never exposes the token."""
    with _LOCK:
        return _FUNDING.get(str(conversation_id), {}).get("router_token", "")
