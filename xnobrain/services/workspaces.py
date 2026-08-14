"""Agent workspace service behavior."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import hashlib
import json
import logging
import os
from pathlib import Path
import time
from typing import Any, Mapping
import uuid

import yaml

from ..defaults import (
    BIG_BROTHER_APPROVAL_DEFAULT_MARKER,
    BIG_BROTHER_AGENT_ID,
    BIG_BROTHER_DESCRIPTION,
    BIG_BROTHER_DISPLAY_NAME,
    BIG_BROTHER_MODEL_DEFAULT_MARKER,
    BIG_BROTHER_NATIVE_TOOLSETS,
    BIG_BROTHER_SKILL_CATEGORY,
    BIG_BROTHER_SKILL_ID,
    CUSTOM_SKILL_CATEGORY,
    DEFAULT_PROFILE_MODEL,
    LEGACY_BIG_BROTHER_TOOLSET,
)
from ..integrations import AgentAPIError, ConfigAPIError, NineRouterAPIError
from ..repositories import StoreError
from .base import ServiceError, iso, utc_now
from .constants import (
    API_KEY_PROVIDERS,
    DEFAULT_TEAM_COORDINATOR_PROMPT,
    DEFAULT_TEAM_SYNTHESIS_PROMPT,
    EVERY_SCHEDULE,
    FREE_MODEL_PROVIDERS,
    NO_AUTH_PROVIDERS,
    OPENAI_COMPATIBLE_PROVIDER_DEFINITIONS,
    PROVIDER_DEFINITIONS,
    SAFE_TOOLSETS,
    SUPPORTED_PROVIDERS,
)
from .cron import CronServiceError
from .helpers import cached_method
from .workspace_preview import WorkspacePreview, WorkspacePreviewError
from .workspace_upload import WorkspaceUploadError


def _validate_create_path(raw_path: Any) -> str:
    """Reject traversal/normalization tricks while allowing nested UI paths."""
    import unicodedata
    from urllib.parse import unquote

    path = str(raw_path or "")
    decoded = unquote(path)
    if (
        not path
        or decoded != path
        or path.startswith(("/", "\\"))
        or "\\" in path
        or any(character in path for character in ("\x00", "\r", "\n", "∕", "⁄", "／"))
        or unicodedata.normalize("NFC", path) != path
    ):
        raise AgentAPIError("path contains an invalid file name", code="invalid_workspace_path", status=400)
    parts = path.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        raise AgentAPIError("path contains an invalid file name", code="invalid_workspace_path", status=400)
    return path

class WorkspacesServiceMixin:
    def list_workspace(self, agent_id: str, path: str = ".") -> dict[str, Any]:
        return self.agents.list_workspace(agent_id, {"path": path})

    def read_workspace(self, agent_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        return self.agents.read_workspace_file(agent_id, body)

    def workspace_file(self, agent_id: str, path: Any) -> Path:
        self.agents._require_profile(self.agents._agent_name(agent_id))
        source = self.agents._workspace_path(agent_id, path, require_file=True)
        if not source.is_file():
            raise AgentAPIError(
                "path is not a file",
                code="invalid_workspace_path",
                status=404,
            )
        return source

    def preview_workspace(self, agent_id: str, path: Any) -> WorkspacePreview:
        return self.workspace_previews.preview(self.workspace_file(agent_id, path))

    def workbook_workspace(self, agent_id: str, path: Any) -> WorkspacePreview:
        return self.workspace_previews.workbook(self.workspace_file(agent_id, path))

    def write_workspace(self, agent_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        relative = _validate_create_path(body.get("path"))
        with self.checkpoints.mutation(agent_id, f"before workspace write: {relative}"):
            return self.agents.write_workspace_file(agent_id, body)

    def upload_workspace_chunk(
        self,
        agent_id: str,
        *,
        path: Any,
        file_name: Any,
        upload_id: Any,
        chunk_index: int,
        total_chunks: int,
        total_size: int,
        payload: bytes,
    ) -> dict[str, Any]:
        name = str(file_name or "").strip()
        if (
            not name
            or name in {".", ".."}
            or len(name) > 255
            or any(character in name for character in ("/", "\\", "\x00", "\r", "\n"))
        ):
            raise WorkspaceUploadError("file_name is invalid")
        directory = str(path or "").strip()
        relative = f"{directory.rstrip('/')}/{name}" if directory else name
        self.agents._require_profile(self.agents._agent_name(agent_id))
        workspace_root = self.agents.workspace_dir(agent_id)
        target = self.agents._workspace_path(agent_id, relative, require_file=True)
        return self.workspace_uploads.put_chunk(
            agent_id=agent_id,
            upload_id=str(upload_id or ""),
            target=target,
            workspace_root=workspace_root,
            chunk_index=chunk_index,
            total_chunks=total_chunks,
            total_size=total_size,
            payload=payload,
            publish_context=lambda: self.checkpoints.mutation(
                agent_id, f"before workspace upload: {relative}"
            ),
        )

    def create_workspace(self, agent_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        path_value = _validate_create_path(body.get("path"))
        if str(body.get("type") or "file") == "directory":
            path = self.agents._workspace_path(agent_id, path_value, require_file=False)
            with self.checkpoints.mutation(agent_id, f"before workspace create: {path_value}"):
                path.mkdir(parents=True, exist_ok=True)
            return {"agent": agent_id, "path": str(path.relative_to(self.agents._workspace_dir(agent_id))), "type": "directory"}
        return self.write_workspace(agent_id, {"path": path_value, "content": body.get("content") or ""})

    def delete_workspace(self, agent_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        relative = _validate_create_path(body.get("path"))
        with self.checkpoints.mutation(agent_id, f"before workspace delete: {relative}"):
            return self.agents.delete_workspace_path(agent_id, body)

    def rename_workspace(self, agent_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        source_relative = _validate_create_path(body.get("path"))
        new_name = _validate_create_path(body.get("new_name"))
        if "/" in new_name or len(new_name) > 255:
            raise AgentAPIError("new_name must be one file name", code="invalid_workspace_path")
        source = self.agents._workspace_path(agent_id, source_relative, require_file=False)
        if source == self.agents._workspace_dir(agent_id) or not source.exists():
            raise AgentAPIError("path not found", code="workspace_path_not_found", status=404)
        destination = source.with_name(new_name)
        if destination.exists() or destination.is_symlink():
            raise AgentAPIError("destination already exists", code="destination_exists", status=409)
        with self.checkpoints.mutation(agent_id, f"before workspace rename: {source_relative}"):
            os.replace(source, destination)
        return {
            "agent": agent_id,
            "path": destination.relative_to(self.agents._workspace_dir(agent_id)).as_posix(),
            "type": "directory" if destination.is_dir() else "file",
        }
