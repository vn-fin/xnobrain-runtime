"""Conversation, chat run, and approval contracts."""

from typing import Literal

from pydantic import BaseModel, Field


class ConversationCreate(BaseModel):
    title: str = Field(default="New Session", max_length=200)


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ConversationCompact(BaseModel):
    focus: str | None = Field(default=None, max_length=500)


class ChatRequest(BaseModel):
    input: str = Field(min_length=1)
    model: str | None = None
    skills: list[str] | None = None
    toolsets: list[str] | None = None
    timeout_seconds: int | None = Field(default=None, gt=0)
    run_mode: Literal["auto", "interactive", "background"] = "auto"
    feature: Literal["todo", "delegate", "goal", "learn"] | None = None


class GoalContractInput(BaseModel):
    outcome: str = Field(default="", max_length=2_000)
    verification: str = Field(default="", max_length=2_000)
    constraints: str = Field(default="", max_length=2_000)
    boundaries: str = Field(default="", max_length=2_000)
    stop_when: str = Field(default="", max_length=2_000)


class GoalCreate(BaseModel):
    objective: str = Field(min_length=1, max_length=10_000)
    max_turns: int = Field(default=20, ge=1, le=100)
    contract: GoalContractInput = Field(default_factory=GoalContractInput)


class GoalUpdate(GoalCreate):
    pass


class SubgoalCreate(BaseModel):
    text: str = Field(min_length=1, max_length=2_000)


class RunApproval(BaseModel):
    choice: Literal["once", "session", "always", "deny"]
    resolve_all: bool = False
    subsystem: Literal["skills", "memory"] | None = None
