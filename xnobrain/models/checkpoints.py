"""Workspace checkpoint API contracts."""

from pydantic import BaseModel


class CheckpointRestore(BaseModel):
    path: str | None = None

