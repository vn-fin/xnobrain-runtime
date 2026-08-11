"""Model Context Protocol server route declarations."""

from ..models import MCPConfig
from .definition import route

ROUTES = (
    route("GET", "/agents-mcp/{agent_id}", "mcp_get", tags=("MCP",)),
    route("PUT", "/agents-mcp/{agent_id}", "mcp_put", MCPConfig, tags=("MCP",)),
)
