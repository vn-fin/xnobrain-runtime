"""Inputs for Agent Maker tools; identity and work context come from the host."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .agent_blueprints import AgentBlueprintCertificationCreate, AgentBlueprintSpec


class MakerInspect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    blueprint_id: str | None = Field(default=None, pattern=r"^abp_[0-9a-f]{32}$")


class MakerPrepare(MakerInspect):
    intent: str = Field(min_length=1, max_length=20_000)
    blueprint: AgentBlueprintSpec
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    expected_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def revision_for_edit(self):
        if bool(self.blueprint_id) != (self.expected_revision is not None):
            raise ValueError("editing requires both blueprint_id and expected_revision")
        return self


class MakerBuild(AgentBlueprintCertificationCreate):
    blueprint_id: str = Field(pattern=r"^abp_[0-9a-f]{32}$")
    decision: Literal["certify"] = "certify"
    max_cost_usd: float = Field(default=0.1, gt=0, le=1)
