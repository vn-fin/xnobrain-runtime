"""Bounded environment settings for conversation and delegation execution."""

from __future__ import annotations

import os
from typing import Any


SESSION_TIMEOUT_ENV = "HERMES_SESSION_TIMEOUT_SECONDS"
PARALLEL_AGENTS_ENV = "DELEGATION_MAX_CONCURRENT_CHILDREN"
DEFAULT_SESSION_TIMEOUT_SECONDS = 3600
MAX_SESSION_TIMEOUT_SECONDS = 3600
DEFAULT_PARALLEL_AGENTS = 3
MAX_PARALLEL_AGENTS = 5


def _bounded_int(value: Any, *, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, maximum))


def session_timeout_seconds(requested: Any = None) -> int:
    """Return the requested timeout, or the bounded environment default."""
    configured = _bounded_int(
        os.getenv(SESSION_TIMEOUT_ENV),
        default=DEFAULT_SESSION_TIMEOUT_SECONDS,
        maximum=MAX_SESSION_TIMEOUT_SECONDS,
    )
    return _bounded_int(requested, default=configured, maximum=MAX_SESSION_TIMEOUT_SECONDS)


def max_parallel_agents(configured: Any = None) -> int:
    """Return a delegation slot count constrained to this host's safe cap."""
    value = os.getenv(PARALLEL_AGENTS_ENV) if configured is None else configured
    return _bounded_int(
        value,
        default=DEFAULT_PARALLEL_AGENTS,
        maximum=MAX_PARALLEL_AGENTS,
    )
