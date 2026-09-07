"""Restore-point API routes."""

from ..models import CheckpointRestore
from .definition import route

ROUTES = (
    route(
        "GET", "/agents/{agent_id}/checkpoints/status", "checkpoints_status", tags=("Checkpoints",)
    ),
    route("GET", "/agents/{agent_id}/checkpoints", "checkpoints_list", tags=("Checkpoints",)),
    route(
        "GET",
        "/agents/{agent_id}/checkpoints/file-versions",
        "checkpoints_file_versions",
        tags=("Checkpoints",),
    ),
    route(
        "GET",
        "/agents/{agent_id}/checkpoints/{checkpoint_id}/diff",
        "checkpoints_diff",
        tags=("Checkpoints",),
    ),
    route(
        "POST",
        "/agents/{agent_id}/checkpoints/{checkpoint_id}/restore",
        "checkpoints_restore",
        CheckpointRestore,
        tags=("Checkpoints",),
    ),
)
