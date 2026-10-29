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


def register(conversation_id: str, mode: str, model: str = "", *, workspace: bool = False) -> None:
    if mode not in {ORGANIZATION, OWNER} or not conversation_id:
        raise ValueError("invalid organization funding")
    # A member Runtime must use a granted ``org/...`` model. The organization
    # workspace bills the organization through its own key, so it may keep the
    # agent's model (empty) or use any model of the organization's connections.
    if mode == ORGANIZATION and not workspace and not model.startswith("org/"):
        raise ValueError("organization funding requires an organization model")
    with _LOCK:
        _FUNDING[conversation_id] = {"mode": mode, "model": model}


def release(conversation_id: str) -> None:
    with _LOCK:
        _FUNDING.pop(conversation_id, None)


def funding_for(conversation_id: str | None) -> dict[str, Any] | None:
    if not conversation_id:
        return None
    with _LOCK:
        value = _FUNDING.get(str(conversation_id))
    return dict(value) if value else None
