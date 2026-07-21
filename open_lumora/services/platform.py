"""Platform service layer for agents, Hermes profiles, cron, MCP, teams, and snapshots."""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import time
from typing import Any, Mapping
import uuid

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
    ):
        self.repository = repository
        self.agents = agents
        self.config = config
        self.router = router
        self.portability = PortabilityService(repository)
        self._oauth_attempts: dict[str, dict[str, str]] = {}
        self._running_crons: set[str] = set()

    def list_profiles(self) -> list[dict[str, Any]]:
        """Use Hermes' native profile inventory, including the default profile."""
        from hermes_cli.profiles import list_profiles
        result = []
        for item in list_profiles():
            updated_at = item.path.stat().st_mtime if item.path.exists() else None
            result.append({
                "name": item.name, "path": str(item.path), "is_default": item.is_default,
                "gateway_running": item.gateway_running, "model": item.model,
                "provider": item.provider, "skill_count": item.skill_count,
                "description": item.description, "updated_at": updated_at,
            })
        return result

    def list_agents(self) -> list[dict[str, Any]]:
        return [self._agent_dto(item) for item in self.agents.list_agents()["agents"]]

    def create_agent(self, body: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(body)
        if "title" not in payload and "name" in payload:
            payload["title"] = payload["name"]
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
        for key in ("title", "description"):
            if key in body:
                current[key] = str(body[key] or "").strip()
        current["updated_at"] = time.time()
        self.repository.atomic_json(metadata_path, current)
        return self.get_agent(agent_id)

    def delete_agent(self, agent_id: str) -> dict[str, Any]:
        target = self.repository.soft_delete_profile(agent_id)
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
        translated.pop("skills_write_approval", None)
        translated.pop("memory_write_approval", None)
        return self.agents.update_config(agent_id, translated)["config"]

    def list_skills(self, agent_id: str) -> list[dict[str, Any]]:
        return self.agents.list_skills(agent_id)["skills"]

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

    def resolve_approval(self, run_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        return self.agents.resolve_approval(run_id, body)

    def export_bundle(self, body: Mapping[str, Any]) -> tuple[bytes, str]:
        return self.portability.export(body)

    def inspect_bundle(self, payload: bytes) -> dict[str, Any]:
        return self.portability.inspect(payload)

    def dry_run_bundle(self, payload: bytes) -> dict[str, Any]:
        return self.portability.dry_run(payload)

    def apply_bundle(self, payload: bytes) -> dict[str, Any]:
        return self.portability.apply(payload)

    def list_crons(self) -> list[dict[str, Any]]:
        return self.repository.list_crons()

    def create_cron(self, body: Mapping[str, Any]) -> dict[str, Any]:
        agent_id = str(body.get("agent_id") or "").strip()
        self.agents.describe_agent(agent_id, include_memory=False)
        prompt = str(body.get("prompt") or "").strip()
        if not prompt:
            raise ServiceError("prompt is required")
        interval = int(body.get("interval_minutes") or 0)
        schedule = str(body.get("schedule") or (f"@every {interval}m" if interval > 0 else "")).strip()
        seconds = self._schedule_seconds(schedule)
        now = utc_now()
        job = {
            "id": uuid.uuid4().hex, "agent_id": agent_id,
            "name": str(body.get("name") or "Scheduled task").strip(),
            "schedule": schedule, "timezone": str(body.get("timezone") or "Etc/UTC"),
            "mode": str(body.get("mode") or "local"),
            "misfire_policy": str(body.get("misfire_policy") or "notify"),
            "replay_limit": int(body.get("replay_limit") or 0),
            "prompt": prompt, "enabled": True,
            "next_run_at": iso(datetime.fromtimestamp(now.timestamp() + seconds, timezone.utc)),
            "last_evaluated_at": iso(now), "created_at": iso(now), "updated_at": iso(now),
        }
        if job["mode"] not in {"local", "managed"}:
            raise ServiceError("mode must be local or managed")
        return self.repository.put_cron(job)

    def set_cron_enabled(self, cron_id: str, enabled: bool) -> dict[str, Any]:
        job = self._cron(cron_id)
        job["enabled"] = enabled
        job["updated_at"] = iso()
        return self.repository.put_cron(job)

    def delete_cron(self, cron_id: str) -> dict[str, Any]:
        if not self.repository.delete_cron(cron_id):
            raise ServiceError("cron not found", status=404, code="not_found")
        return {"deleted": True}

    async def run_cron(self, cron_id: str) -> dict[str, Any]:
        if cron_id in self._running_crons:
            raise ServiceError("cron job is already running", status=409, code="cron_running")
        self._running_crons.add(cron_id)
        try:
            return await self._run_cron(cron_id)
        finally:
            self._running_crons.discard(cron_id)

    async def _run_cron(self, cron_id: str) -> dict[str, Any]:
        job = self._cron(cron_id)
        if not job.get("enabled", True):
            raise ServiceError("cron job is paused", status=409, code="cron_paused")
        if job.get("mode", "local") != "local":
            raise ServiceError("managed cron requires an Enterprise command", status=403, code="managed_cron")
        result = await self.agents.chat(job["agent_id"], {"message": job["prompt"]})
        now = utc_now()
        job["last_run_at"] = iso(now)
        job["last_evaluated_at"] = iso(now)
        job["next_run_at"] = iso(datetime.fromtimestamp(now.timestamp() + self._schedule_seconds(job["schedule"]), timezone.utc))
        job["updated_at"] = iso(now)
        self.repository.put_cron(job)
        return {"job": job, "run": result}

    async def scheduler_loop(self, interval_seconds: int = 30) -> None:
        """Run due profile-local jobs without a database or second service."""
        logger = logging.getLogger("open_lumora.cron")
        logger.info("Open Lumora profile cron scheduler started")
        while True:
            try:
                await self._tick_crons()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("Open Lumora cron tick failed: %s", type(error).__name__)
            await asyncio.sleep(max(1, interval_seconds))

    async def _tick_crons(self) -> None:
        now = utc_now()
        for job in self.repository.list_crons():
            cron_id = str(job.get("id") or "")
            if not cron_id or cron_id in self._running_crons:
                continue
            if not job.get("enabled", True) or job.get("mode", "local") != "local":
                continue
            next_run = self._parse_time(job.get("next_run_at"))
            if next_run is None or next_run > now:
                continue
            # Advance the durable cursor before execution. A process crash may
            # delay one run, but cannot duplicate an already-claimed due job.
            job["last_evaluated_at"] = iso(now)
            job["next_run_at"] = iso(
                datetime.fromtimestamp(
                    now.timestamp() + self._schedule_seconds(str(job.get("schedule") or "")),
                    timezone.utc,
                )
            )
            job["updated_at"] = iso(now)
            self.repository.put_cron(job)
            await self._run_scheduled_cron(cron_id)

    async def _run_scheduled_cron(self, cron_id: str) -> None:
        try:
            await self.run_cron(cron_id)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.repository.put_notification({
                "id": uuid.uuid4().hex,
                "kind": "cron_failed",
                "cron_id": cron_id,
                "error_code": str(getattr(error, "code", "cron_failed")),
                "created_at": iso(),
                "resolved": False,
            })

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
        return {"deleted": True}

    async def run_team(self, team_id: str, task: str) -> dict[str, Any]:
        team = self.get_team(team_id)
        if not team.get("enabled", True):
            raise ServiceError("team is disabled", status=409, code="team_disabled")
        started = iso()
        members = [item for item in team["members"] if item.get("enabled", True)]
        semaphore = asyncio.Semaphore(max(1, int(team.get("max_parallel") or 1)))

        async def run_member(member: Mapping[str, Any]) -> dict[str, Any]:
            async with semaphore:
                tools = [tool for tool in member.get("allowed_tools", []) if tool in SAFE_TOOLSETS]
                prompt = f"Role: {member['role']}\nDo not ask for clarification or write memory. Return only a final summary.\n\nTask: {task}"
                result = await self.agents.chat(member["agent_id"], {"message": prompt, "toolsets": tools})
                return {"agent_id": member["agent_id"], "role": member["role"], "summary": result.get("response", "")}

        results = await asyncio.gather(*(run_member(item) for item in members))
        synthesis = "Synthesize these worker summaries into one final answer.\n\n" + "\n\n".join(f"{item['role']}: {item['summary']}" for item in results)
        final = await self.agents.chat(team["orchestrator_id"], {"message": synthesis, "toolsets": ["todo"]})
        return {"team_id": team_id, "member_results": results, "orchestrator_summary": final.get("response", ""), "started_at": started, "completed_at": iso()}

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
            connection = next((item for item in connections if item.get("provider") == provider and item.get("active", True)), None)
            result.append({
                "id": provider, "display_name": provider.title(), "provider_type": provider,
                "description": "Credentials are managed by the local 9router runtime.",
                "connection_mode": "api-key" if provider in API_KEY_PROVIDERS else "cli",
                "connected": connection is not None,
                "status": "connected" if connection else "disconnected",
                "last_test_status": (connection or {}).get("test_status", "unknown"),
                "default_model": (connection or {}).get("default_model", ""),
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
        connections = (await self.router.list_connections())["connections"]
        current = next((item for item in connections if item.get("provider") == provider), None)
        if not current:
            return {"provider_id": provider, "healthy": False, "status": "not_connected", "message": "Provider is not connected"}
        result = await self.router.test_connection(current["id"])
        return {"provider_id": provider, "healthy": bool(result.get("valid")), "status": "healthy" if result.get("valid") else "unhealthy", "message": result.get("error") or ""}

    @staticmethod
    def _provider(provider: str) -> str:
        if provider not in SUPPORTED_PROVIDERS:
            raise ServiceError("provider not found", status=404, code="not_found")
        return provider

    @staticmethod
    def _api_key_info(provider: str) -> dict[str, Any]:
        return {"provider_id": provider, "provider_type": provider, "connection_mode": "api-key", "required_client_action": "submit_text", "instructions": "Enter the provider API key. It is stored by 9router, not Open Lumora.", "text_label": "API key", "status": "waiting_for_user"}

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

    def _cron(self, cron_id: str) -> dict[str, Any]:
        item = next((item for item in self.repository.list_crons() if item.get("id") == cron_id), None)
        if item is None:
            raise ServiceError("cron not found", status=404, code="not_found")
        return item

    @staticmethod
    def _schedule_seconds(schedule: str) -> int:
        match = _EVERY.fullmatch(schedule.strip())
        if not match:
            raise ServiceError("schedule must use @every <number>s|m|h|d")
        value = int(match.group(1))
        return value * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2)]

    @staticmethod
    def _parse_time(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _conversation_dto(agent_id: str, item: Mapping[str, Any]) -> dict[str, Any]:
        return {"id": str(item.get("id") or item.get("session_id") or ""), "agent_id": agent_id, "title": str(item.get("title") or item.get("name") or "New conversation"), "preview": str(item.get("preview") or ""), "model": str(item.get("model") or ""), "messages": int(item.get("message_count") or item.get("messages") or 0), "tools": int(item.get("tools") or 0), "created_at": item.get("created_at"), "updated_at": item.get("updated_at")}

    @staticmethod
    def _agent_dto(item: Mapping[str, Any]) -> dict[str, Any]:
        metadata = dict(item.get("metadata") or {})
        config = dict(item.get("config") or {})
        name = str(item.get("name") or item.get("profile_name") or "")
        return {"id": name, "name": name, "title": str(metadata.get("title") or metadata.get("name") or name), "description": str(metadata.get("description") or ""), "status": str(metadata.get("status") or "active"), "config": {"provider": str(config.get("provider") or "nine-router"), "model": str(config.get("model") or "auto"), "reasoning_effort": str(config.get("effort") or "medium"), "approval_mode": str(config.get("approval_mode") or "on")}, "created_at": metadata.get("created_at"), "updated_at": metadata.get("updated_at"), "metadata": {**metadata, "profile_path": item.get("profile_path"), "workspace_path": item.get("workspace_path")}}


EXPECTED_ERRORS = (ServiceError, StoreError, AgentAPIError, ConfigAPIError, NineRouterAPIError)
