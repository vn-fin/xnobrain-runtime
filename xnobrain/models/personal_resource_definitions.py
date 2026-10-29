"""Versioned references and private configuration snapshot requests."""

from pydantic import BaseModel, ConfigDict, Field


class ExternalAgentReference(BaseModel):
    """An identity reference, never a workspace address or execution authority."""

    model_config = ConfigDict(extra="forbid")
    organization_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    registration_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    grant_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")


class PersonalResourceDefinitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_revision: str = Field(default="", max_length=71)
    board_id: str | None = Field(default=None, max_length=128)
