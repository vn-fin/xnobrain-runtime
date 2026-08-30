"""Approved organization artifact transfer contracts."""
from typing import Literal
from pydantic import BaseModel, Field
class LocalArtifactInspect(BaseModel):
    path: str
class ArtifactTransfer(BaseModel):
    url: str
    method: Literal["GET", "PUT"]
    headers: dict[str,str] = Field(default_factory=dict)
    expires_at: str
class PublishOrganizationArtifact(BaseModel):
    path: str
    file_id: str
    version_id: str
    transfer: ArtifactTransfer
    approved: bool
class ImportOrganizationArtifact(BaseModel):
    file_id: str
    version_id: str
    name: str
    size_bytes: int = Field(gt=0)
    sha256: str
    media_type: str
    transfer: ArtifactTransfer
    destination: str
    collision: Literal["cancel", "keep_both", "replace"] = "cancel"
    run_id: str | None = None
