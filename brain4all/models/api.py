"""Pydantic contracts for Brain4All's public management APIs."""

from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class APIEnvelope(BaseModel):
    success: bool = True
    data: Any = None
    message: str = "ok"
    status_code: int = 200


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
    reasoning_effort: str | None = None
    approval_mode: str | None = None
    skills_write_approval: bool | None = None
    memory_write_approval: bool | None = None
    system_prompt: str | None = None


class CronCreate(BaseModel):
    agent_id: str
    name: str
    prompt: str
    interval_minutes: int | None = Field(default=None, gt=0)
    schedule: str | None = None
    timezone: str = "Etc/UTC"
    mode: Literal["local"] = "local"


class KanbanBoardCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    color: str | None = Field(default=None, max_length=32)


class KanbanTaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=50_000)
    status: Literal["backlog", "todo"] = "todo"
    priority: Literal["high", "medium", "low"] = "medium"
    assignee: str | None = Field(default=None, max_length=128)
    parents: list[str] = Field(default_factory=list, max_length=100)
    workspace_kind: Literal["scratch", "dir", "worktree"] = "scratch"
    workspace_path: str | None = Field(default=None, max_length=2000)
    skills: list[str] | None = Field(default=None, max_length=64)
    model_override: str | None = Field(default=None, max_length=256)
    provider_override: str | None = Field(default=None, max_length=128)
    goal_mode: bool = False
    idempotency_key: str | None = Field(default=None, max_length=512)


class KanbanTaskPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=50_000)
    priority: Literal["high", "medium", "low"] | None = None
    tags: list[str] | None = Field(default=None, max_length=32)
    model_override: str | None = Field(default=None, max_length=256)
    provider_override: str | None = Field(default=None, max_length=128)


class KanbanMove(BaseModel):
    status: Literal["backlog", "todo", "in_progress", "review", "done"]
    revision: str | None = Field(default=None, max_length=128)
    reason: str | None = Field(default=None, max_length=4000)


class KanbanAssign(BaseModel):
    assignee: str | None = Field(default=None, max_length=128)
    reclaim_first: bool = False
    reason: str | None = Field(default=None, max_length=4000)


class KanbanComment(BaseModel):
    body: str = Field(min_length=1, max_length=20_000)
    author: str = Field(default="user", min_length=1, max_length=128)


class KanbanLink(BaseModel):
    parent_id: str = Field(min_length=1, max_length=128)
    child_id: str = Field(min_length=1, max_length=128)


class SkillInstall(BaseModel):
    model_config = ConfigDict(extra="allow")
    skill_id: str | None = None
    name: str | None = None
    content: str | None = None
    source: str | None = Field(default=None, max_length=512)


class EnabledPatch(BaseModel):
    enabled: bool


class MemoryPatch(BaseModel):
    memory: str


class WorkspacePath(BaseModel):
    path: str


class WorkspaceWrite(WorkspacePath):
    content: str | None = None
    content_base64: str | None = None


class WorkspaceCreate(WorkspaceWrite):
    type: Literal["file", "directory"] = "file"


class ConversationCreate(BaseModel):
    title: str = Field(default="New conversation", max_length=200)


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ChatRequest(BaseModel):
    input: str = Field(min_length=1)
    model: str | None = None
    skills: list[str] | None = None
    toolsets: list[str] | None = None
    timeout_seconds: int | None = Field(default=None, gt=0)


class RunApproval(BaseModel):
    choice: Literal["once", "session", "always", "deny"]
    resolve_all: bool = False
    subsystem: Literal["skills", "memory"] | None = None


class TeamWorkflowStep(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    task: str = Field(min_length=1, max_length=20_000)
    agent_id: str | None = None
    role: str | None = None
    needs: list[str] = Field(default_factory=list)
    allowed_tools: list[str] | None = None


class TeamRun(BaseModel):
    task: str = Field(default="", max_length=20_000)
    workflow: list[TeamWorkflowStep] = Field(default_factory=list, max_length=64)
    synthesis: str | None = Field(default=None, max_length=20_000)


class ProviderCredential(BaseModel):
    model_config = ConfigDict(extra="allow")
    text: str | None = None
    response_text: str | None = None
    token: str | None = None
    api_key: str | None = None
    default_model: str | None = None


class BundleExport(BaseModel):
    agent_ids: list[str] = Field(min_length=1)
    include_conversations: bool = False


class BundleUploadStart(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0)
    sha256: str | None = Field(default=None, min_length=64, max_length=64)


class BundleUploadComplete(BaseModel):
    sha256: str | None = Field(default=None, min_length=64, max_length=64)


class BundleUploadApply(BaseModel):
    environment: dict[str, str] = Field(default_factory=dict)


class MCPConfig(BaseModel):
    servers: dict[str, dict[str, Any]] = Field(default_factory=dict)


class TeamMember(BaseModel):
    agent_id: str
    role: str
    allowed_tools: list[str] = Field(default_factory=lambda: ["web"])
    enabled: bool = True


class TeamCreate(BaseModel):
    name: str
    orchestrator_id: str
    members: list[TeamMember] = Field(default_factory=list)
    max_parallel: int = Field(default=1, gt=0)
    max_depth: int = Field(default=1, gt=0)
    enabled: bool = True


class GenericObject(BaseModel):
    """Document an intentionally extensible Hermes-native payload."""

    model_config = ConfigDict(extra="allow")
