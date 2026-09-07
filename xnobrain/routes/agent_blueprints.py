"""Agent Maker blueprint API route declarations."""

from ..models import (
    AgentBlueprintApprovalCreate,
    AgentBlueprintCreate,
    AgentBlueprintPatch,
)
from .definition import route

ROUTES = (
    route(
        "POST",
        "/agent-blueprints",
        "agent_blueprints_create",
        AgentBlueprintCreate,
        tags=("Agent Maker",),
    ),
    route(
        "GET",
        "/agent-blueprints/{blueprint_id}",
        "agent_blueprints_get",
        tags=("Agent Maker",),
    ),
    route(
        "PATCH",
        "/agent-blueprints/{blueprint_id}",
        "agent_blueprints_patch",
        AgentBlueprintPatch,
        tags=("Agent Maker",),
    ),
    route(
        "POST",
        "/agent-blueprints/{blueprint_id}/approvals",
        "agent_blueprints_approve",
        AgentBlueprintApprovalCreate,
        tags=("Agent Maker",),
    ),
)
