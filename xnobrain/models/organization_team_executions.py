"""Private Control-to-Runtime execution request."""

from pydantic import BaseModel, Field

from .teams import TeamCreate, TeamRun


class OrganizationTeamExecutionStart(BaseModel):
    team: TeamCreate
    input: TeamRun
    capability: str = Field(min_length=64, max_length=64)
    coordinator_tokens: dict[str, str] = Field(default_factory=dict, max_length=2, repr=False)
