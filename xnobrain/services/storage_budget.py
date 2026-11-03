"""Admission for an opted-in workspace's combined private storage budget.

This is deliberately a soft, sampled limit. It does not stop existing writers
or replace the filesystem size limits on the two separate volumes.
"""

from __future__ import annotations

import os

from .base import ServiceError


def storage_budget() -> dict:
    # No default allocation: old workspaces and ordinary Docker stay unchanged.
    raw = os.getenv("RUNTIME_STORAGE_SOFT_LIMIT_BYTES", "0")
    try:
        limit = int(raw)
    except ValueError:
        limit = -1
    if limit == 0:
        return {"enabled": False, "limit_bytes": 0, "used_bytes": None, "state": "disabled"}
    result = {
        "enabled": True,
        "limit_bytes": max(0, limit),
        "used_bytes": None,
        "state": "unavailable",
    }
    if limit < 0:
        return result
    from ..integrations.runtime import LocalRuntimeManager

    sample = LocalRuntimeManager._private_storage_sample()
    used = 0
    for name in ("root", "data"):
        volume = sample.get(name)
        if not isinstance(volume, dict):
            return result
        private, shared = volume.get("private_used_bytes"), volume.get("shared_bytes")
        if type(private) is not int or private < 0 or type(shared) is not int or shared < 0:
            return result
        used += private
    return {**result, "used_bytes": used, "state": "exceeded" if used >= limit else "available"}


def require_storage_growth() -> None:
    budget = storage_budget()
    if budget["state"] == "exceeded":
        raise ServiceError(
            "Workspace storage limit reached. Delete files before starting new work.",
            status=507,
            code="workspace_storage_budget_exceeded",
        )
    if budget["state"] == "unavailable":
        raise ServiceError(
            "Workspace storage usage is temporarily unavailable. Try again shortly.",
            status=503,
            code="workspace_storage_usage_unavailable",
        )
