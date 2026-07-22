"""Single-process FastAPI application factory for Hermes and Brain4All."""

from __future__ import annotations

import json
import logging
import os
import time

import uvicorn


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
        async def structured_request_log(request, call_next):
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
            logging.getLogger("brain4all.http").info(json.dumps({
                "event": "http.request", "method": request.method,
                "route": request.scope.get("route").path if request.scope.get("route") else request.url.path,
                "status": response.status_code,
                "latency_ms": round((time.monotonic() - started) * 1000, 3),
                "trace_id": traceparent.split("-")[1] if traceparent.count("-") >= 3 else "",
            }, separators=(",", ":")))
            return response

        from .telemetry import configure
        configure(app)
    return app


app = create_app()


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(message)s")
    uvicorn.run(
        app,
        host=os.getenv("API_SERVER_HOST", "0.0.0.0"),
        port=int(os.getenv("API_SERVER_PORT", "8642")),
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
