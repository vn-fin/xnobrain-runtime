"""Single-process FastAPI application factory for Hermes and Brain4All."""

from __future__ import annotations

import logging
import os
import time

import uvicorn

from .logging_config import configure_logging


def create_app():
    """Extend Hermes CLI's original FastAPI app with Brain4All routes."""
    from hermes_cli.web_server import app
    from .app import Brain4AllApplication
    from .integrations import AgentManager, GlobalConfigManager, NineRouterManager

    if not getattr(app.state, "brain4all_registered", False):
        composition = Brain4AllApplication(AgentManager(), GlobalConfigManager(), NineRouterManager())
        composition.register(app)
        app.state.brain4all = composition
        app.state.brain4all_registered = True

        @app.middleware("http")
        async def request_log(request, call_next):
            started = time.monotonic()
            # Brain4All owns the /api/v1 management surface. Hermes' dashboard
            # middleware protects its own /api routes with a private browser
            # session token; mark only our versioned platform routes as already
            # authenticated so they remain usable through Traefik without
            # exposing that internal token to the React application.
            if request.url.path.startswith("/api/v1/"):
                request.state.token_authenticated = True
            response = await call_next(request)
            traceparent = request.headers.get("traceparent", "")
            route = request.scope.get("route")
            route_path = route.path if route else request.url.path
            trace_id = traceparent.split("-")[1] if traceparent.count("-") >= 3 else "-"
            logging.getLogger("brain4all.http").info(
                "HTTP %s %s -> %s (%.3f ms, trace_id=%s)",
                request.method,
                route_path,
                response.status_code,
                (time.monotonic() - started) * 1000,
                trace_id,
            )
            return response

        from .telemetry import configure
        configure(app)
    return app


app = create_app()


def main() -> None:
    configure_logging()
    reload_enabled = os.getenv("BRAIN4ALL_RELOAD", "").lower() in {"1", "true", "yes", "on"}
    host = os.getenv("API_SERVER_HOST", "0.0.0.0")
    port = int(os.getenv("API_SERVER_PORT", "8642"))
    uvicorn.run(
        "brain4all.server:app" if reload_enabled else app,
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
