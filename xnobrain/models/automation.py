"""Cron automation and delivery contracts."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class CronCreate(BaseModel):
    agent_id: str
    name: str
    prompt: str
    interval_minutes: int | None = Field(default=None, gt=0)
    schedule: str | None = None
    timezone: str = "Etc/UTC"
    mode: Literal["local"] = "local"


CronDeliveryTargetType = Literal["channel", "email", "kanban", "file"]


class CronDeliveryTargetCreate(BaseModel):
    target_type: CronDeliveryTargetType
    destination: str = Field(default="", max_length=512)


class CronBlueprintInstantiate(BaseModel):
    blueprint: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=128)
    values: dict[str, Any] = Field(default_factory=dict)
    deliver_targets: list[CronDeliveryTargetCreate] = Field(default_factory=list, max_length=32)
