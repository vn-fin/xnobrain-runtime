"""Shared versioned route primitives for the XNOBrain public API."""

from dataclasses import dataclass

API_NAMESPACE = "/xnobrain/api/runtime"
API_VERSION = "v1"
API_PREFIX = f"{API_NAMESPACE}/{API_VERSION}"


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    operation: str
    body: type | None = None
    special: str | None = None
    tags: tuple[str, ...] = ("XNOBrain",)
    include_in_schema: bool = True


def route(
    method: str,
    path: str,
    operation: str,
    body: type | None = None,
    special: str | None = None,
    tags: tuple[str, ...] = ("XNOBrain",),
    include_in_schema: bool = True,
) -> Route:
    """Declare a route relative to the current public API version."""
    suffix = f"/{path.lstrip('/')}" if path else ""
    return Route(method, f"{API_PREFIX}{suffix}", operation, body, special, tags, include_in_schema)
