"""Usage analytics and budget contracts."""

from typing import Literal

from pydantic import BaseModel, Field


class AgentBudgetPatch(BaseModel):
    monthly_usd: float | None = Field(default=None, ge=0)
    daily_usd: float | None = Field(default=None, ge=0)
    warn_threshold_percent: int = Field(default=80, gt=0, le=100)
    cost_basis: Literal["estimated", "actual"] = "estimated"
    currency: str = Field(default="USD", min_length=1, max_length=8)
