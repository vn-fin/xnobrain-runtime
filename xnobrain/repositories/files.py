"""Filesystem repository facade composed from service-group repositories."""

from .agent_blueprints import AgentBlueprintRepositoryMixin
from .base import TEAM_RUN_RETENTION, RepositoryBase, StoreError
from .conversation_runs import ConversationRunRepositoryMixin
from .conversations import ConversationRepositoryMixin
from .cron import CronRepositoryMixin
from .notifications import NotificationRepositoryMixin
from .profiles import ProfileRepositoryMixin
from .skill_usage import SkillUsageRepositoryMixin
from .teams import TeamRepositoryMixin


class FileRepository(
    AgentBlueprintRepositoryMixin,
    ProfileRepositoryMixin,
    ConversationRepositoryMixin,
    ConversationRunRepositoryMixin,
    CronRepositoryMixin,
    TeamRepositoryMixin,
    NotificationRepositoryMixin,
    SkillUsageRepositoryMixin,
    RepositoryBase,
):
    """Compatibility facade exposing all atomic filesystem repository groups."""


__all__ = ["FileRepository", "StoreError", "TEAM_RUN_RETENTION"]
