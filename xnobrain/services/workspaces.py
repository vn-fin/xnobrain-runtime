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
        )

    def create_workspace(self, agent_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        if str(body.get("type") or "file") == "directory":
            path = self.agents._workspace_path(agent_id, body.get("path"), require_file=False)
            path.mkdir(parents=True, exist_ok=True)
            return {"agent": agent_id, "path": str(path.relative_to(self.agents._workspace_dir(agent_id))), "type": "directory"}
        return self.write_workspace(agent_id, {"path": body.get("path"), "content": body.get("content") or ""})

    def delete_workspace(self, agent_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        return self.agents.delete_workspace_path(agent_id, body)


