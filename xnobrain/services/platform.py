"""Composition root for the XNObrain backend service groups."""

from __future__ import annotations

from typing import Any

import yaml

from ..defaults import (
    BIG_BROTHER_MODEL_DEFAULT_MARKER,
    DEFAULT_PROFILE_MODEL,
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
from .agents import AgentsServiceMixin
from .analytics import AnalyticsService
from .automation import AutomationServiceMixin
from .base import ServiceError
from .blends import BlendService
from .conversations import ConversationsServiceMixin
from .cron import CronService, CronServiceError
from .helpers import MemoryCache
from .kanban import KanbanService
from .mcp import MCPService
from .portability import PortabilityService, PortabilityServiceMixin
from .providers import ProvidersServiceMixin
from .sandboxes import SandboxesServiceMixin
from .team_runs import TeamRunService
from .teams import TeamsServiceMixin
from .workspace_preview import WorkspacePreviewError, WorkspacePreviewService
from .workspace_upload import WorkspaceUploadError, WorkspaceUploadService
from .workspaces import WorkspacesServiceMixin
from .errors import EXPECTED_ERRORS


class PlatformService(
    AgentsServiceMixin,
    AutomationServiceMixin,
    ConversationsServiceMixin,
    PortabilityServiceMixin,
    ProvidersServiceMixin,
    SandboxesServiceMixin,
    TeamsServiceMixin,
    WorkspacesServiceMixin,
):
    """Compose feature services behind the stable application service facade."""

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
        self.cron.kanban = self.kanban
        from .analytics import AnalyticsService
        self.analytics = AnalyticsService(agents, router, repository)
        from .blends import BlendService
        self.blends = BlendService(router)
        from .team_runs import TeamRunService
        self.team_runs = TeamRunService(repository, agents, self)
        self._oauth_attempts: dict[str, dict[str, str]] = {}
        self._cache = MemoryCache()
        self.config.ensure_write_approval_defaults()
        self._ensure_existing_write_approval_defaults()
        self.mcp = MCPService(
            repository,
            agents,
            self._agent_profile_path,
            self._snapshot_agent,
        )

    def _ensure_existing_write_approval_defaults(self) -> None:
        """Backfill automatic execution defaults without replacing user choices."""
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
            approvals = config.get("approvals")
            if not isinstance(approvals, dict):
                approvals = {}
                config["approvals"] = approvals
                changed = True
            if "mode" not in approvals:
                approvals["mode"] = "off"
                changed = True
            for subsystem in ("skills", "memory"):
                section = config.get(subsystem)
                if not isinstance(section, dict):
                    section = {}
                    config[subsystem] = section
                    changed = True
                if "write_approval" not in section:
                    section["write_approval"] = False
                    changed = True
            if changed:
                self.repository.snapshot(profile.name, "config", "config", path.read_bytes())
                self.repository.atomic_yaml(path, config)

