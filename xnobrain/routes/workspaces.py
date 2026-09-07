"""Workspaces API route declarations."""

from ..models import WorkspaceCreate, WorkspacePath, WorkspaceRename, WorkspaceWrite
from .definition import route

ROUTES = (
    route("GET", "/agents-workspaces/{agent_id}", "workspace_list", tags=("Workspace",)),
    route(
        "GET",
        "/agents-workspaces/{agent_id}/file",
        "workspace_view",
        special="workspace_file",
        tags=("Workspace",),
    ),
    route(
        "GET",
        "/agents-workspaces/{agent_id}/preview",
        "workspace_preview",
        special="workspace_preview",
        tags=("Workspace",),
    ),
    route(
        "GET",
        "/agents-workspaces/{agent_id}/workbook",
        "workspace_workbook",
        special="workspace_workbook",
        tags=("Workspace",),
    ),
    route(
        "POST",
        "/agents-workspaces/{agent_id}/read",
        "workspace_read",
        WorkspacePath,
        tags=("Workspace",),
    ),
    route(
        "POST",
        "/agents-workspaces/{agent_id}/write",
        "workspace_write",
        WorkspaceWrite,
        tags=("Workspace",),
    ),
    route(
        "POST",
        "/agents-workspaces/{agent_id}/create",
        "workspace_create",
        WorkspaceCreate,
        tags=("Workspace",),
    ),
    route(
        "POST",
        "/agents-workspaces/{agent_id}/upload/chunk",
        "workspace_upload_chunk",
        special="workspace_upload_chunk",
        tags=("Workspace",),
    ),
    route(
        "POST",
        "/agents-workspaces/{agent_id}/upload",
        "workspace_upload",
        special="workspace_upload",
        tags=("Workspace",),
    ),
    route(
        "POST",
        "/agents-workspaces/{agent_id}/delete",
        "workspace_delete",
        WorkspacePath,
        tags=("Workspace",),
    ),
    route(
        "POST",
        "/agents-workspaces/{agent_id}/rename",
        "workspace_rename",
        WorkspaceRename,
        tags=("Workspace",),
    ),
)
