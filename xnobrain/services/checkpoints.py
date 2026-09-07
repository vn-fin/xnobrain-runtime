"""Workspace restore-point policy and orchestration."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping

from ..integrations.checkpoints import CheckpointIntegration, CheckpointIntegrationError
from .base import ServiceError
from .workspaces import _validate_create_path


class CheckpointService:
    def __init__(self, agents: Any):
        self.agents = agents
        self.integration = CheckpointIntegration()
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    def _profile(self, agent_id: str) -> tuple[str, Path, Path, bool]:
        name = self.agents._agent_name(agent_id)
        profile = self.agents._require_profile(name)
        workspace = self.agents.workspace_dir(name)
        raw = self.agents._read_config(profile).get("checkpoints", {})
        enabled = bool(raw.get("enabled", False)) if isinstance(raw, Mapping) else bool(raw)
        return name, profile, workspace, enabled

    def _lock(self, workspace: Path) -> threading.RLock:
        key = str(workspace.resolve())
        with self._locks_guard:
            return self._locks.setdefault(key, threading.RLock())

    @contextmanager
    def mutation(self, agent_id: str, reason: str):
        _, _, workspace, enabled = self._profile(agent_id)
        lock = self._lock(workspace)
        with lock:
            if enabled:
                available, unavailable = self.integration.capability()
                if available:
                    self.integration.take(workspace, reason)
            yield

    def status(self, agent_id: str) -> dict[str, Any]:
        name, _, workspace, enabled = self._profile(agent_id)
        with self._lock(workspace):
            return {"agent_id": name, "enabled": enabled, **self.integration.status(workspace)}

    def list(self, agent_id: str, cursor: str | None = None, limit: int = 20) -> dict[str, Any]:
        _, _, workspace, enabled = self._profile(agent_id)
        if not enabled:
            raise ServiceError(
                "File restore points are disabled", status=409, code="checkpoints_disabled"
            )
        with self._lock(workspace):
            items = self.integration.list(workspace)
        offset = int(cursor or 0)
        size = max(1, min(int(limit), 100))
        page = items[offset : offset + size]
        next_cursor = str(offset + size) if offset + size < len(items) else None
        return {"items": page, "next_cursor": next_cursor}

    @staticmethod
    def _relative(path: Any) -> str:
        return _validate_create_path(path)

    def diff(self, agent_id: str, checkpoint_id: str) -> dict[str, Any]:
        _, _, workspace, enabled = self._profile(agent_id)
        if not enabled:
            raise ServiceError(
                "File restore points are disabled", status=409, code="checkpoints_disabled"
            )
        with self._lock(workspace):
            return self.integration.diff(workspace, checkpoint_id)

    def file_versions(self, agent_id: str, path: Any) -> dict[str, Any]:
        _, _, workspace, enabled = self._profile(agent_id)
        if not enabled:
            raise ServiceError(
                "File restore points are disabled", status=409, code="checkpoints_disabled"
            )
        relative = self._relative(path)
        resolved = (workspace / relative).resolve(strict=False)
        if workspace != resolved and workspace not in resolved.parents:
            raise ServiceError("path escapes the workspace", code="invalid_workspace_path")
        with self._lock(workspace):
            return self.integration.file_versions(workspace, relative)

    def restore(self, agent_id: str, checkpoint_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        name, _, workspace, enabled = self._profile(agent_id)
        if not enabled:
            raise ServiceError(
                "File restore points are disabled", status=409, code="checkpoints_disabled"
            )
        relative = self._relative(body.get("path")) if body.get("path") is not None else None
        if relative is not None:
            resolved = (workspace / relative).resolve(strict=False)
            if workspace != resolved and workspace not in resolved.parents:
                raise ServiceError("path escapes the workspace", code="invalid_workspace_path")
        if name in self.agents.active_agent_ids():
            raise ServiceError(
                "Stop the active run before restoring", status=409, code="agent_busy"
            )
        with self._lock(workspace):
            if name in self.agents.active_agent_ids():
                raise ServiceError(
                    "Stop the active run before restoring", status=409, code="agent_busy"
                )
            result = self.integration.restore(workspace, checkpoint_id, relative)
        return {
            "restored": True,
            "scope": "file" if relative else "workspace",
            "path": relative,
            "checkpoint_id": checkpoint_id,
            "short_id": checkpoint_id[:7],
            **result,
            "conversation_changed": False,
        }


class CheckpointsServiceMixin:
    def checkpoint_status(self, agent_id: str):
        return self.checkpoints.status(agent_id)

    def list_checkpoints(self, agent_id: str, cursor: str | None = None, limit: int = 20):
        return self.checkpoints.list(agent_id, cursor, limit)

    def checkpoint_diff(self, agent_id: str, checkpoint_id: str):
        return self.checkpoints.diff(agent_id, checkpoint_id)

    def checkpoint_file_versions(self, agent_id: str, path: Any):
        return self.checkpoints.file_versions(agent_id, path)

    def restore_checkpoint(self, agent_id: str, checkpoint_id: str, body: Mapping[str, Any]):
        return self.checkpoints.restore(agent_id, checkpoint_id, body)
