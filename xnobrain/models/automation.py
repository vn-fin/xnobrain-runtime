"""Cron automation and delivery contracts."""

from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CronCreate(BaseModel):
    agent_id: str
    name: str
    prompt: str
    interval_minutes: int | None = Field(default=None, gt=0)
    schedule: str | None = Field(default=None, min_length=1, max_length=256)
    timezone: str | None = None
    mode: Literal["local"] = "local"
    # Optional per-job inference pin. `provider` is a connector display hint
    # (e.g. "codex", "grok-cli"), not the native provider. When pinned, the
    # job always runs through the LLM router. Omit to follow the agent model.
    provider: str | None = Field(default=None, max_length=128)
    model: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def unambiguous_schedule(self):
        if self.interval_minutes is not None and self.schedule is not None:
            raise ValueError("choose interval_minutes or schedule, not both")
        return self

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value != "UTC" and ("/" not in value or value.startswith("/")):
            raise ValueError("timezone must be an IANA name")
        if len(value) > 128 or any(part in {"", ".", ".."} for part in value.split("/")):
            raise ValueError("timezone must be an IANA name")
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as error:
            raise ValueError("timezone is unavailable") from error
        return value


class CronUpdate(BaseModel):
    """Partial update to a cron job, including its recurrence.

    `provider` is a connector display hint. The native job stores the LLM
    router provider when pinned.
    """

    name: str | None = Field(default=None, min_length=1, max_length=256)
    prompt: str | None = Field(default=None, min_length=1, max_length=8192)
    interval_minutes: int | None = Field(default=None, gt=0)
    schedule: str | None = Field(default=None, min_length=1, max_length=256)
    timezone: str | None = Field(default=None, max_length=128)
    provider: str | None = Field(default=None, max_length=128)
    model: str | None = Field(default=None, max_length=256)
    # Explicitly clear the pin (revert to following the agent's current model).
    unpin: bool = False

    @model_validator(mode="after")
    def has_change(self):
        pins = self.provider is not None or self.model is not None
        edits = self.name is not None or self.prompt is not None
        schedule_change = self.interval_minutes is not None or self.schedule is not None
        if self.interval_minutes is not None and self.schedule is not None:
            raise ValueError("choose interval_minutes or schedule, not both")
        if self.timezone is not None and self.schedule is None:
            raise ValueError("timezone requires schedule")
        if not self.unpin and not pins and not edits and not schedule_change:
            raise ValueError(
                "provide name/prompt/schedule/provider/model to update, or unpin=true"
            )
        if self.unpin and pins:
            raise ValueError("choose unpin or provider/model, not both")
        return self

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        return CronCreate.validate_timezone(value) if value is not None else None


CronDeliveryTargetType = Literal["channel", "email", "kanban", "file"]


class CronDeliveryTargetCreate(BaseModel):
    target_type: CronDeliveryTargetType
    destination: str = Field(default="", max_length=512)


class CronBlueprintInstantiate(BaseModel):
    timezone: str | None = None

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        return CronCreate.validate_timezone(value) if value is not None else None

    blueprint: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=128)
    values: dict[str, Any] = Field(default_factory=dict)
    deliver_targets: list[CronDeliveryTargetCreate] = Field(default_factory=list, max_length=32)


class CronSchedulePreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schedule: str = Field(min_length=1, max_length=256)
    timezone: str | None = None
    after: datetime | None = None
    count: int = Field(default=5, ge=1, le=20)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        return CronCreate.validate_timezone(value) if value is not None else None

    @field_validator("after")
    @classmethod
    def aware_cutoff(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("preview cutoff must include timezone")
        return value


class CronPreviewOccurrence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    utc: str
    local: str
    timezone: str


class CronSchedulePreviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    occurrences: list[CronPreviewOccurrence] = Field(min_length=1, max_length=20)
    dst_policy: Literal["skip_gap_earlier_fold"]
    executor_parity_verified: bool
