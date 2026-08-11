"""Portable bundle transfer contracts."""

from pydantic import BaseModel, Field


class BundleExport(BaseModel):
    agent_ids: list[str] = Field(default_factory=list, max_length=100)
    team_ids: list[str] = Field(default_factory=list, max_length=100)
    include_conversations: bool = False


class BundleUploadStart(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0)
    sha256: str | None = Field(default=None, min_length=64, max_length=64)


class BundleUploadComplete(BaseModel):
    sha256: str | None = Field(default=None, min_length=64, max_length=64)


class BundleUploadApply(BaseModel):
    environment: dict[str, str] = Field(default_factory=dict)
