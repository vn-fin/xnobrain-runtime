"""Typed contracts for Control's private FT0013 Runtime adapter."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .automation import CronCreate

_HASH_PATTERN = r"^sha256:[0-9a-f]{64}$"
_TOKEN_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$"


class TimeWorkspaceSpec(BaseModel):
    """Control-owned workspace context; never used as local authorization."""

    # Go's encoding/json emits exported field names today. Accept snake_case as
    # an additive compatibility shape if Control adds tags in a later release.
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        alias_generator=lambda value: "".join(part.capitalize() for part in value.split("_")),
    )

    user_id: str = Field(default="", alias="UserID", max_length=256)
    tenant_id: str = Field(default="", alias="TenantID", max_length=256)
    requested_name: str = Field(default="", alias="RequestedName", max_length=256)
    instance_name: str = Field(default="", alias="InstanceName", max_length=256)
    instance_type: str = Field(default="", alias="InstanceType", max_length=64)
    data_volume_name: str = Field(default="", alias="DataVolumeName", max_length=256)
    environment_name: str = Field(default="", alias="EnvironmentName", max_length=64)
    project: str = Field(default="", alias="Project", max_length=256)
    image_reference: str = Field(default="", alias="ImageReference", max_length=1024)
    storage_pool: str = Field(default="", alias="StoragePool", max_length=256)
    network_name: str = Field(default="", alias="NetworkName", max_length=256)
    cpu_count: int = Field(default=0, ge=0, alias="CPUCount")
    memory_bytes: int = Field(default=0, ge=0, alias="MemoryBytes")
    root_disk_bytes: int = Field(default=0, ge=0, alias="RootDiskBytes")
    data_disk_bytes: int = Field(default=0, ge=0, alias="DataDiskBytes")
    runtime_secret: str = Field(default="", alias="RuntimeSecret", max_length=512)
    llm_router_url: str = Field(default="", alias="LLMRouterURL", max_length=4096)
    llm_api_key: str = Field(default="", alias="LLMAPIKey", max_length=4096)


class TimeCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Literal["workspace_timezone", "per_job_timezone"]
    supported: bool
    reason: str = Field(default="", max_length=280)


class TimeObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter: str = Field(pattern=_TOKEN_PATTERN)
    status: Literal["verified", "unknown", "unsupported", "failed"]
    timezone: str | None = None
    observed_at: datetime
    reason: str = Field(default="", max_length=280)


class TimeAdapterState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    effective_timezone: str | None
    scheduler_timezone: str | None
    sandbox_local_time: str
    utc_time: str
    observed_at: datetime
    capabilities: list[TimeCapability] = Field(max_length=16)
    restart_required: bool
    reopen_processes: bool
    observations: list[TimeObservation] = Field(max_length=16)


class TimeOccurrence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    utc: datetime
    local: str = Field(min_length=1, max_length=64)


class TimeMigrationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=_TOKEN_PATTERN)
    label: str = Field(default="", max_length=280)
    old_timezone: str | None = None
    new_timezone: str
    revision: int = Field(ge=1)
    status: Literal["ready", "stale", "unauthorized", "unsupported", "unknown"] = "ready"
    reason: str = Field(default="", max_length=280)
    next_occurrences: list[TimeOccurrence] = Field(default_factory=list, max_length=5)

    @field_validator("new_timezone")
    @classmethod
    def valid_new_timezone(cls, value: str) -> str:
        return CronCreate.validate_timezone(value)


class TimeMigrationRef(BaseModel):
    """Current Control preview reference, including Go zero-value fields."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=_TOKEN_PATTERN)
    label: str = Field(default="", max_length=280)
    old_timezone: None = None
    new_timezone: str
    revision: int = Field(ge=1)
    status: Literal[""] = ""
    reason: str = Field(default="", max_length=280)
    next_occurrences: list[TimeOccurrence] | None = None

    @field_validator("new_timezone")
    @classmethod
    def valid_new_timezone(cls, value: str) -> str:
        return CronCreate.validate_timezone(value)


class TimeAdapterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace: TimeWorkspaceSpec
    operation_id: str = Field(pattern=_TOKEN_PATTERN)
    fence: int = Field(ge=1)
    plan_hash: str = Field(pattern=_HASH_PATTERN)
    target_timezone: str
    expected_revision: int = Field(ge=0)
    cutoff: datetime | None = None
    items: list[TimeMigrationItem] = Field(default_factory=list, max_length=100)

    @field_validator("target_timezone")
    @classmethod
    def valid_target_timezone(cls, value: str) -> str:
        return CronCreate.validate_timezone(value)

    @field_validator("cutoff")
    @classmethod
    def aware_cutoff(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("migration cutoff must include timezone")
        return value


class TimeSettingsApplyRequest(TimeAdapterRequest):
    @model_validator(mode="after")
    def settings_only(self) -> "TimeSettingsApplyRequest":
        if self.cutoff is not None and self.cutoff.year != 1:
            raise ValueError("settings apply does not accept migration fields")
        if self.items:
            raise ValueError("settings apply does not accept migration fields")
        return self


class TimeScheduleMigrateRequest(TimeAdapterRequest):
    @model_validator(mode="after")
    def exact_migration(self) -> "TimeScheduleMigrateRequest":
        if self.cutoff is None or not self.items:
            raise ValueError("schedule migration requires exact items and cutoff")
        seen: set[str] = set()
        for item in self.items:
            if (
                item.id in seen
                or item.new_timezone != self.target_timezone
                or item.status != "ready"
            ):
                raise ValueError("schedule migration items must match the exact plan")
            seen.add(item.id)
        return self


class TimeMigrationPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace: TimeWorkspaceSpec
    operation_id: str = Field(default="", max_length=128)
    fence: int = Field(default=0, ge=0)
    plan_hash: str = Field(default="", max_length=128)
    target_timezone: str
    expected_revision: int = Field(default=0, ge=0)
    cutoff: datetime
    items: list[TimeMigrationRef] = Field(min_length=1, max_length=100)

    @field_validator("target_timezone")
    @classmethod
    def valid_target_timezone(cls, value: str) -> str:
        return CronCreate.validate_timezone(value)

    @field_validator("cutoff")
    @classmethod
    def aware_cutoff(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("migration cutoff must include timezone")
        return value

    @model_validator(mode="after")
    def validate_preview_items(self) -> "TimeMigrationPreviewRequest":
        if self.operation_id or self.fence or self.plan_hash or self.expected_revision:
            raise ValueError("migration preview does not accept mutation authority fields")
        seen: set[str] = set()
        for item in self.items:
            if item.id in seen or item.new_timezone != self.target_timezone:
                raise ValueError("migration preview items must be exact and unique")
            if item.label or item.reason or item.next_occurrences:
                raise ValueError("migration preview accepts schedule references only")
            seen.add(item.id)
        return self


class TimeMigrationPreviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[TimeMigrationItem] = Field(max_length=100)


class TimeItemResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=_TOKEN_PATTERN)
    status: Literal[
        "succeeded",
        "failed",
        "stale",
        "unauthorized",
        "unsupported",
        "skipped",
    ]
    reason: str = Field(default="", max_length=280)
    revision: int = Field(default=0, ge=0)
    next_run_at: str = Field(default="", max_length=64)


class TimeAdapterResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verified: bool
    partial: bool
    error_code: str = Field(default="", max_length=64)
    observations: list[TimeObservation] = Field(default_factory=list, max_length=16)
    item_results: list[TimeItemResult] = Field(default_factory=list, max_length=100)
    effective_timezone: str | None = None
    scheduler_timezone: str | None = None
    sandbox_local_time: str = Field(default="", max_length=64)
    utc_time: str = Field(default="", max_length=64)
    observed_at: datetime | None = None
