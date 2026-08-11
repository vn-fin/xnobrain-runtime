"""Filesystem repository facade composed from service-group repositories."""

from .base import RepositoryBase, StoreError, TEAM_RUN_RETENTION
from .cron import CronRepositoryMixin
from .notifications import NotificationRepositoryMixin
from .profiles import ProfileRepositoryMixin
from .teams import TeamRepositoryMixin


class FileRepository(
    ProfileRepositoryMixin,
    CronRepositoryMixin,
    TeamRepositoryMixin,
    NotificationRepositoryMixin,
    RepositoryBase,
):
    """Compatibility facade exposing all atomic filesystem repository groups."""


__all__ = ["FileRepository", "StoreError", "TEAM_RUN_RETENTION"]
