"""Provider connection and model blend contracts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProviderCredential(BaseModel):
    model_config = ConfigDict(extra="allow")
    text: str | None = None
    response_text: str | None = None
    token: str | None = None
    api_key: str | None = None
    default_model: str | None = None


class ConnectionUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(min_length=1, max_length=4096)
    base_url: str | None = Field(default=None, max_length=2048)


class ConnectionPatch(BaseModel):
    active: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=999)


class SmartRouteModel(BaseModel):
    model: str = Field(min_length=1, max_length=256)
    reasoning: Literal[
        "auto", "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra",
    ] = "auto"


class SmartRouteConfig(BaseModel):
    quick: list[SmartRouteModel] = Field(default_factory=list, max_length=24)
    normal: list[SmartRouteModel] = Field(default_factory=list, max_length=24)
    difficult: list[SmartRouteModel] = Field(default_factory=list, max_length=24)
    uncertain_tier: Literal["normal", "difficult"] = "difficult"


class BlendCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    models: list[str] = Field(min_length=1, max_length=24)
    strategy: Literal["fallback", "round-robin", "fusion", "smart-route"] = "fallback"
    judge_model: str | None = Field(default=None, max_length=256)
    sticky_limit: int | None = Field(default=None, ge=1, le=1000)
    smart_route: SmartRouteConfig | None = None


class BlendPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    models: list[str] | None = Field(default=None, min_length=1, max_length=24)
    strategy: Literal["fallback", "round-robin", "fusion", "smart-route"] | None = None
    judge_model: str | None = Field(default=None, max_length=256)
    sticky_limit: int | None = Field(default=None, ge=1, le=1000)
    smart_route: SmartRouteConfig | None = None
