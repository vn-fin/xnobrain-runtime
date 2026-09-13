"""Typed contracts for bounded skill optimization and rollback."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SHA256 = r"^sha256:[0-9a-f]{64}$"
_SAFE_ID = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$"


class SkillOptimizationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skill_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    expected_baseline_digest: str | None = Field(default=None, pattern=_SHA256)
    candidate_content: str = Field(min_length=1, max_length=200_000)
    enable: bool = False


class SkillOptimizationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_context_id: str = Field(default="personal", pattern=_SAFE_ID)
    range_from: float
    range_to: float
    session_ids: list[str] = Field(default_factory=list, max_length=20)
    patches: list[SkillOptimizationPatch] = Field(min_length=1, max_length=10)
    max_example_bytes: int = Field(default=262_144, ge=0, le=1_048_576)
    max_evaluation_cost_usd: float = Field(default=0, ge=0, le=100)

    @field_validator("session_ids")
    @classmethod
    def unique_sessions(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)) or any(
            not value or len(value) > 256 for value in values
        ):
            raise ValueError("session_ids must be unique bounded references")
        return values

    @field_validator("patches")
    @classmethod
    def unique_skills(
        cls,
        values: list[SkillOptimizationPatch],
    ) -> list[SkillOptimizationPatch]:
        identifiers = [item.skill_id for item in values]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("patches must target unique skills")
        return values

    @model_validator(mode="after")
    def valid_range(self):
        if not self.range_from < self.range_to:
            raise ValueError("range_from must be before range_to")
        if self.range_to - self.range_from > 366 * 86400:
            raise ValueError("optimization range exceeds 366 days")
        return self


class SkillEvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(pattern=_SAFE_ID)
    prompt: str = Field(min_length=1, max_length=4_000)
    expected_trigger: bool
    partition: Literal["development", "held_out"]


class SkillOptimizationEvaluationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    candidate_digest: str = Field(pattern=_SHA256)
    evaluator: Literal["deterministic-trigger-v1"] = "deterministic-trigger-v1"
    trial_count: int = Field(default=1, ge=1, le=5)
    max_cost_usd: float = Field(default=0, ge=0, le=100)
    cases: list[SkillEvaluationCase] = Field(min_length=2, max_length=50)

    @field_validator("cases")
    @classmethod
    def valid_cases(cls, values: list[SkillEvaluationCase]) -> list[SkillEvaluationCase]:
        identifiers = [item.case_id for item in values]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("case IDs must be unique")
        if not any(item.partition == "held_out" for item in values):
            raise ValueError("at least one held-out case is required")
        if not any(not item.expected_trigger for item in values):
            raise ValueError("at least one negative-trigger case is required")
        return values


class SkillOptimizationApprovalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    candidate_digest: str = Field(pattern=_SHA256)
    evaluation_digest: str = Field(pattern=_SHA256)
    decision: Literal["approve", "deny"]
    reason: str | None = Field(default=None, min_length=1, max_length=2_000)


class SkillOptimizationApply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    candidate_digest: str = Field(pattern=_SHA256)
    approval_digest: str = Field(pattern=_SHA256)
    idempotency_key: str = Field(min_length=1, max_length=256, pattern=_SAFE_ID)


class SkillOptimizationCancel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    reason: str | None = Field(default=None, min_length=1, max_length=2_000)


class SkillOptimizationRollback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    expected_applied_digest: str = Field(pattern=_SHA256)
    idempotency_key: str = Field(min_length=1, max_length=256, pattern=_SAFE_ID)
    reason: str = Field(min_length=1, max_length=2_000)
