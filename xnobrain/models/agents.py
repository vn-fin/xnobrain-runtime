"""Agent, profile, skill, memory, and configuration contracts."""

from pydantic import BaseModel, ConfigDict, Field


class AgentCreate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)


class AgentMetadataPatch(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=4000)


class ConfigPatch(BaseModel):
    model_config = ConfigDict(extra="allow")
    provider: str | None = None
    model: str | None = None
    assignment_id: str | None = Field(
        default=None, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$"
    )
    reasoning_effort: str | None = None
    approval_mode: str | None = None
    skills_write_approval: bool | None = None
    memory_write_approval: bool | None = None
    checkpoints_enabled: bool | None = None
    goal_max_turns: int | None = Field(default=None, ge=10, le=30)
    system_prompt: str | None = None


class SkillInstall(BaseModel):
    model_config = ConfigDict(extra="allow")
    skill_id: str | None = None
    name: str | None = None
    content: str | None = None
    source: str | None = Field(default=None, max_length=512)


class SkillSyncRequest(BaseModel):
    agent_ids: list[str] = Field(min_length=1, max_length=100)
    expected_source_revision: str | None = Field(default=None, max_length=128)


class EnabledPatch(BaseModel):
    enabled: bool


class MemoryPatch(BaseModel):
    memory: str
