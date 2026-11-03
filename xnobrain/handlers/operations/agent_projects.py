"""Translate project operations without blocking the ASGI event loop."""

import asyncio

from ...trusted_context import from_request


def operations(handler, request, body):
    # Registry factories run for other groups too: resolve fields only on dispatch.
    def call(operation):
        service = handler.service.agent_projects
        trusted = from_request(request)
        if operation == "read":
            return service.read(trusted)
        return service.mutate(
            operation,
            body,
            trusted,
            project_id=request.path_params.get("project_id"),
            agent_id=request.path_params.get("agent_id"),
        )

    return {
        f"agent_projects_{operation}": (
            lambda operation=operation: asyncio.to_thread(call, operation),
            "agent projects updated" if operation != "read" else "agent projects retrieved",
            201 if operation == "create" else 200,
        )
        for operation in ("read", "create", "rename", "delete", "assign")
    }
