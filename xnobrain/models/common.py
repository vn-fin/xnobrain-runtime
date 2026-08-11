"""Contracts shared across service groups."""

from typing import Any

from pydantic import BaseModel, ConfigDict


class APIEnvelope(BaseModel):
    success: bool = True
    data: Any = None
    message: str = "ok"
    status_code: int = 200


class GenericObject(BaseModel):
    """Document an intentionally extensible Hermes-native payload."""

    model_config = ConfigDict(extra="allow")
