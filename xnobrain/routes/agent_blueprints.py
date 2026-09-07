"""Agent Maker blueprint API route declarations."""

from ..models import (
    AgentBlueprintApprovalCreate,
    AgentBlueprintCancel,
    AgentBlueprintCreate,
    AgentBlueprintLifecycleRequest,
    AgentBlueprintPatch,
)
from .definition import route

ROUTES = (
    route("GET", "/agent-blueprints", "agent_blueprints_list", tags=("Agent Maker",)),
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
        "/agent-blueprints/{blueprint_id}/scaffold",
        "agent_blueprints_scaffold",
        AgentBlueprintLifecycleRequest,
        tags=("Agent Maker",),
    ),
    route(
        "POST",
        "/agent-blueprints/{blueprint_id}/activate",
        "agent_blueprints_activate",
        AgentBlueprintLifecycleRequest,
        tags=("Agent Maker",),
    ),
    route(
        "POST",
        "/agent-blueprints/{blueprint_id}/cancel",
        "agent_blueprints_cancel",
        AgentBlueprintCancel,
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
