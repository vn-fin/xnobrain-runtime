"""Isolated org execution snapshots; never part of personal team listings."""

from .base import RepositoryBase, StoreError
from .teams import TeamRepositoryMixin


class OrganizationTeamExecutionRepository(TeamRepositoryMixin, RepositoryBase):
    def __init__(self, repository, organization_id, *, namespace="organization-team-executions"):
        organization_id = self._id(organization_id, "organization id")
        root = repository.data_dir / self._id(namespace) / organization_id
        # Reuse the real workspace mount guard and lock. The execution directory
        # is a namespace within that mount, not a separately mounted workspace.
        self.storage_mount = repository.storage_mount
        self.data_dir = repository.data_dir
        self.profiles_root = repository.profiles_root
        self.root_profile = repository.root_profile
        self._lock = repository._lock
        self.teams_root = root / "teams"
        self.team_runs_root = root / "runs"
        for path in (root.parent, root, self.teams_root, self.team_runs_root):
            if path.is_symlink():
                raise StoreError("invalid organization execution directory")
            path.mkdir(parents=True, exist_ok=True, mode=0o750)
