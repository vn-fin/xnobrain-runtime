"""MCP server configuration service."""

import copy
from pathlib import Path
from typing import Any, Callable, Mapping

from ..integrations import AgentAPIError, AgentManager
from ..repositories import FileRepository
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
        servers = body.get("servers", {})
        if not isinstance(servers, Mapping):
            raise ServiceError("servers must be an object")
        profile = self.profile_path(agent_id)
        config_path = profile / "config.yaml"
        if config_path.is_file():
            self.snapshot(agent_id, "config", "config", config_path.read_bytes())
        try:
            result = self.agents.update_mcp(agent_id, servers)
        except AgentAPIError as exc:
            raise ServiceError(str(exc), status=exc.status, code=exc.code) from exc
        self.repository.atomic_json(
            profile / "mcp.json",
            {"servers": copy.deepcopy(dict(servers))},
        )
        return result
