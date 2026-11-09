"""Public contracts for per-agent plugins, hooks, tools, and operations."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ExtensionOperationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["install", "scan", "update", "remove", "probe"]
    plugin_id: str = Field(min_length=1, max_length=128)
    source: str | None = Field(default=None, max_length=2048)
    ref: str | None = Field(default=None, max_length=128)
    # Repository subdirectory that holds the plugin, e.g. "plugins/review".
    path: str | None = Field(default=None, max_length=512)
    idempotency_key: str = Field(min_length=1, max_length=128)


class CatalogInstall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog_id: str = Field(min_length=1, max_length=128)
    recipe_revision: int = Field(ge=1)
    fields: dict[str, str] = Field(default_factory=dict, max_length=32)
    connection_id: str | None = Field(default=None, max_length=36)


class PluginMcpConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_id: str = Field(min_length=1, max_length=36)
    plugin_revision: str = Field(min_length=1, max_length=128)

    @field_validator("connection_id")
    @classmethod
    def _canonical_connection_id(cls, value: str) -> str:
        try:
            return str(UUID(value))
        except (TypeError, ValueError, AttributeError) as error:
            raise ValueError("connection_id must be a UUID") from error


class ExtensionApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved: bool
    expected_revision: int = Field(ge=1)


class ToolsetPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    expected_revision: int = Field(ge=1)


class HookCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: str = Field(min_length=1, max_length=80)
    command: str = Field(min_length=1, max_length=4096)
    matcher: str | None = Field(default=None, max_length=256)
    timeout: int = Field(default=30, ge=1, le=300)


class HookApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved: bool
    expected_revision: int = Field(ge=1)


class PluginVisibility(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hidden: bool
    expected_revision: int = Field(ge=1)


class ProbeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=128)
