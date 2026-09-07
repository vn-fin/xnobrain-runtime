"""Sandboxes API route declarations."""

from .definition import route

ROUTES = (
    route(
        "GET",
        "/sandboxes/detail/stream",
        "sandbox_detail_stream",
        special="sandbox_stream",
        tags=("Sandbox",),
    ),
    route("GET", "/sandboxes/{action}", "sandbox", tags=("Sandbox",)),
    route("POST", "/sandboxes/setup", "sandbox_setup", special="sandbox_setup", tags=("Sandbox",)),
)
