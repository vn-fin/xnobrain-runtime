"""Compatibility facade composing the grouped Hermes runtime integrations."""

from __future__ import annotations

from .agent_profiles import AgentProfilesMixin
from .agent_skills import AgentSkillsMixin
from .agents import AgentOperationsMixin
from .conversation_goals import ConversationGoalsMixin
from .conversation_prompt import ConversationPromptMixin
from .conversation_runner import ConversationRunnerMixin
from .conversation_stream import ConversationStreamMixin
from .conversations import ConversationsMixin
from .default_skills import DefaultSkillsMixin
from .hermes_commands import HermesCommandsMixin
from .hermes_support import *  # noqa: F401,F403
from .hermes_support import (
    DEFAULT_PROFILES_ROOT,
    DEFAULT_ROOT_PROFILE,
    AgentAPIError,
    Any,
    LLMRouterClient,
    Path,
    asyncio,  # noqa: F401 - retained for existing runtime monkeypatches
    os,
    threading,
)
from .mcp import MCPIntegrationMixin
from .workspaces import WorkspacesMixin


class AgentManager(
    AgentOperationsMixin,
    MCPIntegrationMixin,
    AgentSkillsMixin,
    DefaultSkillsMixin,
    ConversationRunnerMixin,
    ConversationGoalsMixin,
    ConversationPromptMixin,
    ConversationStreamMixin,
    ConversationsMixin,
    WorkspacesMixin,
    AgentProfilesMixin,
    HermesCommandsMixin,
):
    def __init__(
        self,
        *,
        root_profile: str | Path | None = None,
        profiles_root: str | Path | None = None,
        legacy_agents_root: str | Path | None = None,
        profile_template: str | Path | None = None,
    ):
        self.root_profile = Path(
            root_profile or os.environ.get("HERMES_ROOT_PROFILE") or DEFAULT_ROOT_PROFILE
        )
        self.profiles_root = Path(
            profiles_root
            or os.environ.get("HERMES_PROFILES_ROOT")
            or self.root_profile / "profiles"
            or DEFAULT_PROFILES_ROOT
        )
        self.legacy_agents_root = Path(
            legacy_agents_root
            or os.environ.get("HERMES_LEGACY_AGENTS_ROOT")
            or os.environ.get("HERMES_AGENTS_ROOT")
            or self.root_profile.parent / "legacy-agents"
        )
        # The embedded streaming runner uses this process environment directly,
        # while one-shot commands inherit it in _command_env().
        self._ensure_router_api_key()
        configured_template = (
            profile_template
            or os.environ.get("XNOBRAIN_PROFILE_TEMPLATE")
            or self.root_profile / "profile-template"
        )
        configured_template = Path(configured_template)
        bundled_template = Path(__file__).resolve().parents[2] / "runtime" / "profile-templates"
        self.profile_template = (
            configured_template if configured_template.is_dir() else bundled_template
        )
        self.llm_router = LLMRouterClient()
        self._active_runs: dict[str, dict[str, Any]] = {}
        self._active_agent_counts: dict[str, int] = {}
        self._stopped_runs: set[str] = set()
        self._registry_lock = threading.RLock()
        self._conversation_lock = threading.RLock()
        self._compacting_sessions: set[str] = set()
        self._skill_sync_lock = threading.RLock()
        self.sync_profiles_registry()
        self._apply_default_skills_policy(self.root_profile)
        if self.profiles_root.is_dir():
            for profile_dir in self.profiles_root.iterdir():
                if profile_dir.is_dir():
                    self._apply_default_skills_policy(profile_dir)


__all__ = ["AgentAPIError", "AgentManager"]
