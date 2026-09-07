"""Usage analytics and budget contracts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentBudgetPatch(BaseModel):
    weekly_usd: float | None = Field(default=None, ge=1)
    cost_basis: Literal["estimated", "actual"] = "estimated"
    currency: Literal["USD"] = "USD"


class SkillUsageItem(BaseModel):
    """One skill revision's measured or historical inferred usage."""

    model_config = ConfigDict(extra="forbid")
    skill_id: str
    skill_digest: str | None = None
    requested_count: int | None = Field(default=None, ge=0)
    loaded_count: int = Field(ge=0)
    reference_reads: int | None = Field(default=None, ge=0)
    distinct_runs: int = Field(ge=0)
    last_used_at: str | float | None = None
    tool_invocations: int | None = Field(default=None, ge=0)
    tool_completed: int | None = Field(default=None, ge=0)
    errors: int | None = Field(default=None, ge=0)
    attribution: Literal["observed", "multiple"]


class SkillUsageCoverage(BaseModel):
    """Evidence provenance and instrumentation coverage for a usage page."""

    model_config = ConfigDict(extra="forbid")
    source: Literal["xnobrain_skill_lifecycle_events", "hermes_tool_calls"]
    attribution: Literal["explicit_lifecycle", "observed_load_only"]
    from_: float | None = Field(default=None, alias="from")
    to: float | None = None
    instrumented: bool
    instrumentation_version: str | None = None
    event_count: int | None = Field(default=None, ge=0)
    unattributed_tool_invocations: int | None = Field(default=None, ge=0)
    message: str


class SkillUsageResponse(BaseModel):
    """Typed Runtime v1 selected-agent skill usage response data."""

    model_config = ConfigDict(extra="forbid")
    agent_id: str
    work_context_id: str
    items: list[SkillUsageItem]
    coverage: SkillUsageCoverage
    next_cursor: str | None = None
    limit: int = Field(default=100, ge=1, le=100)
