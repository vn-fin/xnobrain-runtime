"""Typed contracts for the private Runtime update lifecycle adapter."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

StorageLayout = Literal[
    "root_only",
    "incus_custom_volume",
    "docker_volume",
    "native_vm",
    "unknown",
]


class RuntimeUpdateTarget(BaseModel):
    """Immutable release identity selected and authorized by Control."""

    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1, max_length=64)
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    runtime_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    data_schema: int = Field(ge=1)


class RuntimeUpdateRequest(BaseModel):
    """Common fenced operation identity."""

    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(pattern=r"^upd_[A-Za-z0-9_-]{1,120}$")
    generation: int = Field(ge=1)
    target: RuntimeUpdateTarget


class RuntimeUpdatePreflight(RuntimeUpdateRequest):
    expected_layout: StorageLayout
    data_path: str = Field(min_length=1, max_length=4096)
    required_free_bytes: int = Field(default=0, ge=0)


class RuntimeUpdateDrain(RuntimeUpdateRequest):
    deadline_seconds: float = Field(default=30.0, ge=0.0, le=300.0)
    cancel_active_at_deadline: bool = False


class RuntimeUpdateCheckpoint(RuntimeUpdateRequest):
    pass


class RuntimeUpdateReadiness(RuntimeUpdateRequest):
    checkpoint_id: str | None = Field(default=None, pattern=r"^ckp_[0-9a-f]{64}$")


class RuntimeUpdatePostVerify(RuntimeUpdateRequest):
    checkpoint_id: str = Field(pattern=r"^ckp_[0-9a-f]{64}$")


class RuntimeUpdateRecovery(RuntimeUpdateRequest):
    action: Literal[
        "retry_selected_target",
        "approve_newer_target",
        "needs_operator",
    ]
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    newer_target: RuntimeUpdateTarget | None = None

    @model_validator(mode="after")
    def validate_recovery_target(self) -> "RuntimeUpdateRecovery":
        if self.action == "approve_newer_target" and self.newer_target is None:
            raise ValueError("newer_target is required for approve_newer_target")
        if self.action != "approve_newer_target" and self.newer_target is not None:
            raise ValueError("newer_target requires approve_newer_target")
        return self


class ReleaseSelector(BaseModel):
    """Narrow Big Brother selector; arbitrary remotes and commits are impossible."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["release_tag", "release_branch_latest"]
    tag: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

    @model_validator(mode="after")
    def validate_tag(self) -> "ReleaseSelector":
        if self.type == "release_tag" and not self.tag:
            raise ValueError("tag is required for release_tag")
        if self.type == "release_branch_latest" and self.tag is not None:
            raise ValueError("tag is not accepted for release_branch_latest")
        return self
