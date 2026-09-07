"""MCP operation handlers."""

from typing import Any, Callable

Operation = tuple[Callable[[], Any], str, int]


def operations(handler: Any, request: Any, body: dict[str, Any]) -> dict[str, Operation]:
    path = request.path_params
    service = handler.service.mcp
    return {
        "mcp_get": (
            lambda: service.get(path["agent_id"]),
            "MCP config retrieved successfully",
            200,
        ),
        "mcp_put": (
            lambda: service.update(path["agent_id"], body),
            "MCP config updated successfully",
            200,
        ),
    }
