"""Single-process FastAPI application factory for Hermes and Brain4All."""

from __future__ import annotations

import logging
import os
import time

import uvicorn
from fastapi.middleware.cors import CORSMiddleware

from .logging_config import configure_logging


def create_app():
    """Extend Hermes CLI's original FastAPI app with Brain4All routes."""
    from hermes_cli.web_server import app
    from .app import Brain4AllApplication
    from .integrations import AgentManager, GlobalConfigManager, NineRouterManager

    if not getattr(app.state, "brain4all_registered", False):
        cors_origins = [
            origin.strip()
            for origin in os.getenv("CORS_ALLOWED_ORIGINS", "*").split(",")
            if origin.strip()
        ]
        if cors_origins:
            app.add_middleware(
                CORSMiddleware,
                allow_origins=cors_origins,
                allow_credentials=False,
                allow_methods=["*"],
                allow_headers=["*"],
            )

        composition = Brain4AllApplication(AgentManager(), GlobalConfigManager(), NineRouterManager())
        composition.register(app)
        from .openapi_docs import configure_openapi_docs
        configure_openapi_docs(app)
        app.state.brain4all = composition
        app.state.brain4all_registered = True

        @app.middleware("http")
        async def request_log(request, call_next):
            started = time.monotonic()
            # Brain4All owns the /xnobrain/api/runtime/v1 management surface. Hermes' dashboard
            # middleware protects its own /api routes with a private browser
            # session token; mark only our versioned platform routes as already
            # authenticated so they remain usable through Traefik without
            # exposing that internal token to the React application.
            if request.url.path.startswith("/xnobrain/api/runtime/"):
                request.state.token_authenticated = True
            response = await call_next(request)
            traceparent = request.headers.get("traceparent", "")
            route = request.scope.get("route")
            route_path = route.path if route else request.url.path
            trace_id = traceparent.split("-")[1] if traceparent.count("-") >= 3 else "-"
            logging.getLogger("xnobrain.http").info(
                "HTTP request completed",
                extra={
                    "http_method": request.method,
                    "http_route": route_path,
                    "http_status_code": response.status_code,
                    "duration_ms": round((time.monotonic() - started) * 1000, 3),
                    "trace_id": trace_id,
                },
            )
            return response

        from .telemetry import configure
        configure(app)
    return app


app = create_app()


def main() -> None:
    configure_logging()
    reload_enabled = os.getenv("XNOBRAIN_RELOAD", "").lower() in {"1", "true", "yes", "on"}
    host = os.getenv("API_SERVER_HOST", "0.0.0.0")
    port = int(os.getenv("API_SERVER_PORT", "8642"))
    uvicorn.run(
        "xnobrain.server:app" if reload_enabled else app,
        host=host,
        port=port,
        reload=reload_enabled,
        reload_dirs=[os.path.dirname(os.path.dirname(__file__))] if reload_enabled else None,
        reload_excludes=["src", "src/*", "node_modules", "node_modules/*"] if reload_enabled else None,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
