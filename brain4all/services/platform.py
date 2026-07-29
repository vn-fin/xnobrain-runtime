"""Platform service layer for agents, Hermes profiles, cron, MCP, teams, and snapshots."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
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
from ..integrations import (
    AgentAPIError,
    AgentManager,
    ConfigAPIError,
    GlobalConfigManager,
    NineRouterAPIError,
    NineRouterManager,
)
from ..repositories import FileRepository, StoreError
from .portability import PortabilityService
from .cron import CronService, CronServiceError
from .workspace_preview import WorkspacePreview, WorkspacePreviewError, WorkspacePreviewService
from .workspace_upload import WorkspaceUploadError, WorkspaceUploadService


SUPPORTED_PROVIDERS = ("claude", "codex", "antigravity", "openai", "anthropic", "gemini")
API_KEY_PROVIDERS = frozenset({"openai", "anthropic", "gemini"})
SAFE_TOOLSETS = frozenset({
    "browser", "code_execution", "computer_use", "context_engine", "file",
    "image_gen", "session_search", "skills", "terminal", "todo", "tts",
    "video", "video_gen", "vision", "web", "x_search",
})
DEFAULT_TEAM_COORDINATOR_PROMPT = (
    "Plan the workflow and give every stage clear, actionable execution guidance."
)
DEFAULT_TEAM_SYNTHESIS_PROMPT = (
    "Synthesize all completed stage outputs into one clear, accurate final answer."
)
_EVERY = re.compile(r"^@every\s+(\d+)([smhd])$")


class ServiceError(ValueError):
    """Expected service validation or not-found error."""

    def __init__(self, message: str, *, status: int = 400, code: str = "invalid_request"):
        super().__init__(message)
        self.status = status
        self.code = code


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat().replace("+00:00", "Z")


class PlatformService:
    """Coordinates upstream Hermes managers with platform-owned file metadata."""

    def __init__(
        self,
        repository: FileRepository,
        agents: AgentManager,
        config: GlobalConfigManager,
        router: NineRouterManager,
        runtime,
    ):
        self.repository = repository
        self.agents = agents
        self.config = config
        self.router = router
        self.runtime = runtime
        self.portability = PortabilityService(repository, config.root_profile)
        self.cron = CronService(repository, agents)
        self.workspace_previews = WorkspacePreviewService(repository.data_dir / "workspace-previews")
        self.workspace_uploads = WorkspaceUploadService(repository.data_dir / "workspace-uploads")
        from .kanban import KanbanService
        self.kanban = KanbanService(agents, repository)
        from .analytics import AnalyticsService
        self.analytics = AnalyticsService(agents, router, repository)
        from .blends import BlendService
        self.blends = BlendService(router)
        from .team_runs import TeamRunService
        self.team_runs = TeamRunService(repository, agents, self)
        self._oauth_attempts: dict[str, dict[str, str]] = {}
        self.config.ensure_write_approval_defaults()
        self._ensure_existing_write_approval_defaults()

    def _ensure_existing_write_approval_defaults(self) -> None:
        """Backfill safe defaults into existing agent profiles with a snapshot."""
        for profile in self.repository.profiles_root.iterdir():
            path = profile / "config.yaml"
            if not profile.is_dir() or not path.is_file():
                continue
            try:
                config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError):
                continue
            if not isinstance(config, dict):
                continue
            changed = False
            brain4all_config = config.get("brain4all")
            if not isinstance(brain4all_config, dict):
                brain4all_config = {}
                config["brain4all"] = brain4all_config
                changed = True
            if not bool(brain4all_config.get(BIG_BROTHER_MODEL_DEFAULT_MARKER)):
                model = config.get("model")
                if not isinstance(model, dict):
                    model = {}
                    config["model"] = model
                model["default"] = DEFAULT_PROFILE_MODEL
                brain4all_config[BIG_BROTHER_MODEL_DEFAULT_MARKER] = True
                changed = True
            for subsystem in ("skills", "memory"):
                section = config.get(subsystem)
                if not isinstance(section, dict):
                    section = {}
                    config[subsystem] = section
                    changed = True
                if "write_approval" not in section:
                    section["write_approval"] = True
                    changed = True
            if changed:
                self.repository.snapshot(profile.name, "config", "config", path.read_bytes())
                self.repository.atomic_yaml(path, config)

    def list_profiles(self) -> list[dict[str, Any]]:
        """Use Hermes' native profile inventory, including the default profile."""
        from hermes_cli.profiles import list_profiles
        registry = {
            item["name"]: item
            for item in self.agents.sync_profiles_registry()["profiles"]
        }
        result = []
        for item in list_profiles():
            updated_at = item.path.stat().st_mtime if item.path.exists() else None
            metadata = registry.get(item.name, {})
            result.append({
                "name": item.name, "path": str(item.path), "is_default": item.is_default,
                "gateway_running": item.gateway_running, "model": item.model,
                "provider": item.provider, "skill_count": item.skill_count,
                "display_name": metadata.get("display_name", item.name),
                "description": metadata.get("description", item.description),
                "updated_at": metadata.get("updated_at", updated_at),
            })
        return result

    def sandbox(self, action: str) -> dict[str, Any]:
        """Return local runtime detail without exposing a process listing."""
        detail = self.runtime.detail()
        if action == "detail":
            return detail
        if action in {"info", "metrics", "health"}:
            return detail[action]
        if action == "stats":
            return {"metrics": detail["metrics"], "system": detail["system"]}
        raise ServiceError("sandbox resource not found", status=404, code="not_found")

    def list_agents(self) -> list[dict[str, Any]]:
        return [self._agent_dto(item) for item in self.agents.list_agents()["agents"]]

    async def ensure_default_agent(self) -> dict[str, Any]:
        """Expose and repair the root Hermes profile as Big Brother."""
        profile = self.config.root_profile
        profile.mkdir(parents=True, exist_ok=True)
        (profile / "workspace").mkdir(parents=True, exist_ok=True)
        metadata_path = profile / "agent.json"
        metadata = {}
        if metadata_path.is_file():
            try:
                loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
                metadata = loaded if isinstance(loaded, dict) else {}
            except (OSError, json.JSONDecodeError):
                metadata = {}
        next_metadata = {
            **metadata,
            "name": BIG_BROTHER_AGENT_ID,
            "profile_name": "default",
            "display_name": BIG_BROTHER_DISPLAY_NAME,
            "title": BIG_BROTHER_DISPLAY_NAME,
            "description": BIG_BROTHER_DESCRIPTION,
            "updated_at": time.time(),
        }
        if next_metadata != metadata:
            self.repository.atomic_json(metadata_path, next_metadata)

        self.agents.migrate_legacy_big_brother_profile()
        self._migrate_big_brother_skill_category(profile)
        self._ensure_big_brother_toolsets(profile)
        bundled_skill_path = (
            Path(__file__).resolve().parent.parent
            / "assets"
            / "skills"
            / BIG_BROTHER_SKILL_ID
            / "SKILL.md"
        )
        bundled_skill = bundled_skill_path.read_text(encoding="utf-8")
        installed_skill_path = (
            profile
            / "skills"
            / BIG_BROTHER_SKILL_CATEGORY
            / BIG_BROTHER_SKILL_ID
            / "SKILL.md"
        )
        installed_skill = (
            installed_skill_path.read_text(encoding="utf-8")
            if installed_skill_path.is_file()
            else None
        )
        if installed_skill != bundled_skill:
            await self.config.install_skill({
                "skill_id": BIG_BROTHER_SKILL_ID,
                "category": BIG_BROTHER_SKILL_CATEGORY,
                "content": bundled_skill,
                "enable": True,
            })
        elif not next(
            (
                item.get("enabled", True)
                for item in self.config.list_skills()["skills"]
                if item.get("skill_id") == BIG_BROTHER_SKILL_ID
            ),
            True,
        ):
            self.config.set_skill_enabled(
                BIG_BROTHER_SKILL_ID,
                {"enabled": True},
            )
        self.agents.update_profile_registry(
            BIG_BROTHER_AGENT_ID,
            display_name=BIG_BROTHER_DISPLAY_NAME,
            description=BIG_BROTHER_DESCRIPTION,
        )
        return self.get_agent(BIG_BROTHER_AGENT_ID)

    def _migrate_big_brother_skill_category(self, profile: Path) -> None:
        """Move the former root-level bundled skill out of profile inheritance."""
        destination = (
            profile / "skills" / BIG_BROTHER_SKILL_CATEGORY / BIG_BROTHER_SKILL_ID
        )
        for legacy in (
            profile / "skills" / BIG_BROTHER_SKILL_ID,
            profile / "skills" / CUSTOM_SKILL_CATEGORY / BIG_BROTHER_SKILL_ID,
        ):
            if not legacy.is_dir():
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                os.replace(legacy, destination)
                continue
            archive = (
                profile
                / "snapshots"
                / "migrations"
                / f"legacy-{BIG_BROTHER_SKILL_ID}-{time.time_ns()}"
            )
            archive.parent.mkdir(parents=True, exist_ok=True)
            os.replace(legacy, archive)

    def _ensure_big_brother_toolsets(self, profile: Path) -> None:
        """Repair required capabilities without removing user-added toolsets."""
        path = profile / "config.yaml"
        try:
            config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except FileNotFoundError:
            config = {}
        except (OSError, yaml.YAMLError) as exc:
            raise ServiceError(
                "Big Brother profile config is unreadable",
                status=500,
                code="invalid_system_profile",
            ) from exc
        if not isinstance(config, dict):
            config = {}

        changed = False
        legacy = config.get("toolsets")
        if not isinstance(legacy, list):
            legacy = []
        if "kanban" not in legacy:
            config["toolsets"] = [*legacy, "kanban"]
            changed = True

        platforms = config.get("platform_toolsets")
        if not isinstance(platforms, dict):
            platforms = {}
            config["platform_toolsets"] = platforms
            changed = True
        api_server = platforms.get("api_server")
        if not isinstance(api_server, list):
            api_server = []
        next_api_server = [
            item for item in api_server
            if item != LEGACY_BIG_BROTHER_TOOLSET
        ]
        for toolset in BIG_BROTHER_NATIVE_TOOLSETS:
            if toolset not in next_api_server:
                next_api_server.append(toolset)
        if next_api_server != api_server:
            platforms["api_server"] = next_api_server
            changed = True

        brain4all_config = config.get("brain4all")
        if not isinstance(brain4all_config, dict):
            brain4all_config = {}
            config["brain4all"] = brain4all_config
            changed = True
        if not bool(brain4all_config.get(BIG_BROTHER_MODEL_DEFAULT_MARKER)):
            model = config.get("model")
            if not isinstance(model, dict):
                model = {}
                config["model"] = model
            model["default"] = DEFAULT_PROFILE_MODEL
            brain4all_config[BIG_BROTHER_MODEL_DEFAULT_MARKER] = True
            changed = True
        if not bool(brain4all_config.get(BIG_BROTHER_APPROVAL_DEFAULT_MARKER)):
            approvals = config.get("approvals")
            if not isinstance(approvals, dict):
                approvals = {}
                config["approvals"] = approvals
            approvals["mode"] = "off"
            for subsystem in ("skills", "memory"):
                section = config.get(subsystem)
                if not isinstance(section, dict):
                    section = {}
                    config[subsystem] = section
                section["write_approval"] = False
            brain4all_config[BIG_BROTHER_APPROVAL_DEFAULT_MARKER] = True
            changed = True

        if changed:
            self.config.update_config({"config": config})

    def create_agent(self, body: Mapping[str, Any]) -> dict[str, Any]:
        display_name = str(body.get("display_name") or body.get("name") or "").strip()
        if not display_name:
            raise ServiceError("display_name is required")
        payload = {**dict(body), "title": display_name, "display_name": display_name}
        payload.pop("name", None)
        if "description" in body:
            payload["description"] = body["description"]
        raw, _ = self.agents.create_agent(payload)
        return self._agent_dto(raw)

    def get_agent(self, agent_id: str) -> dict[str, Any]:
        return self._agent_dto(self.agents.describe_agent(agent_id))

    @staticmethod
    def _is_big_brother(agent_id: str) -> bool:
        return str(agent_id or "").strip().casefold() in {
            BIG_BROTHER_AGENT_ID,
            "big brother",
            "default",
        }

    def _agent_profile_path(self, agent_id: str) -> Path:
        if self._is_big_brother(agent_id):
            return self.config.root_profile
        return self.repository.profile_path(agent_id)

    def _snapshot_agent(
        self,
        agent_id: str,
        kind: str,
        target: str,
        content: bytes,
    ) -> dict[str, Any]:
        if not self._is_big_brother(agent_id):
            return self.repository.snapshot(agent_id, kind, target, content)
        digest = hashlib.sha256(content).hexdigest()
        snapshot_id = f"{time.time_ns()}-{digest[:12]}"
        suffix = ".yaml" if kind == "config" else ".md"
        relative = Path("snapshots") / kind / target / f"{snapshot_id}{suffix}"
        self.repository.atomic_write(
            self.config.root_profile / relative,
            content,
            mode=0o440,
            replace=False,
        )
        return {
            "id": snapshot_id,
            "agent_id": agent_id,
            "kind": kind,
            "target": target,
            "hash": digest,
            "path": relative.as_posix(),
            "created_at": time.time(),
        }

    def update_agent_metadata(self, agent_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        profile = self._agent_profile_path(agent_id)
        metadata_path = profile / "agent.json"
        current = {}
        if metadata_path.is_file():
            current = json.loads(metadata_path.read_text(encoding="utf-8"))
        for key in ("display_name", "title", "description"):
            if key in body:
                current[key] = str(body[key] or "").strip()
        if "display_name" in body:
            current["title"] = current["display_name"]
        elif "title" in body:
            current["display_name"] = current["title"]
        current["updated_at"] = time.time()
        self.repository.atomic_json(metadata_path, current)
        self.agents.update_profile_registry(
            agent_id,
            display_name=str(current.get("display_name") or current.get("title") or ""),
            description=str(current.get("description") or ""),
        )
        return self.get_agent(agent_id)

    def delete_agent(self, agent_id: str) -> dict[str, Any]:
        if self._is_big_brother(agent_id):
            raise ServiceError(
                "Big Brother is a protected system profile",
                status=409,
                code="protected_agent",
            )
        # Validate the profile before mutating Kanban so a missing assistant
        # cannot trigger an unrelated cleanup.
        profile = self.repository.profile_path(agent_id)
        if not profile.is_dir():
            raise StoreError("agent not found", status=404, code="not_found")
        deleted_tasks = self.kanban.delete_assignee_tasks(agent_id)
        self.repository.hard_delete_profile(agent_id)
        self.agents.sync_profiles_registry()
        return {
            "deleted": True,
            "recoverable": False,
            "kanban_tasks_deleted": deleted_tasks,
        }

    def global_config(self) -> dict[str, Any]:
        return self.config.get_config()

    def update_global_config(self, body: Mapping[str, Any]) -> dict[str, Any]:
        return self.config.update_config(body)

    def update_agent_config(self, agent_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        translated = dict(body)
        if "reasoning_effort" in translated:
            translated["effort"] = translated.pop("reasoning_effort")
        if self._is_big_brother(agent_id):
            return self.config.update_config(translated)["config"]
        profile = self.repository.profile_path(agent_id)
        path = profile / "config.yaml"
        if path.is_file():
            self.repository.snapshot(agent_id, "config", "config", path.read_bytes())
        return self.agents.update_config(agent_id, translated)["config"]

    def list_skills(self, agent_id: str) -> list[dict[str, Any]]:
        if self._is_big_brother(agent_id):
            return self.config.list_skills()["skills"]
        return self.agents.list_skills(agent_id)["skills"]

    def list_default_skills(self) -> list[dict[str, Any]]:
        """List only the skills installed in the default Hermes profile."""
        return [
            item
            for item in self.config.list_skills()["skills"]
            if Path(str(item.get("relative_path") or "")).parts[:1]
            != (BIG_BROTHER_SKILL_CATEGORY,)
        ]

    async def install_default_skill(self, body: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Install a URL, hub identifier, or local SKILL.md into the root profile."""
        return (
            await self.config.install_skill({
                **dict(body),
                "category": CUSTOM_SKILL_CATEGORY,
                "enable": False,
            })
        )["skills"]

    def set_default_skill_enabled(self, skill_id: str, body: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Change whether new profiles inherit a default-profile skill."""
        return self.config.set_skill_enabled(skill_id, body)["skills"]

    async def install_skill(self, agent_id: str, body: Mapping[str, Any]) -> list[dict[str, Any]]:
        install_body = dict(body)
        has_payload = "content" in install_body or bool(
            str(install_body.get("source") or "").strip()
        )
        if not has_payload:
            skill_id = str(
                install_body.get("skill_id") or install_body.get("name") or ""
            ).strip()
            library_skill = next(
                (
                    item
                    for item in self.list_default_skills()
                    if str(item.get("skill_id") or "") == skill_id
                ),
                None,
            )
            if library_skill is None:
                raise ServiceError(
                    "default profile skill not found",
                    status=404,
                    code="skill_not_found",
                )
            if self._is_big_brother(agent_id):
                return self.config.set_skill_enabled(
                    skill_id,
                    {"enabled": False},
                )["skills"]
            skill_file = Path(str(library_skill.get("path") or "")) / "SKILL.md"
            try:
                install_body["content"] = skill_file.read_text(encoding="utf-8")
            except OSError as exc:
                raise ServiceError(
                    "default profile skill is unreadable",
                    status=500,
                    code="invalid_skill",
                ) from exc

        if self._is_big_brother(agent_id):
            return (
                await self.config.install_skill({
                    **install_body,
                    "category": CUSTOM_SKILL_CATEGORY,
                    "enable": False,
                })
            )["skills"]
        payload = await self.agents.install_skill(agent_id, {
            **install_body,
            "category": CUSTOM_SKILL_CATEGORY,
            "enable": False,
        })
        skill_id = str(
            install_body.get("skill_id") or install_body.get("name") or ""
        ).strip()
        if skill_id and "content" in install_body:
            self.repository.snapshot(
                agent_id,
                "skills",
                skill_id,
                str(install_body["content"]).encode(),
            )
        return payload["skills"]

    def set_skill_enabled(self, agent_id: str, skill_id: str, body: Mapping[str, Any]) -> list[dict[str, Any]]:
        if self._is_big_brother(agent_id):
            return self.config.set_skill_enabled(skill_id, body)["skills"]
        payload = self.agents.set_skill_enabled(agent_id, skill_id, body)
        path = self.repository.profile_path(agent_id) / "skills" / skill_id / "SKILL.md"
        if path.is_file():
            self.repository.snapshot(agent_id, "skills", skill_id, path.read_bytes())
        return payload["skills"]

    def remove_skill(self, agent_id: str, skill_id: str) -> list[dict[str, Any]]:
        if self._is_big_brother(agent_id):
            return self.config.delete_skill(skill_id)["skills"]
        path = self.repository.profile_path(agent_id) / "skills" / skill_id / "SKILL.md"
        if path.is_file():
            self.repository.snapshot(agent_id, "skills", skill_id, path.read_bytes())
        return self.agents.remove_skill(agent_id, skill_id)["skills"]

    def read_memory(self, agent_id: str) -> dict[str, Any]:
        return self.agents.read_memory(agent_id)

    def write_memory(self, agent_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        result = self.agents.write_memory(agent_id, body)
        memory = str(result.get("memory") or body.get("memory") or "")
        self._snapshot_agent(agent_id, "memory", "memory", memory.encode())
        return result

    def list_snapshots(self, agent_id: str, kind: str | None = None) -> list[dict[str, Any]]:
        if self._is_big_brother(agent_id):
            return self._list_root_snapshots(kind)
        return self.repository.list_snapshots(agent_id, kind)

    def restore_snapshot(self, agent_id: str, snapshot_id: str) -> dict[str, Any]:
        if self._is_big_brother(agent_id):
            return self._restore_root_snapshot(snapshot_id)
        return self.repository.restore_snapshot(agent_id, snapshot_id)

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

    def list_conversations(self, agent_id: str) -> dict[str, Any]:
        payload = self.agents.list_conversations(agent_id)
        return {"conversations": [self._conversation_dto(agent_id, item) for item in payload["conversations"]], "pagination": {"page": 1, "limit": 50, "has_more": False}}

    def create_conversation(self, agent_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        payload = self.agents.create_conversation(agent_id, {"title": body.get("title") or "New Conversation"})
        return self._conversation_dto(agent_id, payload["conversation"])

    def get_conversation(self, agent_id: str, conversation_id: str) -> dict[str, Any]:
        payload = self.agents.get_conversation(agent_id, conversation_id)
        result = self._conversation_dto(agent_id, payload["conversation"])
        result["messages"] = payload["messages"]
        return result

    def conversation_usage(self, agent_id: str, conversation_id: str) -> dict[str, Any]:
        payload = self.agents.get_conversation(agent_id, conversation_id)
        session = dict(payload["conversation"])
        messages = list(payload["messages"])

        def integer(field: str) -> int:
            try:
                return max(0, int(session.get(field) or 0))
            except (TypeError, ValueError):
                return 0

        def number(field: str) -> float:
            try:
                return max(0.0, float(session.get(field) or 0))
            except (TypeError, ValueError):
                return 0.0

        input_tokens = integer("input_tokens")
        output_tokens = integer("output_tokens")
        cache_read_tokens = integer("cache_read_tokens")
        cache_write_tokens = integer("cache_write_tokens")
        reasoning_tokens = integer("reasoning_tokens")
        total_tokens = input_tokens + output_tokens + cache_read_tokens + cache_write_tokens
        if total_tokens == 0:
            total_tokens = sum(
                max(0, int(message.get("token_count") or 0))
                for message in messages
                if str(message.get("token_count") or "").lstrip("-").isdigit()
            )

        started_at = number("started_at")
        ended_at = number("ended_at")
        execution_seconds = max(0.0, ended_at - started_at) if started_at and ended_at else 0.0
        tool_steps = sum(1 for message in messages if message.get("role") == "tool")
        actual_cost = session.get("actual_cost_usd")
        cost = number("actual_cost_usd") if actual_cost is not None else number("estimated_cost_usd")
        model_config = session.get("model_config")
        context = (
            model_config.get("brain4all_context")
            if isinstance(model_config, Mapping)
            else {}
        )
        context = context if isinstance(context, Mapping) else {}
        try:
            context_used = max(0, int(context.get("used") or 0))
        except (TypeError, ValueError):
            context_used = 0
        try:
            context_limit = max(0, int(context.get("limit") or 0))
        except (TypeError, ValueError):
            context_limit = 0
        context_payload: dict[str, Any] = {"used": context_used}
        if context_limit:
            context_payload.update({
                "limit": context_limit,
                "percent": round(min(100.0, context_used / context_limit * 100), 2),
            })

        return {
            "conversation_id": conversation_id,
            "api_calls": integer("api_call_count"),
            "duration": f"{execution_seconds:.2f}s",
            "execution_seconds": round(execution_seconds, 3),
            "messages": len(messages),
            "steps": tool_steps,
            "tool_calls": tool_steps,
            "model": str(session.get("model") or ""),
            "source": str(session.get("source") or ""),
            "tokens": {
                "cache_read": cache_read_tokens,
                "cache_write": cache_write_tokens,
                "input": input_tokens,
                "output": output_tokens,
                "reasoning": reasoning_tokens,
                "total": total_tokens,
            },
            "cost": {
                "source": str(session.get("cost_source") or ""),
                "status": str(session.get("cost_status") or ""),
                "total_usd": cost,
            },
            "context": context_payload,
        }

    def rename_conversation(self, agent_id: str, conversation_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        payload = self.agents.update_conversation(agent_id, conversation_id, {"title": body.get("title")})
        return self._conversation_dto(agent_id, payload["conversation"])

    def delete_conversation(self, agent_id: str, conversation_id: str) -> dict[str, Any]:
        return self.agents.delete_conversation(agent_id, conversation_id)

    async def stream_conversation(self, agent_id: str, conversation_id: str, body: Mapping[str, Any]):
        payload = dict(body)
        payload["message"] = payload.pop("input", "")
        payload["conversation_id"] = conversation_id
        async for event in self.agents.chat_stream(agent_id, payload):
            yield event

    async def stop_run(self, run_id: str) -> dict[str, Any]:
        return await self.agents.stop_run(run_id)

    def resolve_approval(
        self,
        run_id: str,
        body: Mapping[str, Any],
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        subsystem = str(body.get("subsystem") or "").strip().lower()
        choice = str(body.get("choice") or "").lower()
        resolution = dict(body)
        if choice == "always" and subsystem in {"skills", "memory"}:
            # Write gates are booleans, not command allowlists: approve this
            # write once, then disable the selected gate for future writes.
            resolution["choice"] = "once"
        result = self.agents.resolve_approval(run_id, resolution)
        if choice == "always" and agent_id and subsystem in {"skills", "memory"}:
            field = f"{subsystem}_write_approval"
            self.update_agent_config(agent_id, {field: False})
            result = {**result, "choice": "always", "subsystem": subsystem, "write_approval_disabled": True}
        return result

    def export_bundle(self, body: Mapping[str, Any]) -> tuple[bytes, str]:
        return self.portability.export(body)

    def inspect_bundle(self, payload: bytes) -> dict[str, Any]:
        return self.portability.inspect(payload)

    def dry_run_bundle(self, payload: bytes) -> dict[str, Any]:
        return self.portability.dry_run(payload)

    def apply_bundle(self, payload: bytes) -> dict[str, Any]:
        result = self.portability.apply(payload)
        self.agents.sync_profiles_registry()
        return result

    def start_bundle_export(self, body: Mapping[str, Any]) -> dict[str, Any]:
        return self.portability.start_export(body)

    def bundle_export_part(self, transfer_id: str, part_number: str) -> tuple[bytes, dict[str, Any]]:
        return self.portability.read_export_part(transfer_id, part_number)

    def start_bundle_upload(self, body: Mapping[str, Any]) -> dict[str, Any]:
        return self.portability.start_upload(body)

    def put_bundle_upload_part(self, transfer_id: str, part_number: str, payload: bytes) -> dict[str, Any]:
        return self.portability.put_upload_part(transfer_id, part_number, payload)

    def complete_bundle_upload(self, transfer_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        return self.portability.complete_upload(transfer_id, body)

    def apply_bundle_upload(self, transfer_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        result = self.portability.apply_upload(transfer_id, body)
        self.agents.sync_profiles_registry()
        return result

    def delete_bundle_transfer(self, kind: str, transfer_id: str) -> dict[str, Any]:
        return self.portability.delete_transfer(kind, transfer_id)

    def list_crons(self) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self.kanban.list_tasks(
                "default",
                {"include_archived": "true", "limit": 200, "offset": offset},
            )
            tasks.extend(page["tasks"])
            offset += len(page["tasks"])
            if offset >= int(page["total"]) or not page["tasks"]:
                break
        return [
            self._schedule_job(task)
            for task in tasks
            if task.get("schedule") is not None and not task.get("archived")
        ]

    def get_job_detail(self, cron_id: str) -> dict[str, Any]:
        task = self.kanban.get_task("default", cron_id)
        if task.get("schedule") is None or task.get("archived"):
            raise ServiceError("cron job not found", status=404, code="cron_not_found")
        return {"job": self._schedule_job(task), "run": None}

    def create_cron(self, body: Mapping[str, Any]) -> dict[str, Any]:
        agent_id = str(body.get("agent_id") or "").strip()
        self.agents.describe_agent(agent_id, include_memory=False)
        prompt = str(body.get("prompt") or "").strip()
        if not prompt:
            raise ServiceError("prompt is required")
        interval = int(body.get("interval_minutes") or 0)
        raw_schedule = str(body.get("schedule") or "").strip()
        timezone_name = str(body.get("timezone") or "Etc/UTC")
        if str(body.get("mode") or "local") != "local":
            raise ServiceError("only local cron jobs are supported")
        recurrence = "interval"
        if interval <= 0 and raw_schedule:
            try:
                parsed = datetime.fromisoformat(raw_schedule.replace("Z", "+00:00"))
            except ValueError:
                seconds = self._schedule_seconds(raw_schedule)
                interval = max(1, seconds // 60)
            else:
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                recurrence = "once"
                scheduled_at = parsed.astimezone(timezone.utc).isoformat()
        if recurrence == "interval":
            if interval <= 0:
                raise ServiceError("interval_minutes or an ISO scheduled time is required")
            scheduled_at = (
                datetime.now(timezone.utc) + timedelta(minutes=interval)
            ).isoformat()
        template = self.kanban.create_task(
            "default",
            {
                "title": str(body.get("name") or "Scheduled task").strip(),
                "description": prompt,
                "status": "scheduled",
                "priority": "medium",
                "assignee": agent_id,
                "schedule": {
                    "recurrence": recurrence,
                    "scheduled_at": scheduled_at,
                    "timezone": timezone_name,
                    "interval_minutes": interval if recurrence == "interval" else None,
                },
            },
            created_by=agent_id,
        )
        return self._schedule_job(template)

    def set_cron_enabled(self, cron_id: str, enabled: bool) -> dict[str, Any]:
        task = self.kanban.schedule_action(
            "default",
            cron_id,
            "resume" if enabled else "pause",
        )
        return self._schedule_job(task)

    def delete_cron(self, cron_id: str) -> dict[str, Any]:
        self.kanban.archive_task("default", cron_id)
        return {"deleted": True}

    def run_cron(self, cron_id: str) -> dict[str, Any]:
        task = self.kanban.schedule_action("default", cron_id, "run_now")
        return {
            "job": self._schedule_job(task),
            "run": {
                "id": f"pending-{cron_id}",
                "state": "running",
                "triggered_at": iso(),
            },
        }

    @staticmethod
    def _schedule_job(task: Mapping[str, Any]) -> dict[str, Any]:
        schedule = dict(task.get("schedule") or {})
        interval = schedule.get("interval_minutes")
        return {
            "id": str(task["id"]),
            "agent_id": task.get("assignee"),
            "name": str(task.get("title") or "Scheduled task"),
            "prompt": str(task.get("description") or ""),
            "schedule": (
                f"@every {interval}m"
                if schedule.get("recurrence") == "interval"
                else schedule.get("next_run_at")
            ),
            "timezone": str(schedule.get("timezone") or "Etc/UTC"),
            "enabled": bool(schedule.get("enabled")),
            "next_run_at": schedule.get("next_run_at"),
            "last_run_at": schedule.get("last_run_at"),
            "mode": "local",
            "kanban_board": "default",
            "kanban_task_id": str(task["id"]),
        }

    @staticmethod
    def _schedule_seconds(schedule: str) -> int:
        match = _EVERY.fullmatch(schedule.strip())
        if not match:
            raise ServiceError("schedule must use @every <number>s|m|h|d")
        value = int(match.group(1))
        return value * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2)]

    @staticmethod
    def _team_with_description(team: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(team)
        if not str(result.get("description") or "").strip():
            count = len(result.get("members") or []) + 1
            result["description"] = (
                f"A coordinated team of {count} agents for multi-stage work."
            )
        return result

    def list_teams(self) -> list[dict[str, Any]]:
        return [
            self._team_with_description(team)
            for team in self.repository.list_teams()
        ]

    def get_team(self, team_id: str) -> dict[str, Any]:
        return self._team_with_description(self.repository.get_team(team_id))

    def create_team(self, body: Mapping[str, Any]) -> dict[str, Any]:
        return self._put_team(uuid.uuid4().hex, body, created_at=iso())

    def update_team(self, team_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        current = self.repository.get_team(team_id)
        payload = dict(body)
        if "description" not in payload:
            payload["description"] = current.get("description")
        return self._put_team(team_id, payload, created_at=current["created_at"])

    def delete_team(self, team_id: str) -> dict[str, Any]:
        if not self.repository.delete_team(team_id):
            raise ServiceError("team not found", status=404, code="not_found")
        self.repository.delete_team_runs(team_id)
        return {"deleted": True}

    def _build_team_workflow(self, team: Mapping[str, Any], body: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Build and validate the executable workflow for a team run.

        Shared by the legacy sync ``run_team`` and the async ``TeamRunService`` so
        both paths schedule one identical DAG. Raises the same synchronous 400s as
        before on invalid workflows.
        """
        task = str(body.get("task") or "").strip()
        raw_workflow = body.get("workflow") or team.get("workflow") or []
        if not task and not raw_workflow:
            raise ServiceError("task or workflow is required")
        members = [item for item in team["members"] if item.get("enabled", True)]
        configured = {
            str(item["agent_id"]): {
                "agent_id": str(item["agent_id"]),
                "role": str(item["role"]),
                "allowed_tools": [tool for tool in item.get("allowed_tools", []) if tool in SAFE_TOOLSETS],
            }
            for item in members
        }
        configured.setdefault(str(team["orchestrator_id"]), {
            "agent_id": str(team["orchestrator_id"]),
            "role": "coordinator",
            "allowed_tools": ["todo"],
        })
        if raw_workflow:
            workflow = self._team_workflow(raw_workflow, configured, members, task)
        else:
            if not members:
                raise ServiceError("team has no enabled workers", status=409, code="team_has_no_workers")
            workflow = [
                {
                    "id": f"worker-{index + 1}",
                    "task": task,
                    "needs": [],
                    "skills": self._enabled_team_skills(str(member["agent_id"])),
                    **configured[str(member["agent_id"])],
                }
                for index, member in enumerate(members)
            ]
        self._validate_team_workflow(workflow)
        return workflow

    async def run_team(self, team_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """Legacy synchronous run: execute the DAG inline via the shared engine and
        return the historical response dict. Sync runs also persist a run record."""
        record = await self.team_runs.run_sync(team_id, body)
        results = [self._legacy_step_result(step) for step in record["steps"]]
        return {
            "team_id": team_id,
            "member_results": results,
            "workflow_results": results,
            "orchestrator_summary": record["orchestrator_summary"],
            "started_at": record["started_at"],
            "completed_at": record["ended_at"],
        }

    @staticmethod
    def _legacy_step_result(step: Mapping[str, Any]) -> dict[str, Any]:
        base = {
            "id": step["id"], "agent_id": step["agent_id"], "role": step["role"],
            "needs": list(step["needs"]), "status": step["status"],
        }
        if step["status"] == "failed":
            base["error"] = step["error"]
        else:
            base["summary"] = step["summary"]
        return base

    def get_mcp(self, agent_id: str) -> dict[str, Any]:
        return self.agents.get_mcp(agent_id)

    def update_mcp(self, agent_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        servers = body.get("servers", {})
        if not isinstance(servers, Mapping):
            raise ServiceError("servers must be an object")
        path = self._agent_profile_path(agent_id) / "config.yaml"
        if path.is_file():
            self.repository.snapshot(agent_id, "config", "config", path.read_bytes())
        try:
            result = self.agents.update_mcp(agent_id, servers)
        except AgentAPIError as exc:
            raise ServiceError(str(exc), status=exc.status, code=exc.code) from exc
        # Preserve the pre-native MCP file contract for existing profile
        # exports and older Brain4All consumers while Hermes reads config.yaml.
        self.repository.atomic_json(
            self._agent_profile_path(agent_id) / "mcp.json",
            {"servers": copy.deepcopy(dict(servers))},
        )
        return result

    def _list_root_snapshots(self, kind: str | None = None) -> list[dict[str, Any]]:
        root = self.config.root_profile / "snapshots"
        if not root.is_dir():
            return []
        result = []
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(self.config.root_profile)
            parts = relative.parts
            if len(parts) < 3 or parts[0] != "snapshots":
                continue
            current_kind = parts[1]
            if current_kind not in {"memory", "skills", "config"}:
                continue
            if kind and current_kind != kind:
                continue
            target = "config" if current_kind == "config" else path.parent.name
            payload = path.read_bytes()
            result.append({
                "id": path.stem,
                "agent_id": BIG_BROTHER_AGENT_ID,
                "kind": current_kind,
                "target": target,
                "hash": hashlib.sha256(payload).hexdigest(),
                "path": relative.as_posix(),
                "created_at": path.stat().st_mtime,
            })
        return sorted(result, key=lambda item: item["created_at"], reverse=True)

    def _restore_root_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", snapshot_id):
            raise StoreError("invalid snapshot id")
        matches = [
            item
            for item in self._list_root_snapshots()
            if item["id"] == snapshot_id
        ]
        if not matches:
            raise StoreError("snapshot not found", status=404, code="not_found")
        item = matches[0]
        source = self.config.root_profile / item["path"]
        relative = Path(item["path"])
        if item["kind"] == "config":
            destination = self.config.root_profile / "config.yaml"
        elif item["kind"] == "memory":
            destination = self.config.root_profile / "memories" / "MEMORY.md"
        else:
            skill_relative = Path(*relative.parts[2:-1])
            destination = self.config.root_profile / "skills" / skill_relative / "SKILL.md"
        self.repository.atomic_write(destination, source.read_bytes(), mode=0o640)
        return item

    async def providers(self) -> list[dict[str, Any]]:
        try:
            connections = (await self.router.list_connections())["connections"]
            models = (await self.router.list_models())["data"]
        except NineRouterAPIError:
            connections, models = [], []
        result = []
        for provider in SUPPORTED_PROVIDERS:
            provider_rows = sorted(
                (item for item in connections if item.get("provider") == provider),
                key=lambda item: (int(item.get("priority") or 0), str(item.get("name") or "")),
            )
            active_rows = [item for item in provider_rows if item.get("active") is not False]
            primary = active_rows[0] if active_rows else (provider_rows[0] if provider_rows else {})
            result.append({
                "id": provider, "display_name": provider.title(), "provider_type": provider,
                "description": "Credentials are managed by the local 9router runtime.",
                "connection_mode": "api-key" if provider in API_KEY_PROVIDERS else "cli",
                "connected": bool(active_rows),
                "status": "connected" if active_rows else "disconnected",
                "last_test_status": primary.get("test_status", "unknown"),
                "default_model": primary.get("default_model", ""),
                "connection_count": len(provider_rows),
                "available_models": [item["id"] for item in models if item.get("provider") == provider],
            })
        return result

    async def provider_status(self, provider: str) -> dict[str, Any]:
        self._provider(provider)
        items = await self.providers()
        current = next(item for item in items if item["id"] == provider)
        return {"provider_id": provider, "connection_mode": current["connection_mode"], "connected": current["connected"], "status": current["status"], "default_model": current["default_model"], "available_models": current["available_models"]}

    async def start_provider_connect(self, provider: str) -> dict[str, Any]:
        self._provider(provider)
        if provider in API_KEY_PROVIDERS:
            return self._api_key_info(provider)
        redirect = "http://localhost:1455/auth/callback" if provider == "codex" else "http://localhost:20128/callback"
        payload = await self.router.oauth(provider, "authorize", method="GET", query_string=f"redirect_uri={redirect}")
        self._oauth_attempts[provider] = {"code_verifier": str(payload.get("codeVerifier") or ""), "state": str(payload.get("state") or ""), "redirect_uri": redirect}
        url = str(payload.get("authUrl") or "")
        if not url:
            raise ServiceError("9router did not return an authorization URL", status=502, code="router_error")
        return {"provider_id": provider, "connection_mode": "cli", "required_client_action": "submit_response", "login_url": url, "verification_url": url, "instructions": "Authorize in the browser, then paste the complete callback URL.", "text_label": "Callback URL", "status": "waiting_for_user"}

    async def submit_provider_connect(self, provider: str, body: Mapping[str, Any]) -> dict[str, Any]:
        self._provider(provider)
        value = str(body.get("text") or body.get("response_text") or body.get("token") or body.get("api_key") or "").strip()
        if not value:
            raise ServiceError("provider credential or callback is required")
        if provider in API_KEY_PROVIDERS:
            await self.router.create_api_key_connection({"provider": provider, "api_key": value})
            return {**self._api_key_info(provider), "connected": True, "status": "connected"}
        from urllib.parse import parse_qs, urlparse
        attempt = self._oauth_attempts.get(provider)
        if not attempt:
            raise ServiceError("provider connection has not been started", status=409, code="connect_not_started")
        parsed = urlparse(value)
        query = parse_qs(parsed.query) if parsed.scheme else {}
        code = str((query.get("code") or [value])[0])
        state = str((query.get("state") or [attempt["state"]])[0])
        if attempt["state"] and state != attempt["state"]:
            raise ServiceError("provider callback state does not match", status=400, code="oauth_state_mismatch")
        payload = await self.router.oauth(provider, "exchange", method="POST", body={"code": code, "redirectUri": attempt["redirect_uri"], "codeVerifier": attempt["code_verifier"], "state": state})
        if not payload.get("success"):
            raise ServiceError("provider authorization was not accepted", status=422, code="provider_auth_failed")
        self._oauth_attempts.pop(provider, None)
        return {"provider_id": provider, "connection_mode": "cli", "connected": True, "status": "connected"}

    async def update_provider(self, provider: str, body: Mapping[str, Any]) -> dict[str, Any]:
        self._provider(provider)
        if provider not in API_KEY_PROVIDERS:
            raise ServiceError("OAuth providers must be updated through connect")
        result = await self.router.create_api_key_connection({"provider": provider, "api_key": body.get("api_key"), "name": body.get("display_name"), "default_model": body.get("default_model")})
        return {"provider_id": provider, "connected": True, "status": "connected", "connection": result}

    async def disconnect_provider(self, provider: str) -> dict[str, Any]:
        self._provider(provider)
        connections = (await self.router.list_connections())["connections"]
        for item in connections:
            if item.get("provider") == provider:
                await self.router.delete_connection(item["id"])
        self._oauth_attempts.pop(provider, None)
        return {"provider_id": provider, "connected": False, "status": "disconnected"}

    async def test_provider(self, provider: str) -> dict[str, Any]:
        self._provider(provider)
        rows = await self._provider_connections(provider)
        current = next(
            (item for item in rows if item.get("active") is not False),
            rows[0] if rows else None,
        )
        if not current:
            return {"provider_id": provider, "healthy": False, "status": "not_connected", "message": "Provider is not connected"}
        result = await self.router.test_connection(current["id"])
        return {"provider_id": provider, "healthy": bool(result.get("valid")), "status": "healthy" if result.get("valid") else "unhealthy", "message": result.get("error") or ""}

    async def list_provider_connections(self, provider: str) -> dict[str, Any]:
        self._provider(provider)
        connections = await self._provider_connections(provider)
        return {
            "provider_id": provider,
            "connected": any(item.get("active") is not False for item in connections),
            "connections": connections,
        }

    async def add_provider_connection(self, provider: str, body: Mapping[str, Any]) -> dict[str, Any]:
        self._provider(provider)
        if provider not in API_KEY_PROVIDERS:
            raise ServiceError(
                "use the provider connect flow to add an OAuth account",
                status=400, code="oauth_connect_required",
            )
        result = await self.router.create_api_key_connection({
            "provider": provider,
            "api_key": body.get("api_key"),
            "name": body.get("name"),
            "default_model": body.get("default_model"),
        })
        return {"provider_id": provider, "connected": True, "connection": result["connection"]}

    async def patch_provider_connection(self, provider: str, connection_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        await self._owned_connection(provider, connection_id)
        active = body.get("active")
        priority = body.get("priority")
        if active is None and priority is None:
            raise ServiceError("active or priority is required", code="invalid_request")
        if priority is not None:
            priority = max(0, min(999, int(priority)))
        await self.router.update_connection(connection_id, active=active, priority=priority)
        # 9router owns priority normalization, so return the stored row, not the request.
        refreshed = await self._owned_connection(provider, connection_id)
        return {"provider_id": provider, "connection": refreshed}

    async def test_provider_connection(self, provider: str, connection_id: str) -> dict[str, Any]:
        await self._owned_connection(provider, connection_id)
        result = await self.router.test_connection(connection_id)
        healthy = bool(result.get("valid"))
        return {
            "provider_id": provider, "connection_id": connection_id,
            "healthy": healthy, "status": "healthy" if healthy else "unhealthy",
            "message": result.get("error") or "",
        }

    async def delete_provider_connection(self, provider: str, connection_id: str) -> dict[str, Any]:
        await self._owned_connection(provider, connection_id)
        await self.router.delete_connection(connection_id)
        remaining = await self._provider_connections(provider)
        return {
            "provider_id": provider, "connection_id": connection_id, "deleted": True,
            "connected": any(item.get("active") is not False for item in remaining),
        }

    async def connection_usage(self, provider: str, connection_id: str) -> dict[str, Any]:
        await self._owned_connection(provider, connection_id)
        payload = await self.router.usage_for_connection(connection_id)
        return {"provider_id": provider, **payload}

    async def _provider_connections(self, provider: str) -> list[dict[str, Any]]:
        connections = (await self.router.list_connections())["connections"]
        rows = [item for item in connections if item.get("provider") == provider]
        rows.sort(key=lambda item: (int(item.get("priority") or 0), str(item.get("name") or "")))
        return rows

    async def _owned_connection(self, provider: str, connection_id: str) -> dict[str, Any]:
        self._provider(provider)
        rows = await self._provider_connections(provider)
        current = next((item for item in rows if item.get("id") == connection_id), None)
        if current is None:
            raise ServiceError("connection not found", status=404, code="not_found")
        return current

    @staticmethod
    def _provider(provider: str) -> str:
        if provider not in SUPPORTED_PROVIDERS:
            raise ServiceError("provider not found", status=404, code="not_found")
        return provider

    @staticmethod
    def _api_key_info(provider: str) -> dict[str, Any]:
        return {"provider_id": provider, "provider_type": provider, "connection_mode": "api-key", "required_client_action": "submit_text", "instructions": "Enter the provider API key. It is stored by 9router, not Brain4All.", "text_label": "API key", "status": "waiting_for_user"}

    def _team_workflow(
        self,
        raw_workflow: Any,
        configured: Mapping[str, Mapping[str, Any]],
        members: list[Mapping[str, Any]],
        parent_task: str,
    ) -> list[dict[str, Any]]:
        if not isinstance(raw_workflow, list) or not raw_workflow or len(raw_workflow) > 64:
            raise ServiceError("workflow must contain between 1 and 64 steps", code="invalid_workflow")
        workers = [str(item["agent_id"]) for item in members]
        workflow: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_workflow):
            if not isinstance(raw, Mapping):
                raise ServiceError("workflow steps must be objects", code="invalid_workflow")
            step_id = str(raw.get("id") or "").strip()
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", step_id):
                raise ServiceError("workflow step id is invalid", code="invalid_workflow")
            step_task = str(raw.get("task") or raw.get("goal") or parent_task).strip()
            if not step_task:
                raise ServiceError("workflow step task is required", code="invalid_workflow")
            requested_agent = str(raw.get("agent_id") or "").strip()
            requested_role = str(raw.get("role") or "").strip()
            if not requested_agent and requested_role:
                requested_agent = next(
                    (agent_id for agent_id, item in configured.items() if item["role"] == requested_role),
                    "",
                )
            if not requested_agent:
                if not workers:
                    raise ServiceError("workflow has no available worker", status=409, code="team_has_no_workers")
                requested_agent = workers[index % len(workers)]
            assignment = configured.get(requested_agent)
            if assignment is None:
                raise ServiceError("workflow references an agent outside the team", code="invalid_workflow_agent")
            allowed = list(assignment["allowed_tools"])
            requested_tools = raw.get("allowed_tools")
            if requested_tools is not None:
                if not isinstance(requested_tools, list):
                    raise ServiceError("allowed_tools must be a list", code="invalid_workflow")
                if any(str(tool) not in SAFE_TOOLSETS for tool in requested_tools):
                    raise ServiceError("workflow step contains a privileged toolset", code="invalid_workflow_tools")
                if allowed and any(str(tool) not in allowed for tool in requested_tools):
                    raise ServiceError("workflow step exceeds its assigned tool policy", code="invalid_workflow_tools")
                allowed = sorted({str(tool) for tool in requested_tools})
            needs = raw.get("needs") or []
            if not isinstance(needs, list):
                raise ServiceError("workflow needs must be a list", code="invalid_workflow")
            workflow.append({
                "id": step_id,
                "task": step_task,
                "agent_id": requested_agent,
                "role": requested_role or str(assignment["role"]),
                "allowed_tools": allowed,
                "skills": (
                    self._enabled_team_skills(requested_agent)
                    if raw.get("skills") is None
                    else self._team_step_skills(raw.get("skills"))
                ),
                "needs": [str(item).strip() for item in needs],
            })
        return workflow

    @staticmethod
    def _validate_team_workflow(workflow: list[Mapping[str, Any]]) -> None:
        ids = [str(step["id"]) for step in workflow]
        if len(set(ids)) != len(ids):
            raise ServiceError("workflow step ids must be unique", code="invalid_workflow")
        known = set(ids)
        remaining = set(ids)
        completed: set[str] = set()
        by_id = {str(step["id"]): step for step in workflow}
        for step in workflow:
            needs = list(step.get("needs") or [])
            if len(set(needs)) != len(needs) or str(step["id"]) in needs:
                raise ServiceError("workflow dependencies are invalid", code="invalid_workflow")
            if any(need not in known for need in needs):
                raise ServiceError("workflow dependency does not exist", code="invalid_workflow_dependency")
        while remaining:
            ready = {step_id for step_id in remaining if all(need in completed for need in by_id[step_id].get("needs", []))}
            if not ready:
                raise ServiceError("workflow contains a dependency cycle", code="workflow_cycle")
            completed.update(ready)
            remaining.difference_update(ready)

    def _put_team(self, team_id: str, body: Mapping[str, Any], *, created_at: str) -> dict[str, Any]:
        name = str(body.get("name") or "").strip()
        orchestrator = str(body.get("orchestrator_id") or "").strip()
        if not name or not orchestrator:
            raise ServiceError("name and orchestrator_id are required")
        self.agents.describe_agent(orchestrator, include_memory=False)
        synthesis_agent = str(body.get("synthesis_agent_id") or orchestrator).strip()
        self.agents.describe_agent(synthesis_agent, include_memory=False)
        seen: set[str] = set()
        members = []
        for raw in body.get("members") or []:
            agent_id = str(raw.get("agent_id") or "").strip()
            role = str(raw.get("role") or "").strip()
            if not agent_id or not role or agent_id in seen:
                raise ServiceError("team members require unique agent_id and role")
            self.agents.describe_agent(agent_id, include_memory=False)
            seen.add(agent_id)
            raw_tools = raw.get("allowed_tools")
            tools = sorted(set(raw_tools if raw_tools is not None else []))
            if any(tool not in SAFE_TOOLSETS for tool in tools):
                raise ServiceError("team member contains a privileged toolset")
            members.append({"agent_id": agent_id, "role": role, "allowed_tools": tools, "enabled": bool(raw.get("enabled", True))})
        configured = {
            str(item["agent_id"]): {
                "agent_id": str(item["agent_id"]),
                "role": str(item["role"]),
                "allowed_tools": list(item["allowed_tools"]),
            }
            for item in members
        }
        configured.setdefault(orchestrator, {
            "agent_id": orchestrator,
            "role": "coordinator",
            "allowed_tools": ["todo"],
        })
        workflow = []
        if body.get("workflow"):
            workflow = self._team_workflow(body["workflow"], configured, members, "")
            self._validate_team_workflow(workflow)
        team = {
            "id": team_id,
            "user_id": "local",
            "name": name,
            "description": (
                str(body.get("description") or "").strip()
                or f"A coordinated team of {len(members) + 1} agents for multi-stage work."
            ),
            "orchestrator_id": orchestrator,
            "coordinator_prompt": (
                str(body.get("coordinator_prompt") or "").strip()
                or DEFAULT_TEAM_COORDINATOR_PROMPT
            ),
            "coordinator_allowed_tools": self._team_toolsets(body.get("coordinator_allowed_tools")),
            "coordinator_skills": (
                self._enabled_team_skills(orchestrator)
                if body.get("coordinator_skills") is None
                else self._team_step_skills(body.get("coordinator_skills"))
            ),
            "synthesis_agent_id": synthesis_agent,
            "synthesis_allowed_tools": self._team_toolsets(body.get("synthesis_allowed_tools")),
            "synthesis_skills": (
                self._enabled_team_skills(synthesis_agent)
                if body.get("synthesis_skills") is None
                else self._team_step_skills(body.get("synthesis_skills"))
            ),
            "members": members,
            "workflow": workflow,
            "shared_workspace": bool(body.get("shared_workspace", False)),
            "communication_level": max(0, min(3, int(body.get("communication_level", 1)))),
            "synthesis_instruction": (
                str(body.get("synthesis_instruction") or "").strip()
                or DEFAULT_TEAM_SYNTHESIS_PROMPT
            ),
            "max_parallel": max(1, int(body.get("max_parallel") or 1)),
            "max_depth": max(1, int(body.get("max_depth") or 1)),
            "enabled": bool(body.get("enabled", True)),
            "created_at": created_at,
            "updated_at": iso(),
        }
        return self.repository.put_team(team)

    @staticmethod
    def _team_step_skills(value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ServiceError("skills must be a list", code="invalid_workflow")
        skills = sorted({str(item).strip() for item in value if str(item).strip()})
        if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", skill) for skill in skills):
            raise ServiceError("workflow skill id is invalid", code="invalid_workflow")
        return skills

    def _enabled_team_skills(self, agent_id: str) -> list[str]:
        try:
            skills = self.agents.list_skills(agent_id).get("skills", [])
        except (AgentAPIError, StoreError):
            return []
        return sorted({
            str(item.get("skill_id") or "").strip()
            for item in skills
            if item.get("enabled", True) and str(item.get("skill_id") or "").strip()
        })

    @staticmethod
    def _team_toolsets(value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ServiceError("allowed tools must be a list", code="invalid_workflow")
        tools = sorted({str(item).strip() for item in value if str(item).strip()})
        if any(tool not in SAFE_TOOLSETS for tool in tools):
            raise ServiceError("team role contains a privileged toolset", code="invalid_workflow_tools")
        return tools

    @staticmethod
    def _conversation_dto(agent_id: str, item: Mapping[str, Any]) -> dict[str, Any]:
        return {"id": str(item.get("id") or item.get("session_id") or ""), "agent_id": agent_id, "title": str(item.get("title") or item.get("name") or "New Conversation"), "preview": str(item.get("preview") or ""), "model": str(item.get("model") or ""), "messages": int(item.get("message_count") or item.get("messages") or 0), "tools": int(item.get("tool_call_count") or item.get("tools") or 0), "created_at": item.get("created_at"), "updated_at": item.get("updated_at")}

    @staticmethod
    def _agent_dto(item: Mapping[str, Any]) -> dict[str, Any]:
        metadata = dict(item.get("metadata") or {})
        config = dict(item.get("config") or {})
        name = str(item.get("name") or item.get("profile_name") or "")
        display_name = str(metadata.get("display_name") or metadata.get("title") or metadata.get("name") or name)
        public_metadata = {
            "display_name": display_name,
            "description": str(metadata.get("description") or ""),
        }
        return {
            "id": name,
            "name": display_name,
            "display_name": display_name,
            "title": display_name,
            "description": str(metadata.get("description") or ""),
            "status": str(metadata.get("status") or "active"),
            "config": {
                "model": str(config.get("model") or "auto"),
                "reasoning_effort": str(config.get("effort") or "medium"),
                "approval_mode": str(config.get("approval_mode") or "on"),
                "skills_write_approval": bool(config.get("skills_write_approval", True)),
                "memory_write_approval": bool(config.get("memory_write_approval", True)),
            },
            "created_at": metadata.get("created_at"),
            "updated_at": metadata.get("updated_at"),
            "metadata": public_metadata,
        }


EXPECTED_ERRORS = (
    ServiceError,
    CronServiceError,
    StoreError,
    AgentAPIError,
    ConfigAPIError,
    NineRouterAPIError,
    WorkspacePreviewError,
    WorkspaceUploadError,
)
