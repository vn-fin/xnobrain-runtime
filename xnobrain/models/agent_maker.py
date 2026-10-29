"""Inputs for Agent Maker tools; identity and work context come from the host."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .agent_blueprints import AgentBlueprintSpec


class MakerInspect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    blueprint_id: str | None = Field(default=None, pattern=r"^abp_[0-9a-f]{32}$")


class MakerPrepare(MakerInspect):
    intent: str = Field(min_length=1, max_length=20_000)
    blueprint: AgentBlueprintSpec
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    expected_revision: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Optional revision precondition when editing a blueprint. Omit to update "
            "the current stored revision without an inspection call."
        ),
    )

    @model_validator(mode="after")
    def revision_for_edit(self):
        if self.expected_revision is not None and not self.blueprint_id:
            raise ValueError("expected_revision requires blueprint_id")
        return self


class MakerBuild(BaseModel):
    model_config = ConfigDict(extra="forbid")
    blueprint_id: str = Field(pattern=r"^abp_[0-9a-f]{32}$")
    idempotency_key: str = Field(
        min_length=1, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$"
    )
    expected_revision: int | None = Field(default=None, ge=1)
    canonical_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    decision: Literal["build"] = "build"

    @model_validator(mode="before")
    @classmethod
    def discard_legacy_certification_options(cls, value):
        # In-flight callers may still use the previous tool schema. These options
        # are accepted for compatibility but never execute or gate a build.
        if isinstance(value, dict):
            value = dict(value)
            if value.get("decision") == "certify":
                value["decision"] = "build"
            for key in ("cases", "max_cost_usd", "timeout_seconds", "max_turns_per_case"):
                value.pop(key, None)
        return value
