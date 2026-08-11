"""Agent, profile, skill, memory, configuration, and snapshot service behavior."""

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

class AgentsServiceMixin:
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

    @cached_method("agents")
    def list_agents(self) -> list[dict[str, Any]]:
        return [self._agent_dto(item) for item in self.agents.list_agents()["agents"]]

    async def list_agents_async(self) -> list[dict[str, Any]]:
        if self._cache.contains("agents"):
            return self.list_agents()
        return await asyncio.to_thread(self.list_agents)

    async def ensure_default_agent(self) -> dict[str, Any]:
        """Expose and repair the root Hermes profile as Big Brother."""
        profile = self.config.root_profile
        profile.mkdir(parents=True, exist_ok=True)
        workspace = profile / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        self.agents._ensure_workspace_agents(profile, workspace)
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
        self._cache.invalidate("agents")
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
        self._cache.invalidate("agents")
        return self._agent_dto(raw)

    def get_agent(self, agent_id: str) -> dict[str, Any]:
        return self._agent_dto(
            self.agents.describe_agent(agent_id),
            include_soul=True,
        )

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
        self._cache.invalidate("agents")
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
        self._cache.invalidate("agents")
        return {
            "deleted": True,
            "recoverable": False,
            "kanban_tasks_deleted": deleted_tasks,
        }

    def global_config(self) -> dict[str, Any]:
        return self.config.get_config()

    def update_global_config(self, body: Mapping[str, Any]) -> dict[str, Any]:
        result = self.config.update_config(body)
        self._cache.invalidate("agents")
        return result

    def update_agent_config(self, agent_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        translated = dict(body)
        if "reasoning_effort" in translated:
            translated["effort"] = translated.pop("reasoning_effort")
        if self._is_big_brother(agent_id):
            result = self.config.update_config(translated)["config"]
        else:
            profile = self.repository.profile_path(agent_id)
            path = profile / "config.yaml"
            if path.is_file():
                self.repository.snapshot(agent_id, "config", "config", path.read_bytes())
            result = self.agents.update_config(agent_id, translated)["config"]
        self._cache.invalidate("agents")
        return result

    def list_skills(self, agent_id: str) -> list[dict[str, Any]]:
        if self._is_big_brother(agent_id):
            return self.config.list_skills()["skills"]
        return self.agents.list_skills(agent_id)["skills"]

    def list_default_skills(self) -> list[dict[str, Any]]:
        """List only the skills installed in the default Hermes profile."""
        return self._default_skills(self.config.list_skills()["skills"])

    @staticmethod
    def _default_skills(skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            item
            for item in skills
            if str(item.get("skill_id") or "") != BIG_BROTHER_SKILL_ID
            and Path(str(item.get("relative_path") or "")).parts[:1]
            != (BIG_BROTHER_SKILL_CATEGORY,)
        ]

    @staticmethod
    def _reject_control_skill(skill_id: str) -> None:
        if str(skill_id).strip() == BIG_BROTHER_SKILL_ID:
            raise ServiceError(
                "Big Brother control skill is restricted to Big Brother",
                status=403,
                code="protected_skill",
            )

    async def list_skills_overview(self) -> dict[str, Any]:
        """Return one fresh skills snapshot for the library and every agent."""
        agents = await self.list_agents_async()
        agent_ids = [str(item.get("id") or "").strip() for item in agents]
        agent_ids = [agent_id for agent_id in agent_ids if agent_id]
        profile_agent_ids = [
            agent_id
            for agent_id in agent_ids
            if not self._is_big_brother(agent_id)
        ]
        concurrency = asyncio.Semaphore(8)

        async def agent_skills(agent_id: str) -> tuple[str, list[dict[str, Any]]]:
            async with concurrency:
                skills = await asyncio.to_thread(self.list_skills, agent_id)
            return agent_id, skills

        results = await asyncio.gather(
            asyncio.to_thread(self.config.list_skills),
            *(agent_skills(agent_id) for agent_id in profile_agent_ids),
        )
        root_payload, *agent_results = results
        root_skills = root_payload["skills"]
        skills_by_agent = dict(agent_results)
        for agent_id in agent_ids:
            if self._is_big_brother(agent_id):
                skills_by_agent[agent_id] = root_skills
        return {
            "skills": self._default_skills(root_skills),
            "agents": skills_by_agent,
        }

    async def preview_skill_sync(self, body: Mapping[str, Any]) -> dict[str, Any]:
        agent_ids = await self._skill_sync_agent_ids(body)
        return await asyncio.to_thread(self.agents.preview_common_skill_sync, agent_ids)

    async def sync_skills(self, body: Mapping[str, Any]) -> dict[str, Any]:
        agent_ids = await self._skill_sync_agent_ids(body)
        revision = str(body.get("expected_source_revision") or "").strip()
        if not revision:
            raise ServiceError(
                "expected_source_revision is required",
                status=422,
                code="skills_sync_revision_required",
            )
        preview = await asyncio.to_thread(self.agents.preview_common_skill_sync, agent_ids)
        if preview["source_revision"] != revision:
            raise ServiceError(
                "Common skills changed after the preview. Review the sync again.",
                status=409,
                code="skills_sync_stale",
            )
        for plan in preview["agents"]:
            agent_id = plan["agent_id"]
            profile_dir = self.agents.profile_path(agent_id)
            current = {
                str(item.get("skill_id") or ""): item
                for item in self.agents.list_skills(agent_id)["skills"]
            }
            for skill_id in [*plan["updated"], *plan["removed"]]:
                relative_path = str(current.get(skill_id, {}).get("path") or "")
                skill_file = profile_dir / "skills" / relative_path / "SKILL.md"
                if skill_file.is_file():
                    self.repository.snapshot(agent_id, "skills", skill_id, skill_file.read_bytes())
        result = await asyncio.to_thread(
            self.agents.sync_common_skills,
            agent_ids,
            revision,
        )
        return result

    async def _skill_sync_agent_ids(self, body: Mapping[str, Any]) -> list[str]:
        requested = body.get("agent_ids")
        if not isinstance(requested, list):
            raise ServiceError("agent_ids is required", status=422, code="invalid_agent_ids")
        agent_ids = list(dict.fromkeys(str(value).strip() for value in requested if str(value).strip()))
        if not agent_ids:
            raise ServiceError("select at least one agent", status=422, code="invalid_agent_ids")
        known = {
            str(item.get("id") or "").strip()
            for item in await self.list_agents_async()
        }
        invalid = [agent_id for agent_id in agent_ids if agent_id not in known or self._is_big_brother(agent_id)]
        if invalid:
            raise ServiceError(
                f"agents cannot be synchronized: {', '.join(invalid)}",
                status=422,
                code="invalid_agent_ids",
            )
        return agent_ids

    async def install_default_skill(self, body: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Install a URL, hub identifier, or local SKILL.md into the root profile."""
        requested_id = str(body.get("skill_id") or body.get("name") or "").strip()
        self._reject_control_skill(requested_id)
        result = await self.config.install_skill({
            **dict(body),
            "category": CUSTOM_SKILL_CATEGORY,
            "enable": False,
        })
        return self._default_skills(result["skills"])

    def set_default_skill_enabled(self, skill_id: str, body: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Change whether new profiles inherit a default-profile skill."""
        self._reject_control_skill(skill_id)
        result = self.config.set_skill_enabled(skill_id, body)
        return self._default_skills(result["skills"])

    async def install_skill(self, agent_id: str, body: Mapping[str, Any]) -> list[dict[str, Any]]:
        install_body = dict(body)
        requested_id = str(
            install_body.get("skill_id") or install_body.get("name") or ""
        ).strip()
        if not self._is_big_brother(agent_id):
            self._reject_control_skill(requested_id)
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
        self._reject_control_skill(skill_id)
        payload = self.agents.set_skill_enabled(agent_id, skill_id, body)
        path = self.repository.profile_path(agent_id) / "skills" / skill_id / "SKILL.md"
        if path.is_file():
            self.repository.snapshot(agent_id, "skills", skill_id, path.read_bytes())
        return payload["skills"]

    def remove_skill(self, agent_id: str, skill_id: str) -> list[dict[str, Any]]:
        if self._is_big_brother(agent_id):
            return self.config.delete_skill(skill_id)["skills"]
        self._reject_control_skill(skill_id)
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
            result = self._restore_root_snapshot(snapshot_id)
        else:
            result = self.repository.restore_snapshot(agent_id, snapshot_id)
        self._cache.invalidate("agents")
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

    @staticmethod
    def _agent_dto(
        item: Mapping[str, Any],
        *,
        include_soul: bool = False,
    ) -> dict[str, Any]:
        metadata = dict(item.get("metadata") or {})
        config = dict(item.get("config") or {})
        name = str(item.get("name") or item.get("profile_name") or "")
        display_name = str(metadata.get("display_name") or metadata.get("title") or metadata.get("name") or name)
        public_metadata = {
            "display_name": display_name,
            "description": str(metadata.get("description") or ""),
        }
        result = {
            "id": name,
            "name": display_name,
            "display_name": display_name,
            "title": display_name,
            "description": str(metadata.get("description") or ""),
            "status": str(metadata.get("status") or "active"),
            "config": {
                "model": str(config.get("model") or "auto"),
                "reasoning_effort": str(config.get("effort") or "medium"),
                "approval_mode": str(config.get("approval_mode") or "off"),
                "skills_write_approval": bool(config.get("skills_write_approval", False)),
                "memory_write_approval": bool(config.get("memory_write_approval", False)),
            },
            "created_at": metadata.get("created_at"),
            "updated_at": metadata.get("updated_at"),
            "metadata": public_metadata,
        }
        if include_soul:
            result["soul"] = str(item.get("soul") or "")
        return result

