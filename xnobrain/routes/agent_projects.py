"""Workspace-local personal agent organization API."""

from ..models.agent_projects import (
    AgentProjectsSnapshot,
    ProjectAssignment,
    ProjectRevision,
    ProjectWrite,
)
from .definition import route

ROUTES = (
    route("GET", "/agent-projects", "agent_projects_read", response_data=AgentProjectsSnapshot),
    route(
        "POST",
        "/agent-projects",
        "agent_projects_create",
        ProjectWrite,
        response_data=AgentProjectsSnapshot,
    ),
    route(
        "PATCH",
        "/agent-projects/{project_id}",
        "agent_projects_rename",
        ProjectWrite,
        response_data=AgentProjectsSnapshot,
    ),
    route(
        "DELETE",
        "/agent-projects/{project_id}",
        "agent_projects_delete",
        ProjectRevision,
        response_data=AgentProjectsSnapshot,
    ),
    route(
        "PUT",
        "/agents/{agent_id}/project",
        "agent_projects_assign",
        ProjectAssignment,
        response_data=AgentProjectsSnapshot,
    ),
)
