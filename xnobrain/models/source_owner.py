"""Private source-owner probe request and status-only result contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SourceOwnerStatus = Literal["match", "fresh", "mismatch", "legacy_unproven"]

_OPAQUE = r"^[^\x00-\x20\x7f]{1,512}$"


class SourceOwnerProbeRequest(BaseModel):
    """Only deployment labels are accepted; actor identity never comes here."""

    model_config = ConfigDict(extra="forbid")

    environment: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    runtime_source_key: str = Field(min_length=1, max_length=512, pattern=_OPAQUE)


class SourceOwnerProbeResult(BaseModel):
    """Status only; no owner, path, content, or credential is returned."""

    model_config = ConfigDict(extra="forbid")

    status: SourceOwnerStatus
