"""Single-process FastAPI application factory for Hermes and XNOBrain."""

from __future__ import annotations

import logging
import os
import time

import uvicorn
from fastapi.middleware.cors import CORSMiddleware

from .logging_config import configure_logging


def create_app():
    """Extend Hermes CLI's original FastAPI app with XNOBrain routes."""
    # Configure before middleware registration as `app` is imported at module
    # load by both the source reloader and compiled entrypoint.
    configure_logging()
    from hermes_cli.web_server import app
    from .app import XNOBrainApplication
    from .integrations import AgentManager, GlobalConfigManager, LLMRouterClient

    if not getattr(app.state, "xnobrain_registered", False):
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

        composition = XNOBrainApplication(AgentManager(), GlobalConfigManager(), LLMRouterClient())
        composition.register(app)
        from .openapi_docs import configure_openapi_docs
        configure_openapi_docs(app)
        app.state.xnobrain = composition
        app.state.xnobrain_registered = True

        @app.middleware("http")
        async def request_log(request, call_next):
            started = time.monotonic()
            # XNOBrain owns the /xnobrain/api/runtime/v1 management surface. Hermes' dashboard
            # middleware protects its own /api routes with a private browser
            # session token; mark only our versioned platform routes as already
            # authenticated so they remain usable through Traefik without
            # exposing that internal token to the React application.
            if request.url.path.startswith("/xnobrain/api/runtime/"):
                request.state.token_authenticated = True
            response = None
            request_error = None
            try:
                response = await call_next(request)
                if response.status_code >= 400:
                    request_error = f"http_status_{response.status_code}"
                return response
            except Exception:
                request_error = "unhandled_exception"
                raise
            finally:
                route = request.scope.get("route")
                route_path = route.path if route else request.url.path
                from opentelemetry import trace
                span_context = trace.get_current_span().get_span_context()
                trace_id = format(span_context.trace_id, "032x") if span_context.is_valid else ""
                span_id = format(span_context.span_id, "016x") if span_context.is_valid else ""
                status_code = response.status_code if response is not None else 500
                logging.getLogger("xnobrain.http").log(
                    logging.ERROR if status_code >= 500 else logging.WARNING if status_code >= 400 else logging.INFO,
                    "HTTP request completed",
                    extra={
                        "http_method": request.method,
                        "http_route": route_path,
                        "http_status_code": status_code,
                        "duration_ms": round((time.monotonic() - started) * 1000, 3),
                        "trace_id": trace_id,
                        "span_id": span_id,
                        "error": request_error,
                    },
                )

        from .telemetry import configure
        configure(app)
    return app


app = create_app()


def main() -> None:
    reload_enabled = os.getenv("XNOBRAIN_RELOAD", "").lower() in {"1", "true", "yes", "on"}
    host = os.getenv("API_SERVER_HOST", "0.0.0.0")
    port = int(os.getenv("API_SERVER_PORT", "3000"))
    reload_shutdown_timeout = (
        max(1, int(os.getenv("RUNTIME_RELOAD_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS", "3")))
        if reload_enabled
        else None
    )
    uvicorn.run(
        "xnobrain.server:app" if reload_enabled else app,
        host=host,
        port=port,
        reload=reload_enabled,
        reload_dirs=[os.path.dirname(os.path.dirname(__file__))] if reload_enabled else None,
        reload_excludes=["node_modules", "node_modules/*"] if reload_enabled else None,
        timeout_graceful_shutdown=reload_shutdown_timeout,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
