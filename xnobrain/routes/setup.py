"""The only XNOBrain route assembly point."""

from typing import Any

from fastapi import Body, Request
from fastapi.responses import Response

from ..feature_flags import (
    FEATURE_AGENT_CUSTOM_PAGE,
    FEATURE_UI_CUSTOMIZATION,
)
from ..feature_flags import (
    enabled as feature_enabled,
)
from ..models import APIEnvelope
from . import (
    agent_api,
    agent_blueprints,
    agents,
    analytics,
    automation,
    checkpoints,
    conversations,
    custom_page,
    events,
    hosted,
    kanban,
    marketplace,
    mcp,
    organization_artifacts,
    portability,
    providers,
    runtime_updates,
    sandboxes,
    skill_doctor,
    skill_optimizations,
    system,
    teams,
    time_control,
    ui_composition,
    workspaces,
)
from .definition import Route

BASE_ROUTE_GROUPS = (
    system.ROUTES,
    time_control.ROUTES,
    runtime_updates.ROUTES,
    events.ROUTES,
    agents.ROUTES,
    agent_api.ROUTES,
    agent_blueprints.ROUTES,
    checkpoints.ROUTES,
    mcp.ROUTES,
    workspaces.ROUTES,
    organization_artifacts.ROUTES,
    marketplace.ROUTES,
    hosted.ROUTES,
    conversations.ROUTES,
    automation.ROUTES,
    kanban.ROUTES,
    teams.ROUTES,
    providers.ROUTES,
    analytics.ROUTES,
    skill_doctor.ROUTES,
    skill_optimizations.ROUTES,
    sandboxes.ROUTES,
    portability.ROUTES,
)


def route_groups() -> tuple[tuple[Route, ...], ...]:
    """Resolve startup routes from deployment flags; disabled routes are absent."""
    optional = ()
    if feature_enabled(FEATURE_AGENT_CUSTOM_PAGE):
        optional += (custom_page.ROUTES,)
    if feature_enabled(FEATURE_UI_CUSTOMIZATION):
        optional += (ui_composition.ROUTES,)
    return optional + BASE_ROUTE_GROUPS


# Compatibility snapshots for route-contract tests. setup_routes resolves flags
# again so test/process environment changes before app startup are honored.
ROUTE_GROUPS = route_groups()
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
    elif route.special == "community_snapshot":

        async def endpoint(request: Request) -> Response:
            return await handlers.community_snapshot(request)
    elif route.special == "bundle_export":

        async def endpoint(request: Request, body=Body(...)) -> Response:
            return await handlers.bundle_export(request, body.model_dump(exclude_unset=True))

        endpoint.__annotations__["body"] = route.body
    elif route.special == "bundle_task":
        if route.body is not None:

            async def endpoint(request: Request, body=Body(...)) -> Response:
                return await handlers.bundle_task(request, body.model_dump(exclude_unset=True))

            endpoint.__annotations__["body"] = route.body
        else:

            async def endpoint(request: Request) -> Response:
                return await handlers.bundle_task(request, {})
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
    elif route.special == "workspace_event_stream":

        async def endpoint(request: Request) -> Response:
            return await handlers.workspace_event_stream(request)
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
    elif route.special == "agent_chat_completions":

        async def endpoint(request: Request, body=Body(...)) -> Response:
            return await handlers.agent_chat_completions(
                request,
                body.model_dump(exclude_unset=True),
            )

        endpoint.__annotations__["body"] = route.body
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
    routes = tuple(route for group in route_groups() for route in group)
    for route in routes:
        raw_response = route.special in {
            "stream",
            "workspace_upload",
            "workspace_upload_chunk",
            "workspace_file",
            "workspace_preview",
            "workspace_workbook",
            "community_snapshot",
            "bundle_export",
            "bundle_upload",
            "bundle_part",
            "sandbox_setup",
            "sandbox_stream",
            "workspace_event_stream",
            "agent_activity_stream",
            "kanban_stream",
            "team_run_stream",
            "conversation_run_stream",
            "agent_chat_completions",
        }
        response_model = APIEnvelope
        if route.response_data is not None:
            response_model = APIEnvelope[route.response_data]
        app.add_api_route(
            route.path,
            _endpoint(handlers, route),
            methods=[route.method],
            name=route.operation,
            tags=list(route.tags),
            include_in_schema=route.include_in_schema,
            response_model=None if raw_response else response_model,
        )

    # Hermes CLI serves its bundled SPA from a catch-all route. Compatibility
    # APIs must stay ahead of it in Starlette's ordered router.
    catch_all = [
        item for item in app.router.routes if getattr(item, "path", "") == "/{full_path:path}"
    ]
    if catch_all:
        app.router.routes[:] = [
            item for item in app.router.routes if item not in catch_all
        ] + catch_all
