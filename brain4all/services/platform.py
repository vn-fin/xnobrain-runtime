"""Platform service layer for agents, Hermes profiles, cron, MCP, teams, and snapshots."""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import re
import time
from typing import Any, Mapping
import uuid

import yaml

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


SUPPORTED_PROVIDERS = ("claude", "codex", "antigravity", "openai", "anthropic", "gemini")
API_KEY_PROVIDERS = frozenset({"openai", "anthropic", "gemini"})
SAFE_TOOLSETS = frozenset({
    "browser", "code_execution", "file", "image_gen", "terminal", "todo",
    "tts", "video", "video_gen", "vision", "web", "x_search",
})
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
        from .kanban import KanbanService
        self.kanban = KanbanService(agents)
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

    def update_agent_metadata(self, agent_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        profile = self.repository.profile_path(agent_id)
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
        target = self.repository.soft_delete_profile(agent_id)
        self.agents.sync_profiles_registry()
        return {"deleted": True, "recoverable": True, "trash_path": str(target)}

    def global_config(self) -> dict[str, Any]:
        return self.config.get_config()

    def update_global_config(self, body: Mapping[str, Any]) -> dict[str, Any]:
        return self.config.update_config(body)

    def update_agent_config(self, agent_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        profile = self.repository.profile_path(agent_id)
        path = profile / "config.yaml"
        if path.is_file():
            self.repository.snapshot(agent_id, "config", "config", path.read_bytes())
        translated = dict(body)
        if "reasoning_effort" in translated:
            translated["effort"] = translated.pop("reasoning_effort")
        return self.agents.update_config(agent_id, translated)["config"]

    def list_skills(self, agent_id: str) -> list[dict[str, Any]]:
        return self.agents.list_skills(agent_id)["skills"]

    def list_default_skills(self) -> list[dict[str, Any]]:
        """List only the skills installed in the default Hermes profile."""
        return self.config.list_skills()["skills"]

    async def install_default_skill(self, body: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Install a URL, hub identifier, or local SKILL.md into the root profile."""
        return (await self.config.install_skill(body))["skills"]

    async def install_skill(self, agent_id: str, body: Mapping[str, Any]) -> list[dict[str, Any]]:
        payload = await self.agents.install_skill(agent_id, body)
        skill_id = str(body.get("skill_id") or body.get("name") or "").strip()
        if skill_id and "content" in body:
            self.repository.snapshot(agent_id, "skills", skill_id, str(body["content"]).encode())
        return payload["skills"]

    def set_skill_enabled(self, agent_id: str, skill_id: str, body: Mapping[str, Any]) -> list[dict[str, Any]]:
        payload = self.agents.set_skill_enabled(agent_id, skill_id, body)
        path = self.repository.profile_path(agent_id) / "skills" / skill_id / "SKILL.md"
        if path.is_file():
            self.repository.snapshot(agent_id, "skills", skill_id, path.read_bytes())
        return payload["skills"]

    def remove_skill(self, agent_id: str, skill_id: str) -> list[dict[str, Any]]:
        path = self.repository.profile_path(agent_id) / "skills" / skill_id / "SKILL.md"
        if path.is_file():
            self.repository.snapshot(agent_id, "skills", skill_id, path.read_bytes())
        return self.agents.remove_skill(agent_id, skill_id)["skills"]

    def read_memory(self, agent_id: str) -> dict[str, Any]:
        return self.agents.read_memory(agent_id)

    def write_memory(self, agent_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        result = self.agents.write_memory(agent_id, body)
        memory = str(result.get("memory") or body.get("memory") or "")
        self.repository.snapshot(agent_id, "memory", "memory", memory.encode())
        return result

    def list_snapshots(self, agent_id: str, kind: str | None = None) -> list[dict[str, Any]]:
        return self.repository.list_snapshots(agent_id, kind)

    def restore_snapshot(self, agent_id: str, snapshot_id: str) -> dict[str, Any]:
        return self.repository.restore_snapshot(agent_id, snapshot_id)

    def list_workspace(self, agent_id: str, path: str = ".") -> dict[str, Any]:
        return self.agents.list_workspace(agent_id, {"path": path})

    def read_workspace(self, agent_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        return self.agents.read_workspace_file(agent_id, body)

    def write_workspace(self, agent_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        return self.agents.write_workspace_file(agent_id, body)

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
        payload = self.agents.create_conversation(agent_id, {"title": body.get("title") or "New conversation"})
        return self._conversation_dto(agent_id, payload["conversation"])

    def get_conversation(self, agent_id: str, conversation_id: str) -> dict[str, Any]:
        payload = self.agents.get_conversation(agent_id, conversation_id)
        result = self._conversation_dto(agent_id, payload["conversation"])
        result["messages"] = payload["messages"]
        return result

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

    async def run_cron(self, cron_id: str) -> dict[str, Any]:
        task = self.kanban.schedule_action("default", cron_id, "run_now")
        return {"job": self._schedule_job(task), "task": task}

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

    def list_teams(self) -> list[dict[str, Any]]:
        return self.repository.list_teams()

    def get_team(self, team_id: str) -> dict[str, Any]:
        return self.repository.get_team(team_id)

    def create_team(self, body: Mapping[str, Any]) -> dict[str, Any]:
        return self._put_team(uuid.uuid4().hex, body, created_at=iso())

    def update_team(self, team_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        current = self.repository.get_team(team_id)
        return self._put_team(team_id, body, created_at=current["created_at"])

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
        raw_workflow = body.get("workflow") or []
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
        configured[str(team["orchestrator_id"])] = {
            "agent_id": str(team["orchestrator_id"]),
            "role": "coordinator",
            "allowed_tools": ["todo"],
        }
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
        path = self.repository.profile_path(agent_id) / "mcp.json"
        if not path.is_file():
            return {"servers": {}}
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {"servers": {}}

    def update_mcp(self, agent_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        payload = copy.deepcopy(dict(body))
        if not isinstance(payload.get("servers", {}), dict):
            raise ServiceError("servers must be an object")
        path = self.repository.profile_path(agent_id) / "mcp.json"
        self.repository.atomic_json(path, payload)
        return payload

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
                if any(str(tool) not in allowed for tool in requested_tools):
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
        seen = {orchestrator}
        members = []
        for raw in body.get("members") or []:
            agent_id = str(raw.get("agent_id") or "").strip()
            role = str(raw.get("role") or "").strip()
            if not agent_id or not role or agent_id in seen:
                raise ServiceError("team members require unique agent_id and role")
            self.agents.describe_agent(agent_id, include_memory=False)
            seen.add(agent_id)
            tools = sorted(set(raw.get("allowed_tools") or ["web"]))
            if any(tool not in SAFE_TOOLSETS for tool in tools):
                raise ServiceError("team member contains a privileged toolset")
            members.append({"agent_id": agent_id, "role": role, "allowed_tools": tools, "enabled": bool(raw.get("enabled", True))})
        team = {"id": team_id, "user_id": "local", "name": name, "orchestrator_id": orchestrator, "members": members, "shared_workspace": False, "max_parallel": max(1, int(body.get("max_parallel") or 1)), "max_depth": max(1, int(body.get("max_depth") or 1)), "enabled": bool(body.get("enabled", True)), "created_at": created_at, "updated_at": iso()}
        return self.repository.put_team(team)

    @staticmethod
    def _schedule_seconds(schedule: str) -> int:
        match = _EVERY.fullmatch(schedule.strip())
        if not match:
            raise ServiceError("schedule must use @every <number>s|m|h|d")
        value = int(match.group(1))
        return value * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2)]

    @staticmethod
    def _conversation_dto(agent_id: str, item: Mapping[str, Any]) -> dict[str, Any]:
        return {"id": str(item.get("id") or item.get("session_id") or ""), "agent_id": agent_id, "title": str(item.get("title") or item.get("name") or "New conversation"), "preview": str(item.get("preview") or ""), "model": str(item.get("model") or ""), "messages": int(item.get("message_count") or item.get("messages") or 0), "tools": int(item.get("tools") or 0), "created_at": item.get("created_at"), "updated_at": item.get("updated_at")}

    @staticmethod
    def _agent_dto(item: Mapping[str, Any]) -> dict[str, Any]:
        metadata = dict(item.get("metadata") or {})
        config = dict(item.get("config") or {})
        name = str(item.get("name") or item.get("profile_name") or "")
        display_name = str(metadata.get("display_name") or metadata.get("title") or metadata.get("name") or name)
        return {"id": name, "name": name, "display_name": display_name, "title": display_name, "description": str(metadata.get("description") or ""), "status": str(metadata.get("status") or "active"), "config": {"provider": str(config.get("provider") or "nine-router"), "model": str(config.get("model") or "auto"), "reasoning_effort": str(config.get("effort") or "medium"), "approval_mode": str(config.get("approval_mode") or "on"), "skills_write_approval": bool(config.get("skills_write_approval", True)), "memory_write_approval": bool(config.get("memory_write_approval", True))}, "created_at": metadata.get("created_at"), "updated_at": metadata.get("updated_at"), "metadata": {**metadata, "profile_path": item.get("profile_path"), "workspace_path": item.get("workspace_path")}}


EXPECTED_ERRORS = (ServiceError, StoreError, AgentAPIError, ConfigAPIError, NineRouterAPIError)
