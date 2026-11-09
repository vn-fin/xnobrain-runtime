"""In-memory Control MCP grants scoped to a Hermes profile or one run."""

from __future__ import annotations

import contextvars
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
# Per-run overlay read by the managed MCP session wrapper. A ContextVar is
# safe across asyncio tasks; the session wrapper transfers it onto the exact
# server auth object while one request is in flight.
_CURRENT_RUN_GRANT: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "xnobrain_control_run_grant", default=None
)
# Presence means "this server is executing a bound run". An empty value is a
# deliberate no-credential scope and must not fall back to a profile grant.
_SERVER_GRANTS: dict[str, str] = {}
_PENDING_SERVER_GRANTS: dict[str, str | None] = {}
_CALL_LOCKS: dict[str, threading.RLock] = {}


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


def current_run_grant() -> str | None:
    """Return the grant bound to the current run, or None when unbound.

    An empty string is meaningful: the run is bound with no credential and
    must not inherit a profile-scoped grant.
    """
    return _CURRENT_RUN_GRANT.get()


def get_for_server(server_key: str, scope: str) -> str:
    """Prefer one run's server-local scope, then the profile fallback."""
    if server_key:
        with _LOCK:
            if server_key in _SERVER_GRANTS:
                return _SERVER_GRANTS[server_key]
    return get(scope)


def set_server_grant(server_key: str, grant: str) -> None:
    """Bind one in-flight managed MCP request to an exact run scope."""
    if not server_key:
        return
    with _LOCK:
        _SERVER_GRANTS[server_key] = str(grant or "")


def clear_server_grant(server_key: str) -> None:
    if not server_key:
        return
    with _LOCK:
        _SERVER_GRANTS.pop(server_key, None)


def pending_server_grant(server_key: str) -> str | None:
    """Return the grant handed from a tool handler to the MCP loop."""
    with _LOCK:
        return _PENDING_SERVER_GRANTS.get(server_key)


@contextmanager
def server_call_scope(server_key: str, grant: str | None):
    """Serialize one server's tool handlers and publish their run grant.

    Hermes already serializes client RPCs per server. This caller-side lock
    keeps the pending grant unambiguous while the coroutine runs on the
    dedicated MCP loop in another thread.
    """
    if not server_key:
        yield
        return
    with _LOCK:
        lock = _CALL_LOCKS.setdefault(server_key, threading.RLock())
    with lock:
        with _LOCK:
            _PENDING_SERVER_GRANTS[server_key] = grant
        try:
            yield
        finally:
            with _LOCK:
                _PENDING_SERVER_GRANTS.pop(server_key, None)


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
    run_token = _CURRENT_RUN_GRANT.set(str(grant or ""))
    try:
        yield
    finally:
        _CURRENT_RUN_GRANT.reset(run_token)
        with _LOCK:
            entries = _RUN_GRANTS.get(scope)
            if entries is not None:
                entries[:] = [entry for entry in entries if entry[0] is not token]
                if not entries:
                    _RUN_GRANTS.pop(scope, None)
