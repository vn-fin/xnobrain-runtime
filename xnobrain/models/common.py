"""Contracts shared across service groups."""

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict

ResponseData = TypeVar("ResponseData")


class APIEnvelope(BaseModel, Generic[ResponseData]):
    success: bool = True
    data: ResponseData | None = None
    message: str = "ok"
    status_code: int = 200


class GenericObject(BaseModel):
    """Document an intentionally extensible Hermes-native payload."""

    model_config = ConfigDict(extra="allow")
