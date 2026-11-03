"""Personal sidebar project contracts; workspace identity is never client input."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProjectRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0, strict=True)


class ProjectWrite(ProjectRevision):
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value):
            raise ValueError("project name must contain visible text")
        return value


class ProjectAssignment(ProjectRevision):
    project_id: str | None = Field(min_length=1, max_length=128)


class AgentProject(BaseModel):
    id: str
    name: str
    created_at: str
    updated_at: str


class AgentProjectsSnapshot(BaseModel):
    revision: int
    projects: list[AgentProject]
    memberships: dict[str, str]
