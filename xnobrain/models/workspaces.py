"""Agent workspace contracts."""

from typing import Literal

from pydantic import BaseModel


class WorkspacePath(BaseModel):
    path: str


class WorkspaceWrite(WorkspacePath):
    content: str | None = None
    content_base64: str | None = None


class WorkspaceCreate(WorkspaceWrite):
    type: Literal["file", "directory"] = "file"


class WorkspaceRename(WorkspacePath):
    new_name: str
