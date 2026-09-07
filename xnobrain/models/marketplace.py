"""Typed contracts for marketplace installation and safe profile export."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import TypeAlias

_SAFE_ID = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$"
_SHA256 = r"^sha256:[0-9a-f]{64}$"
MarketplaceText: TypeAlias = Annotated[str, Field(max_length=1_000_000)]


class MarketplaceInstallRequest(BaseModel):
    package: dict


class MarketplaceUninstallRequest(BaseModel):
    local_profile_id: str


class MarketplaceUpdateRequest(BaseModel):
    package: dict
    local_profile_id: str


class MarketplaceExportRequest(BaseModel):
    """Publisher-selected metadata that participates in the package digest."""

    model_config = ConfigDict(extra="forbid")
    license: str = Field(min_length=1, max_length=200)


class MarketplaceDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    soul: str = Field(min_length=1, max_length=100_000)
    public_config: dict[str, str]
    prompts: dict[str, MarketplaceText]
    skills: dict[str, MarketplaceText]
    assets: dict[str, MarketplaceText]
    tool_requirements: list[Annotated[str, Field(pattern=_SAFE_ID)]] = Field(max_length=100)
    mcp_requirements: list[Annotated[str, Field(pattern=_SAFE_ID)]] = Field(max_length=100)
    model_slots: list[Annotated[str, Field(min_length=1, max_length=256)]] = Field(max_length=20)


class MarketplaceCompatibility(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runtime_api: Literal["v1"] = "v1"
    marketplace_package_schema: Literal[1] = 1


class MarketplaceExportFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=512)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0, le=1_000_000)
    kind: Literal["soul", "instructions", "skill", "reference", "script", "asset"]


class MarketplaceExportLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_files: int = Field(ge=1)
    max_file_bytes: int = Field(ge=1)
    max_total_bytes: int = Field(ge=1)
    file_count: int = Field(ge=1)
    total_bytes: int = Field(ge=1)


class MarketplaceExportExclusions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    categories: list[
        Literal[
            "credentials",
            "conversations_history",
            "personal_memory",
            "environment_files",
            "caches",
            "private_workspace",
            "runtime_state",
        ]
    ]
    omitted_file_count: int = Field(ge=0)


class MarketplaceExportPackage(BaseModel):
    """Complete digest-bound publication input produced by Runtime."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    source_agent_id: str = Field(min_length=1, max_length=128)
    digest: str = Field(pattern=_SHA256)
    definition: MarketplaceDefinition
    requested_permissions: list[Annotated[str, Field(pattern=_SAFE_ID)]] = Field(max_length=100)
    compatibility: MarketplaceCompatibility
    license: str = Field(min_length=1, max_length=200)
    files: list[MarketplaceExportFile] = Field(max_length=202)
    limits: MarketplaceExportLimits
    exclusions: MarketplaceExportExclusions
