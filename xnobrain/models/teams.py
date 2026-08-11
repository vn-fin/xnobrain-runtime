"""Team definition and team-run contracts."""

from typing import Literal

from pydantic import BaseModel, Field


class TeamWorkflowStep(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    task: str = Field(min_length=1, max_length=20_000)
    agent_id: str | None = None
    role: str | None = None
    needs: list[str] = Field(default_factory=list)
    allowed_tools: list[str] | None = None
    skills: list[str] | None = None


class TeamRun(BaseModel):
    task: str = Field(default="", max_length=20_000)
    workflow: list[TeamWorkflowStep] = Field(default_factory=list, max_length=64)
    synthesis: str | None = Field(default=None, max_length=20_000)


TeamRunStatus = Literal["pending", "running", "completed", "failed", "cancelled"]


class TeamRunStepRecord(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    agent_id: str
    role: str
    task: str = Field(default="", max_length=20_000)
    needs: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    status: TeamRunStatus = "pending"
    summary: str = ""
    summary_chars: int = 0
    error: str | None = None
    conversation_id: str | None = None
    started_at: str | None = None
    ended_at: str | None = None


class TeamRunRecord(BaseModel):
    id: str
    team_id: str
    status: TeamRunStatus = "pending"
    error: str | None = None
    mode: Literal["async", "sync"] = "async"
    task: str = ""
    synthesis_instruction: str = ""
    orchestrator_id: str = ""
    orchestrator_summary: str = ""
    created_at: str
    started_at: str | None = None
    ended_at: str | None = None
    updated_at: str
    revision: int = 0
    steps: list[TeamRunStepRecord] = Field(default_factory=list)


class TeamMember(BaseModel):
    agent_id: str
    role: str
    allowed_tools: list[str] = Field(default_factory=list)
    enabled: bool = True


class TeamCreate(BaseModel):
    name: str
    description: str | None = Field(default=None, max_length=2000)
    orchestrator_id: str
    coordinator_prompt: str | None = Field(default=None, max_length=20_000)
    coordinator_allowed_tools: list[str] | None = None
    coordinator_skills: list[str] | None = None
    synthesis_agent_id: str | None = None
    synthesis_allowed_tools: list[str] | None = None
    synthesis_skills: list[str] | None = None
    members: list[TeamMember] = Field(default_factory=list)
    workflow: list[TeamWorkflowStep] = Field(default_factory=list, max_length=64)
    shared_workspace: bool = False
    communication_level: Literal[0, 1, 2, 3] = 1
    synthesis_instruction: str | None = Field(default=None, max_length=20_000)
    max_parallel: int = Field(default=1, gt=0)
    max_depth: int = Field(default=1, gt=0)
    enabled: bool = True
