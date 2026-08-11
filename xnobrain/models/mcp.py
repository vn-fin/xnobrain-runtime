"""Model Context Protocol server contracts."""

from typing import Any

from pydantic import BaseModel, Field


class MCPConfig(BaseModel):
    servers: dict[str, dict[str, Any]] = Field(default_factory=dict)
