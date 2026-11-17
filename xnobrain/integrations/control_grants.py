"""In-memory Control MCP grants scoped to a single Hermes profile."""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from pathlib import Path

_LOCK = threading.RLock()
# Management operations finish well within it; Control's grant TTL is 2h.
_MANAGEMENT_GRANT_TTL_SECONDS = 600.0
_GRANTS: dict[str, tuple[str, float]] = {}
_RUN_GRANTS: dict[str, list[tuple[object, str]]] = {}


def _effective_grant(scope: str) -> str:
    """Return the newest active run grant, else the unexpired management grant."""
    now = time.monotonic()
    entry = _GRANTS.get(scope)
    if entry is not None and entry[1] <= now:
        _GRANTS.pop(scope, None)
        entry = None
    runs = _RUN_GRANTS.get(scope)
    if runs:
        return runs[-1][1]
    return entry[0] if entry is not None else ""


def set(scope: str, grant: str | None) -> None:
    """Store a fallback management grant and wake parked proxy MCP tasks."""
    if not grant:
        return
    with _LOCK:
        previous = _effective_grant(scope)
        expires_at = time.monotonic() + _MANAGEMENT_GRANT_TTL_SECONDS
        _GRANTS[scope] = (grant, expires_at)
        current = _effective_grant(scope)
    if current != previous:
        from . import managed_mcp

        managed_mcp.reconnect_control_tasks(scope)


def get(scope: str) -> str:
    with _LOCK:
        return _effective_grant(scope)


@contextmanager
def bind_run(profile: Path, grant: str):
    from .managed_mcp import _profile_scope

    scope = _profile_scope(Path(profile))
    token = object()
    with _LOCK:
        previous = _effective_grant(scope)
        if grant:
            _RUN_GRANTS.setdefault(scope, []).append((token, grant))
        current = _effective_grant(scope)
    if current != previous:
        from . import managed_mcp

        managed_mcp.reconnect_control_tasks(scope)
    try:
        yield
    finally:
        with _LOCK:
            entries = _RUN_GRANTS.get(scope)
            if entries is not None:
                entries[:] = [entry for entry in entries if entry[0] is not token]
                if not entries:
                    _RUN_GRANTS.pop(scope, None)
