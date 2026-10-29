"""Signed personal execution input, populated only by Control."""

from pydantic import Field

from .organization_team_executions import OrganizationTeamExecutionStart


class PersonalDelegatedExecutionStart(OrganizationTeamExecutionStart):
    source_revision: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    source_kind: str = Field(pattern=r"^(agent_team|task)$")
