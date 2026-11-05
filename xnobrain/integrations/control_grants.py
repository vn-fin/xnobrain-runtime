"""In-memory Control MCP grants scoped to a single Hermes profile."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path

_LOCK = threading.RLock()
_GRANTS: dict[str, str] = {}


def set(scope: str, grant: str | None) -> None:
    """Replace a profile grant and wake its parked proxy MCP tasks."""
    if not grant:
        return
    with _LOCK:
        previous = _GRANTS.get(scope, "")
        _GRANTS[scope] = grant
    if grant and grant != previous:
        from . import managed_mcp

        managed_mcp.reconnect_control_tasks(scope)


def get(scope: str) -> str:
    with _LOCK:
        return _GRANTS.get(scope, "")


@contextmanager
def bind_run(profile: Path, grant: str):
    from .managed_mcp import _profile_scope

    scope = _profile_scope(Path(profile))
    set(scope, grant)
    yield
