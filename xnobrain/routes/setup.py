"""The only XNOBrain route assembly point."""

from typing import Any

from fastapi import Body, Request
from fastapi.responses import Response

from ..models import APIEnvelope
from .definition import Route
from . import organization_artifacts, analytics, agents, automation, checkpoints, conversations, kanban, mcp, portability, providers, sandboxes, system, teams, workspaces

ROUTE_GROUPS = (
    system.ROUTES,
    agents.ROUTES,
    checkpoints.ROUTES,
    mcp.ROUTES,
    workspaces.ROUTES,
    organization_artifacts.ROUTES,
    conversations.ROUTES,
    automation.ROUTES,
    kanban.ROUTES,
    teams.ROUTES,
    providers.ROUTES,
    analytics.ROUTES,
    sandboxes.ROUTES,
    portability.ROUTES,
)
ROUTES = tuple(route for group in ROUTE_GROUPS for route in group)


def _endpoint(handlers: Any, route: Route):
    if route.special == "stream":
        async def endpoint(request: Request, body=Body(...)) -> Response:
            return await handlers.stream(request, body.model_dump(exclude_unset=True))
        endpoint.__annotations__["body"] = route.body
    elif route.special == "workspace_upload":
        async def endpoint(request: Request) -> Response:
            return await handlers.workspace_upload(request)
    elif route.special == "workspace_upload_chunk":
        async def endpoint(request: Request) -> Response:
            return await handlers.workspace_upload_chunk(request)
    elif route.special == "workspace_file":
        async def endpoint(request: Request) -> Response:
            return await handlers.workspace_file(request)
    elif route.special == "workspace_preview":
        async def endpoint(request: Request) -> Response:
            return await handlers.workspace_preview(request)
    elif route.special == "workspace_workbook":
        async def endpoint(request: Request) -> Response:
            return await handlers.workspace_workbook(request)
    elif route.special == "bundle_export":
        async def endpoint(request: Request, body=Body(...)) -> Response:
            return await handlers.bundle_export(request, body.model_dump(exclude_unset=True))
        endpoint.__annotations__["body"] = route.body
    elif route.special == "bundle_upload":
        async def endpoint(request: Request) -> Response:
            return await handlers.bundle_upload(request)
    elif route.special == "bundle_part":
        async def endpoint(request: Request) -> Response:
            return await handlers.bundle_part(request)
    elif route.special == "sandbox_setup":
        async def endpoint(request: Request) -> Response:
            return await handlers.sandbox_setup(request)
    elif route.special == "sandbox_stream":
        async def endpoint(request: Request) -> Response:
            return await handlers.sandbox_detail_stream(request)
    elif route.special == "agent_activity_stream":
        async def endpoint(request: Request) -> Response:
            return await handlers.agent_activity_stream(request)
    elif route.special == "kanban_stream":
        async def endpoint(request: Request) -> Response:
            return await handlers.kanban_event_stream(request)
    elif route.special == "team_run_stream":
        async def endpoint(request: Request) -> Response:
            return await handlers.team_run_event_stream(request)
    elif route.special == "conversation_run_stream":
        async def endpoint(request: Request) -> Response:
            return await handlers.conversation_run_event_stream(request)
    elif route.body is not None:
        async def endpoint(request: Request, body=Body(...)) -> Response:
            return await handlers.dispatch(request, body.model_dump(exclude_unset=True))
        endpoint.__annotations__["body"] = route.body
    else:
        async def endpoint(request: Request) -> Response:
            return await handlers.dispatch(request, {})
    endpoint.__name__ = route.operation
    endpoint.__doc__ = f"{route.method} {route.path}"
    return endpoint


def setup_routes(app: Any, handlers: Any) -> None:
    for route in ROUTES:
        raw_response = route.special in {"stream", "workspace_upload", "workspace_upload_chunk", "workspace_file", "workspace_preview", "workspace_workbook", "bundle_export", "bundle_upload", "bundle_part", "sandbox_setup", "sandbox_stream", "agent_activity_stream", "kanban_stream", "team_run_stream", "conversation_run_stream"}
        app.add_api_route(
            route.path,
            _endpoint(handlers, route),
            methods=[route.method],
            name=route.operation,
            tags=list(route.tags),
            include_in_schema=route.include_in_schema,
            response_model=None if raw_response else APIEnvelope,
        )

    # Hermes CLI serves its bundled SPA from a catch-all route. Compatibility
    # APIs must stay ahead of it in Starlette's ordered router.
    catch_all = [item for item in app.router.routes if getattr(item, "path", "") == "/{full_path:path}"]
    if catch_all:
        app.router.routes[:] = [item for item in app.router.routes if item not in catch_all] + catch_all
