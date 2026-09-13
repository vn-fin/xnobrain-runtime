"""Agent Maker workflow and blueprint API route declarations."""

from ..models import (
    AgentBlueprintApprovalCreate,
    AgentBlueprintCancel,
    AgentBlueprintCertificationCreate,
    AgentBlueprintCreate,
    AgentBlueprintLifecycleRequest,
    AgentBlueprintPatch,
    AgentBlueprintRollbackCreate,
    AgentMakerLaunchCreate,
    AgentMakerLaunchRecord,
)
from .definition import route

ROUTES = (
    route(
        "POST",
        "/agents/{agent_id}/agent-maker/launches",
        "agent_maker_launch",
        AgentMakerLaunchCreate,
        tags=("Agent Maker",),
        response_data=AgentMakerLaunchRecord,
    ),
    route(
        "GET",
        "/agents/{agent_id}/agent-maker/launches/{session_id}",
        "agent_maker_launch_get",
        tags=("Agent Maker",),
        response_data=AgentMakerLaunchRecord,
    ),
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
        "/agent-blueprints/{blueprint_id}/certifications",
        "agent_blueprints_certify",
        AgentBlueprintCertificationCreate,
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
        "/agent-blueprints/{blueprint_id}/rollback",
        "agent_blueprints_rollback",
        AgentBlueprintRollbackCreate,
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
