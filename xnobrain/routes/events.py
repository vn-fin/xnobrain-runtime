"""Unified workspace event stream route declarations."""

from .definition import route

ROUTES = (
    route(
        "GET",
        "/events/stream",
        "workspace_event_stream",
        special="workspace_event_stream",
        tags=("Events",),
    ),
)
