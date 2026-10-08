"""Private, signed Community delivery contracts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CommunityFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=512)
    content: str = Field(max_length=14_000_000)
    encoding: Literal["", "utf8", "utf-8", "base64"]
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    size: int = Field(ge=0, le=10_000_000)
    media_type: str = ""
    preview: str = ""


class CommunitySkillInstall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(pattern=r"^ski_[A-Za-z0-9_-]+$", max_length=128)
    candidate_id: str = Field(min_length=1, max_length=128)
    use_grant_id: str = Field(max_length=128)
    skill_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$", max_length=128)
    version: str = Field(min_length=1, max_length=128)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    files: list[CommunityFile] = Field(min_length=1, max_length=200)
    target_profile_id: str = Field(min_length=1, max_length=128)
    collision_resolution: Literal["fail", "rename", "replace"]
    rename_to: str = Field(default="", max_length=128)
    accepted_permissions: list[str] = Field(max_length=100)
    enable: Literal[False] = False
