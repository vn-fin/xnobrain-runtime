"""System API route declarations."""

from .definition import route

ROUTES = (
    route("GET", "/health", "health", tags=("System",)),
    route("GET", "/health/9router", "nine_router_health", tags=("System",)),
    route("GET", "/ping", "health", tags=("System",)),
    route("GET", "/limits", "limits", tags=("System",)),
    route("GET", "/system/deployment", "deployment", tags=("System",)),
)
