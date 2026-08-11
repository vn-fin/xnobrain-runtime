"""Kanban board, task, scheduling, and transition contracts."""

from typing import Literal

from pydantic import BaseModel, Field


class KanbanBoardCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    color: str | None = Field(default=None, max_length=32)


class KanbanTaskSchedule(BaseModel):
    recurrence: Literal["once", "interval"] = "once"
    scheduled_at: str = Field(min_length=1, max_length=64)
    timezone: str = Field(default="Etc/UTC", min_length=1, max_length=64)
    interval_minutes: int | None = Field(default=None, ge=1, le=525_600)


class KanbanTaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(min_length=1, max_length=50_000)
    status: Literal["backlog", "todo", "scheduled"] = "todo"
    priority: Literal["high", "medium", "low"] = "medium"
    assignee: str | None = Field(default=None, max_length=128)
    team_id: str | None = Field(default=None, max_length=128)
    parents: list[str] = Field(default_factory=list, max_length=100)
    workspace_kind: Literal["scratch", "dir", "worktree"] | None = None
    workspace_path: str | None = Field(default=None, max_length=2000)
    skills: list[str] | None = None
    model_override: str | None = Field(default=None, max_length=256)
    provider_override: str | None = Field(default=None, max_length=128)
    goal_mode: bool = False
    idempotency_key: str | None = Field(default=None, max_length=512)
    schedule: KanbanTaskSchedule | None = None


class KanbanTaskPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, min_length=1, max_length=50_000)
    priority: Literal["high", "medium", "low"] | None = None
    skills: list[str] | None = None
    tags: list[str] | None = Field(default=None, max_length=32)
    model_override: str | None = Field(default=None, max_length=256)
    provider_override: str | None = Field(default=None, max_length=128)
    schedule: KanbanTaskSchedule | None = None


class KanbanScheduleAction(BaseModel):
    action: Literal["pause", "resume", "run_now"]


class KanbanMove(BaseModel):
    status: Literal["backlog", "todo", "running", "done", "archived"]
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
