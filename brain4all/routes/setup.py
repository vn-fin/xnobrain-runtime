"""The only Brain4All route assembly point.

Hermes CLI owns its native ``/api`` routes. This module adds the stable Brain4All
compatibility surface and deliberately keeps every URL out of handlers and
services so the public contract is reviewable in one place.
"""

from dataclasses import dataclass
from typing import Any

from fastapi import Body, Request
from fastapi.responses import Response

from ..models import (
    AgentBudgetPatch, BlendCreate, BlendPatch,
    AgentCreate, AgentMetadataPatch, APIEnvelope, BundleExport, BundleUploadApply,
    BundleUploadComplete, BundleUploadStart, ChatRequest,
    ConfigPatch, ConnectionCreate, ConnectionPatch, ConversationCreate,
    ConversationRename, CronCreate, EnabledPatch,
    GenericObject, MCPConfig, MemoryPatch, ProviderCredential, RunApproval,
    SkillInstall, TeamCreate, TeamRun, WorkspaceCreate, WorkspacePath, WorkspaceWrite,
    KanbanAssign, KanbanBoardCreate, KanbanComment, KanbanLink, KanbanMove,
    KanbanScheduleAction, KanbanTaskCreate, KanbanTaskPatch,
)


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    operation: str
    body: type | None = None
    special: str | None = None
    tags: tuple[str, ...] = ("Brain4All",)


ROUTES = (
    Route("GET", "/api/v1/health", "health", tags=("System",)),
    Route("GET", "/agent-gateway/v1/ping", "health", tags=("System",)),
    Route("GET", "/api/v1/limits", "limits", tags=("System",)),
    Route("GET", "/api/v1/system/deployment", "deployment", tags=("System",)),
    Route("GET", "/agent-gateway/v1/agents", "agents_list", tags=("Agents",)),
    Route("POST", "/agent-gateway/v1/agents", "agents_create", AgentCreate, tags=("Agents",)),
    Route("GET", "/agent-gateway/v1/profiles", "profiles_list", tags=("Profiles",)),
    Route("GET", "/agent-gateway/v1/agents/{agent_id}/detail", "agents_get", tags=("Agents",)),
    Route("GET", "/agent-gateway/v1/agents/{agent_id}/runtime", "agents_get", tags=("Agents",)),
    Route("PATCH", "/agent-gateway/v1/agents/{agent_id}/metadata", "agents_metadata", AgentMetadataPatch, tags=("Agents",)),
    Route("DELETE", "/agent-gateway/v1/agents/{agent_id}/delete", "agents_delete", tags=("Agents",)),
    Route("POST", "/agent-gateway/v1/agents/{agent_id}/test", "agents_test", tags=("Agents",)),

    Route("GET", "/agent-gateway/v1/agents-configs/global", "config_global_get", tags=("Config",)),
    Route("PATCH", "/agent-gateway/v1/agents-configs/global", "config_global_patch", ConfigPatch, tags=("Config",)),
    Route("PATCH", "/agent-gateway/v1/agents-configs/{agent_id}", "config_agent_patch", ConfigPatch, tags=("Config",)),
    Route("GET", "/agent-gateway/v1/agents-skills", "skills_default_list", tags=("Skills",)),
    Route("POST", "/agent-gateway/v1/agents-skills", "skills_default_install", SkillInstall, tags=("Skills",)),
    Route("GET", "/agent-gateway/v1/agents-skills/{agent_id}", "skills_list", tags=("Skills",)),
    Route("POST", "/agent-gateway/v1/agents-skills/{agent_id}", "skills_install", SkillInstall, tags=("Skills",)),
    Route("PATCH", "/agent-gateway/v1/agents-skills/{agent_id}/{skill_id}", "skills_patch", EnabledPatch, tags=("Skills",)),
    Route("DELETE", "/agent-gateway/v1/agents-skills/{agent_id}/{skill_id}", "skills_delete", tags=("Skills",)),
    Route("GET", "/agent-gateway/v1/agents/{agent_id}/memory", "memory_get", tags=("Memory",)),
    Route("PATCH", "/agent-gateway/v1/agents/{agent_id}/memory", "memory_patch", MemoryPatch, tags=("Memory",)),
    Route("GET", "/agent-gateway/v1/agents/{agent_id}/snapshots", "snapshots_list", tags=("Snapshots",)),
    Route("POST", "/agent-gateway/v1/agents/{agent_id}/snapshots/{snapshot_id}/restore", "snapshots_restore", tags=("Snapshots",)),

    Route("GET", "/agent-gateway/v1/agents-workspaces/{agent_id}", "workspace_list", tags=("Workspace",)),
    Route("GET", "/agent-gateway/v1/agents-workspaces/{agent_id}/file", "workspace_view", tags=("Workspace",)),
    Route("POST", "/agent-gateway/v1/agents-workspaces/{agent_id}/read", "workspace_read", WorkspacePath, tags=("Workspace",)),
    Route("POST", "/agent-gateway/v1/agents-workspaces/{agent_id}/write", "workspace_write", WorkspaceWrite, tags=("Workspace",)),
    Route("POST", "/agent-gateway/v1/agents-workspaces/{agent_id}/create", "workspace_create", WorkspaceCreate, tags=("Workspace",)),
    Route("POST", "/agent-gateway/v1/agents-workspaces/{agent_id}/upload", "workspace_upload", special="workspace_upload", tags=("Workspace",)),
    Route("POST", "/agent-gateway/v1/agents-workspaces/{agent_id}/delete", "workspace_delete", WorkspacePath, tags=("Workspace",)),
    Route("GET", "/agent-gateway/v1/agents-mcp/{agent_id}", "mcp_get", tags=("MCP",)),
    Route("PUT", "/agent-gateway/v1/agents-mcp/{agent_id}", "mcp_put", MCPConfig, tags=("MCP",)),

    Route("GET", "/conversations/v1/conversations", "conversations_list", tags=("Conversations",)),
    Route("POST", "/conversations/v1/conversations", "conversations_create", ConversationCreate, tags=("Conversations",)),
    Route("GET", "/conversations/v1/conversations/{conversation_id}/detail", "conversations_get", tags=("Conversations",)),
    Route("GET", "/conversations/v1/conversations/{conversation_id}/messages", "messages_list", tags=("Conversations",)),
    Route("GET", "/conversations/v1/conversations/{conversation_id}/usage", "conversations_usage", tags=("Conversations",)),
    Route("PATCH", "/conversations/v1/conversations/{conversation_id}/name", "conversations_rename", ConversationRename, tags=("Conversations",)),
    Route("DELETE", "/conversations/v1/conversations/{conversation_id}/delete", "conversations_delete", tags=("Conversations",)),
    Route("POST", "/conversations/v1/conversations/{conversation_id}/chat/stream", "conversations_stream", ChatRequest, "stream", ("Runs",)),
    Route("POST", "/conversations/v1/conversations/{conversation_id}/runs/{run_id}/stop", "run_stop", tags=("Runs",)),
    Route("POST", "/conversations/v1/conversations/{conversation_id}/runs/{run_id}/approval", "run_approval", RunApproval, tags=("Runs",)),

    Route("GET", "/agent-gateway/v1/cron/jobs", "cron_list", tags=("Cron",)),
    Route("POST", "/agent-gateway/v1/cron/jobs", "cron_create", CronCreate, tags=("Cron",)),
    Route("POST", "/agent-gateway/v1/cron/jobs/{job_id}/pause", "cron_pause", tags=("Cron",)),
    Route("POST", "/agent-gateway/v1/cron/jobs/{job_id}/resume", "cron_resume", tags=("Cron",)),
    Route("POST", "/agent-gateway/v1/cron/jobs/{job_id}/run", "cron_run", tags=("Cron",)),
    Route("DELETE", "/agent-gateway/v1/cron/jobs/{job_id}", "cron_delete", tags=("Cron",)),

    Route("GET", "/agent-gateway/v1/kanban/boards", "kanban_boards", tags=("Kanban",)),
    Route("POST", "/agent-gateway/v1/kanban/boards", "kanban_board_create", KanbanBoardCreate, tags=("Kanban",)),
    Route("GET", "/agent-gateway/v1/kanban/boards/{board_slug}", "kanban_board_get", tags=("Kanban",)),
    Route("PATCH", "/agent-gateway/v1/kanban/boards/{board_slug}", "kanban_board_patch", GenericObject, tags=("Kanban",)),
    Route("POST", "/agent-gateway/v1/kanban/boards/{board_slug}/select", "kanban_board_select", tags=("Kanban",)),
    Route("DELETE", "/agent-gateway/v1/kanban/boards/{board_slug}", "kanban_board_delete", tags=("Kanban",)),
    Route("GET", "/agent-gateway/v1/kanban/boards/{board_slug}/tasks", "kanban_tasks", tags=("Kanban",)),
    Route("POST", "/agent-gateway/v1/kanban/boards/{board_slug}/tasks", "kanban_task_create", KanbanTaskCreate, tags=("Kanban",)),
    Route("GET", "/agent-gateway/v1/kanban/boards/{board_slug}/tasks/{task_id}", "kanban_task_get", tags=("Kanban",)),
    Route("PATCH", "/agent-gateway/v1/kanban/boards/{board_slug}/tasks/{task_id}", "kanban_task_patch", KanbanTaskPatch, tags=("Kanban",)),
    Route("POST", "/agent-gateway/v1/kanban/boards/{board_slug}/tasks/{task_id}/move", "kanban_task_move", KanbanMove, tags=("Kanban",)),
    Route("POST", "/agent-gateway/v1/kanban/boards/{board_slug}/tasks/{task_id}/assign", "kanban_task_assign", KanbanAssign, tags=("Kanban",)),
    Route("POST", "/agent-gateway/v1/kanban/boards/{board_slug}/tasks/{task_id}/schedule", "kanban_task_schedule", KanbanScheduleAction, tags=("Kanban",)),
    Route("POST", "/agent-gateway/v1/kanban/boards/{board_slug}/tasks/{task_id}/archive", "kanban_task_archive", tags=("Kanban",)),
    Route("POST", "/agent-gateway/v1/kanban/boards/{board_slug}/tasks/{task_id}/team/cancel", "kanban_team_cancel", tags=("Kanban",)),
    Route("POST", "/agent-gateway/v1/kanban/boards/{board_slug}/tasks/{task_id}/unarchive", "kanban_task_unarchive", tags=("Kanban",)),
    Route("GET", "/agent-gateway/v1/kanban/boards/{board_slug}/tasks/{task_id}/comments", "kanban_comments", tags=("Kanban",)),
    Route("POST", "/agent-gateway/v1/kanban/boards/{board_slug}/tasks/{task_id}/comments", "kanban_comment_create", KanbanComment, tags=("Kanban",)),
    Route("POST", "/agent-gateway/v1/kanban/boards/{board_slug}/tasks/{task_id}/links", "kanban_link_create", KanbanLink, tags=("Kanban",)),
    Route("DELETE", "/agent-gateway/v1/kanban/boards/{board_slug}/tasks/{task_id}/links", "kanban_link_delete", KanbanLink, tags=("Kanban",)),
    Route("GET", "/agent-gateway/v1/kanban/boards/{board_slug}/tasks/{task_id}/events", "kanban_events", tags=("Kanban",)),
    Route("GET", "/agent-gateway/v1/kanban/boards/{board_slug}/events/stream", "kanban_event_stream", special="kanban_stream", tags=("Kanban",)),
    Route("GET", "/agent-gateway/v1/kanban/diagnostics", "kanban_diagnostics", tags=("Kanban",)),
    Route("GET", "/api/v1/notifications", "notifications", tags=("Cron",)),
    Route("POST", "/api/v1/notifications/{notification_id}/resolve", "notification_resolve", tags=("Cron",)),

    Route("GET", "/api/v1/teams", "teams_list", tags=("Teams",)),
    Route("GET", "/api/v1/teams/", "teams_list", tags=("Teams",)),
    Route("POST", "/api/v1/teams", "teams_create", TeamCreate, tags=("Teams",)),
    Route("POST", "/api/v1/teams/", "teams_create", TeamCreate, tags=("Teams",)),
    Route("GET", "/api/v1/teams/{team_id}", "teams_get", tags=("Teams",)),
    Route("PUT", "/api/v1/teams/{team_id}", "teams_update", TeamCreate, tags=("Teams",)),
    Route("DELETE", "/api/v1/teams/{team_id}", "teams_delete", tags=("Teams",)),
    Route("POST", "/api/v1/teams/{team_id}/run", "teams_run", TeamRun, tags=("Teams",)),
    Route("POST", "/api/v1/teams/{team_id}/runs", "team_runs_start", TeamRun, tags=("Teams",)),
    Route("GET", "/api/v1/teams/{team_id}/runs", "team_runs_list", tags=("Teams",)),
    Route("GET", "/api/v1/teams/{team_id}/runs/{run_id}", "team_runs_get", tags=("Teams",)),
    Route("DELETE", "/api/v1/teams/{team_id}/runs/{run_id}", "team_runs_delete", tags=("Teams",)),
    Route("POST", "/api/v1/teams/{team_id}/runs/{run_id}/cancel", "team_runs_cancel", tags=("Teams",)),
    Route("GET", "/api/v1/teams/{team_id}/runs/{run_id}/events", "team_run_event_stream", special="team_run_stream", tags=("Teams",)),

    Route("GET", "/agent-gateway/v1/providers", "providers", tags=("Providers",)),
    Route("POST", "/agent-gateway/v1/providers/{provider_id}/connect", "provider_connect_start", tags=("Providers",)),
    Route("GET", "/agent-gateway/v1/providers/{provider_id}/connect", "provider_connect_status", tags=("Providers",)),
    Route("PUT", "/agent-gateway/v1/providers/{provider_id}/connect", "provider_connect_submit", ProviderCredential, tags=("Providers",)),
    Route("PATCH", "/agent-gateway/v1/providers/{provider_id}/update", "provider_update", ProviderCredential, tags=("Providers",)),
    Route("POST", "/agent-gateway/v1/providers/{provider_id}/disconnect", "provider_disconnect", tags=("Providers",)),
    Route("POST", "/agent-gateway/v1/providers/{provider_id}/test", "provider_test", tags=("Providers",)),
    Route("GET", "/agent-gateway/v1/providers/{provider_id}/models", "provider_models", tags=("Providers",)),
    Route("GET", "/agent-gateway/v1/providers/{provider_id}/models/{model}/reasoning", "provider_reasoning", tags=("Providers",)),
    Route("GET", "/agent-gateway/v1/providers/{provider_id}/connections", "provider_connections_list", tags=("Providers",)),
    Route("POST", "/agent-gateway/v1/providers/{provider_id}/connections", "provider_connection_create", ConnectionCreate, tags=("Providers",)),
    Route("PATCH", "/agent-gateway/v1/providers/{provider_id}/connections/{connection_id}", "provider_connection_patch", ConnectionPatch, tags=("Providers",)),
    Route("POST", "/agent-gateway/v1/providers/{provider_id}/connections/{connection_id}/test", "provider_connection_test", tags=("Providers",)),
    Route("DELETE", "/agent-gateway/v1/providers/{provider_id}/connections/{connection_id}", "provider_connection_delete", tags=("Providers",)),
    Route("GET", "/agent-gateway/v1/providers/{provider_id}/connections/{connection_id}/usage", "provider_connection_usage", tags=("Providers",)),

    Route("GET", "/agent-gateway/v1/blends", "blends_list", tags=("Blends",)),
    Route("POST", "/agent-gateway/v1/blends", "blends_create", BlendCreate, tags=("Blends",)),
    Route("GET", "/agent-gateway/v1/blends/available-models", "blends_available_models", tags=("Blends",)),
    Route("PATCH", "/agent-gateway/v1/blends/{blend_id}", "blends_patch", BlendPatch, tags=("Blends",)),
    Route("DELETE", "/agent-gateway/v1/blends/{blend_id}", "blends_delete", tags=("Blends",)),

    Route("GET", "/agent-gateway/v1/analytics/agents", "analytics_agents", tags=("Analytics",)),
    Route("GET", "/agent-gateway/v1/analytics/usage", "analytics_usage", tags=("Analytics",)),
    Route("GET", "/agent-gateway/v1/analytics/overview", "analytics_overview", tags=("Analytics",)),
    Route("GET", "/agent-gateway/v1/analytics/models", "analytics_models", tags=("Analytics",)),
    Route("GET", "/agent-gateway/v1/analytics/timeseries", "analytics_timeseries", tags=("Analytics",)),
    Route("GET", "/agent-gateway/v1/analytics/agents/{agent_id}/usage", "analytics_agent_usage", tags=("Analytics",)),
    Route("GET", "/agent-gateway/v1/analytics/agents/{agent_id}/budget", "analytics_budget_get", tags=("Analytics",)),
    Route("PUT", "/agent-gateway/v1/analytics/agents/{agent_id}/budget", "analytics_budget_set", AgentBudgetPatch, tags=("Analytics",)),

    Route("GET", "/sandboxes/v1/me/sandboxes/detail/stream", "sandbox_detail_stream", special="sandbox_stream", tags=("Sandbox",)),
    Route("GET", "/sandboxes/v1/me/sandboxes/{action}", "sandbox", tags=("Sandbox",)),
    Route("POST", "/sandboxes/v1/me/sandboxes/setup", "sandbox_setup", special="sandbox_setup", tags=("Sandbox",)),
    Route("POST", "/api/v1/bundles/export", "bundle_export", BundleExport, "bundle_export", ("Portability",)),
    Route("POST", "/api/v1/bundles/inspect", "bundle_inspect", special="bundle_upload", tags=("Portability",)),
    Route("POST", "/api/v1/bundles/dry-run", "bundle_dry_run", special="bundle_upload", tags=("Portability",)),
    Route("POST", "/api/v1/bundles/apply", "bundle_apply", special="bundle_upload", tags=("Portability",)),
    Route("POST", "/api/v1/bundles/exports", "bundle_export_start", BundleExport, tags=("Portability",)),
    Route("GET", "/api/v1/bundles/exports/{transfer_id}/parts/{part_number}", "bundle_export_part", special="bundle_part", tags=("Portability",)),
    Route("DELETE", "/api/v1/bundles/exports/{transfer_id}", "bundle_export_delete", tags=("Portability",)),
    Route("POST", "/api/v1/bundles/uploads", "bundle_upload_start", BundleUploadStart, tags=("Portability",)),
    Route("PUT", "/api/v1/bundles/uploads/{transfer_id}/parts/{part_number}", "bundle_upload_part", special="bundle_part", tags=("Portability",)),
    Route("POST", "/api/v1/bundles/uploads/{transfer_id}/complete", "bundle_upload_complete", BundleUploadComplete, tags=("Portability",)),
    Route("POST", "/api/v1/bundles/uploads/{transfer_id}/apply", "bundle_upload_apply", BundleUploadApply, tags=("Portability",)),
    Route("DELETE", "/api/v1/bundles/uploads/{transfer_id}", "bundle_upload_delete", tags=("Portability",)),

)


def _endpoint(handlers: Any, route: Route):
    if route.special == "stream":
        async def endpoint(request: Request, body=Body(...)) -> Response:
            return await handlers.stream(request, body.model_dump(exclude_unset=True))
        endpoint.__annotations__["body"] = route.body
    elif route.special == "workspace_upload":
        async def endpoint(request: Request) -> Response:
            return await handlers.workspace_upload(request)
    elif route.special == "bundle_export":
        async def endpoint(request: Request, body=Body(...)) -> Response:
            return await handlers.bundle_export(request, body.model_dump(exclude_unset=True))
        endpoint.__annotations__["body"] = route.body
    elif route.special == "bundle_upload":
        async def endpoint(request: Request) -> Response:
            return await handlers.bundle_upload(request)
    elif route.special == "bundle_part":
        async def endpoint(request: Request) -> Response:
            return await handlers.bundle_part(request)
    elif route.special == "sandbox_setup":
        async def endpoint(request: Request) -> Response:
            return await handlers.sandbox_setup(request)
    elif route.special == "sandbox_stream":
        async def endpoint(request: Request) -> Response:
            return await handlers.sandbox_detail_stream(request)
    elif route.special == "kanban_stream":
        async def endpoint(request: Request) -> Response:
            return await handlers.kanban_event_stream(request)
    elif route.special == "team_run_stream":
        async def endpoint(request: Request) -> Response:
            return await handlers.team_run_event_stream(request)
    elif route.body is not None:
        async def endpoint(request: Request, body=Body(...)) -> Response:
            return await handlers.dispatch(request, body.model_dump(exclude_unset=True))
        endpoint.__annotations__["body"] = route.body
    else:
        async def endpoint(request: Request) -> Response:
            return await handlers.dispatch(request, {})
    endpoint.__name__ = route.operation
    endpoint.__doc__ = f"{route.method} {route.path}"
    return endpoint


def setup_routes(app: Any, handlers: Any) -> None:
    for route in ROUTES:
        raw_response = route.special in {"stream", "workspace_upload", "bundle_export", "bundle_upload", "bundle_part", "sandbox_setup", "sandbox_stream", "kanban_stream", "team_run_stream"}
        app.add_api_route(
            route.path,
            _endpoint(handlers, route),
            methods=[route.method],
            name=route.operation,
            tags=list(route.tags),
            response_model=None if raw_response else APIEnvelope,
        )

    # Hermes CLI serves its bundled SPA from a catch-all route. Compatibility
    # APIs must stay ahead of it in Starlette's ordered router.
    catch_all = [item for item in app.router.routes if getattr(item, "path", "") == "/{full_path:path}"]
    if catch_all:
        app.router.routes[:] = [item for item in app.router.routes if item not in catch_all] + catch_all
