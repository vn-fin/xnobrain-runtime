"""Usage analytics and budget contracts."""

from typing import Literal

from pydantic import BaseModel, Field


class AgentBudgetPatch(BaseModel):
    weekly_usd: float | None = Field(default=None, ge=1)
    cost_basis: Literal["estimated", "actual"] = "estimated"
    currency: Literal["USD"] = "USD"
