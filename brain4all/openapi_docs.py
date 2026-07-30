"""Version-independent OpenAPI and Swagger UI routes for Brain4All."""

from __future__ import annotations

from typing import Any

from fastapi.openapi.docs import (
    get_swagger_ui_html,
    get_swagger_ui_oauth2_redirect_html,
)
from fastapi.responses import JSONResponse

DOCS_URL = "/api/brain/swagger_docs"
OPENAPI_URL = "/api/brain/openapi.json"
OAUTH2_REDIRECT_URL = f"{DOCS_URL}/oauth2-redirect"

_FASTAPI_DOC_ROUTE_NAMES = {
    "openapi",
    "swagger_ui_html",
    "swagger_ui_redirect",
    "redoc_html",
}


def configure_openapi_docs(app: Any) -> None:
    """Replace FastAPI's root documentation endpoints with Brain-owned paths."""
    if getattr(app.state, "brain4all_docs_registered", False):
        return

    app.router.routes[:] = [
        route
        for route in app.router.routes
        if getattr(route, "name", "") not in _FASTAPI_DOC_ROUTE_NAMES
    ]
    app.docs_url = DOCS_URL
    app.openapi_url = OPENAPI_URL
    app.redoc_url = None
    app.swagger_ui_oauth2_redirect_url = OAUTH2_REDIRECT_URL

    async def openapi_schema() -> JSONResponse:
        return JSONResponse(app.openapi())

    async def swagger_ui():
        return get_swagger_ui_html(
            openapi_url=OPENAPI_URL,
            title=f"{app.title} - Swagger UI",
            oauth2_redirect_url=OAUTH2_REDIRECT_URL,
            swagger_ui_parameters={"persistAuthorization": True},
        )

    async def swagger_oauth2_redirect():
        return get_swagger_ui_oauth2_redirect_html()

    app.add_api_route(
        OPENAPI_URL,
        openapi_schema,
        include_in_schema=False,
        name="brain4all_openapi",
    )
    app.add_api_route(
        DOCS_URL,
        swagger_ui,
        include_in_schema=False,
        name="brain4all_swagger_ui",
    )
    app.add_api_route(
        OAUTH2_REDIRECT_URL,
        swagger_oauth2_redirect,
        include_in_schema=False,
        name="brain4all_swagger_oauth2_redirect",
    )

    # Hermes serves its SPA through an ordered catch-all route.
    catch_all = [
        route
        for route in app.router.routes
        if getattr(route, "path", "") == "/{full_path:path}"
    ]
    if catch_all:
        app.router.routes[:] = [
            route for route in app.router.routes if route not in catch_all
        ] + catch_all

    app.openapi_schema = None
    app.state.brain4all_docs_registered = True
