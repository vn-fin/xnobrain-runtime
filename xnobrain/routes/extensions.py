"""Per-agent extension management API declarations."""

from ..models.extensions import (
    CatalogInstall,
    ExtensionApproval,
    ExtensionOperationCreate,
    HookApproval,
    HookCreate,
    PluginVisibility,
    ProbeCreate,
    ToolsetPatch,
)
from .definition import route

_TAG = ("Agent extensions",)

ROUTES = (
    route("GET", "/plugins/catalog", "extensions_catalog", tags=_TAG),
    route("GET", "/agents-plugins/{agent_id}", "extensions_plugins_list", tags=_TAG),
    route(
        "POST",
        "/agents-plugins/{agent_id}/install-catalog",
        "extensions_catalog_install",
        CatalogInstall,
        tags=_TAG,
    ),
    route(
        "POST",
        "/agents-plugins/{agent_id}/{plugin_id}/approval",
        "extensions_plugin_approval",
        ExtensionApproval,
        tags=_TAG,
    ),
    route(
        "POST",
        "/agents-plugins/{agent_id}/{plugin_id}/enable",
        "extensions_plugin_enable",
        tags=_TAG,
    ),
    route(
        "POST",
        "/agents-plugins/{agent_id}/{plugin_id}/disable",
        "extensions_plugin_disable",
        tags=_TAG,
    ),
    route(
        "PATCH",
        "/agents-plugins/{agent_id}/{plugin_id}/visibility",
        "extensions_plugin_visibility",
        PluginVisibility,
        tags=_TAG,
    ),
    route(
        "POST",
        "/agents-plugins/{agent_id}/{plugin_id}/probe",
        "extensions_plugin_probe",
        ProbeCreate,
        tags=_TAG,
    ),
    route(
        "POST",
        "/agents-plugins/{agent_id}/{plugin_id}/disconnect",
        "extensions_plugin_disconnect",
        tags=_TAG,
    ),
    route(
        "POST",
        "/agents-extension-operations/{agent_id}",
        "extensions_operation_create",
        ExtensionOperationCreate,
        tags=_TAG,
    ),
    route(
        "GET", "/agents-extension-operations/{agent_id}", "extensions_operations_list", tags=_TAG
    ),
    route(
        "GET",
        "/agents-extension-operations/{agent_id}/{operation_id}",
        "extensions_operation_get",
        tags=_TAG,
    ),
    route(
        "POST",
        "/agents-extension-operations/{agent_id}/{operation_id}/cancel",
        "extensions_operation_cancel",
        tags=_TAG,
    ),
    route("GET", "/agents-tools/{agent_id}", "extensions_tools_list", tags=_TAG),
    route(
        "PATCH",
        "/agents-tools/{agent_id}/{toolset}",
        "extensions_tools_patch",
        ToolsetPatch,
        tags=_TAG,
    ),
    route("GET", "/agents-hooks/{agent_id}", "extensions_hooks_list", tags=_TAG),
    route("POST", "/agents-hooks/{agent_id}", "extensions_hook_create", HookCreate, tags=_TAG),
    route(
        "POST",
        "/agents-hooks/{agent_id}/{hook_id}/approval",
        "extensions_hook_approval",
        HookApproval,
        tags=_TAG,
    ),
    route("POST", "/agents-hooks/{agent_id}/{hook_id}/enable", "extensions_hook_enable", tags=_TAG),
    route(
        "POST", "/agents-hooks/{agent_id}/{hook_id}/disable", "extensions_hook_disable", tags=_TAG
    ),
    route("DELETE", "/agents-hooks/{agent_id}/{hook_id}", "extensions_hook_delete", tags=_TAG),
)
