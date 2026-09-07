"""Filesystem repository facade composed from service-group repositories."""

from .agent_blueprints import AgentBlueprintRepositoryMixin
from .base import TEAM_RUN_RETENTION, RepositoryBase, StoreError
from .conversation_runs import ConversationRunRepositoryMixin
from .cron import CronRepositoryMixin
from .notifications import NotificationRepositoryMixin
from .profiles import ProfileRepositoryMixin
from .teams import TeamRepositoryMixin


class FileRepository(
    AgentBlueprintRepositoryMixin,
    ProfileRepositoryMixin,
    ConversationRunRepositoryMixin,
    CronRepositoryMixin,
    TeamRepositoryMixin,
    NotificationRepositoryMixin,
    RepositoryBase,
):
    """Compatibility facade exposing all atomic filesystem repository groups."""


__all__ = ["FileRepository", "StoreError", "TEAM_RUN_RETENTION"]
