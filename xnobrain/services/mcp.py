"""MCP server configuration service."""

import copy
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml

from ..integrations import AgentAPIError, AgentManager
from ..repositories import FileRepository, StoreError
from ..repositories.custom_page_locks import lifecycle_gate
from .base import ServiceError


class MCPService:
    def __init__(
        self,
        repository: FileRepository,
        agents: AgentManager,
        profile_path: Callable[[str], Path],
        snapshot: Callable[[str, str, str, bytes], dict[str, Any]],
    ):
        self.repository = repository
        self.agents = agents
        self.profile_path = profile_path
        self.snapshot = snapshot

    def get(self, agent_id: str) -> dict[str, Any]:
        return self.agents.get_mcp(agent_id)

    def update(self, agent_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        try:
            with lifecycle_gate(self.repository.data_dir, agent_id):
                return self._update_under_lifecycle(agent_id, body)
        except StoreError as error:
            if error.code == "custom_page_jobs_active":
                raise ServiceError(
                    "Agent has an active run", status=409, code="agent_busy"
                ) from error
            raise

    def _update_under_lifecycle(self, agent_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        servers = body.get("servers", {})
        if not isinstance(servers, Mapping):
            raise ServiceError("servers must be an object")
        profile = self.profile_path(agent_id)
        config_path = profile / "config.yaml"
        merged_servers = copy.deepcopy(dict(servers))
        if config_path.is_symlink():
            raise ServiceError("Unsafe profile config", status=409, code="invalid_profile")
        if config_path.is_file():
            raw_config = config_path.read_bytes()
            try:
                current_config = yaml.safe_load(raw_config.decode("utf-8")) or {}
            except (UnicodeDecodeError, yaml.YAMLError) as exc:
                raise ServiceError(
                    "Profile config is invalid", code="invalid_profile_config"
                ) from exc
            if isinstance(current_config, dict):
                current_servers = current_config.get("mcp_servers") or {}
                if isinstance(current_servers, dict):
                    from ..integrations.extension_policy import load_state

                    profile_state = load_state(profile)
                    for record in profile_state.get("mcp", {}).values():
                        name = record.get("server_name")
                        if name in current_servers:
                            merged_servers[name] = copy.deepcopy(current_servers[name])
            self.snapshot(agent_id, "config", "config", raw_config)
        try:
            result = self.agents.update_mcp(agent_id, merged_servers)
        except AgentAPIError as exc:
            raise ServiceError(str(exc), status=exc.status, code=exc.code) from exc
        self.repository.atomic_json(
            profile / "mcp.json",
            {"servers": copy.deepcopy(merged_servers)},
        )
        return result
