"""HTTP operation bindings for per-agent extensions."""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from ...trusted_context import from_request

Operation = tuple[Callable[[], Any], str, int]


def operations(handler: Any, request: Any, body: dict[str, Any]) -> dict[str, Operation]:
    path, service = request.path_params, handler.service.extensions
    context = lambda: from_request(request)
    entries = {
        "extensions_catalog": (service.catalog, "extension catalog retrieved", 200),
        "extensions_plugins_list": (
            lambda: service.list_plugins(path["agent_id"], context()),
            "extensions retrieved",
            200,
        ),
        "extensions_catalog_install": (
            lambda: service.install_catalog(path["agent_id"], body, context()),
            "catalog integration configured",
            200,
        ),
        "extensions_plugin_approval": (
            lambda: service.approve_plugin(path["agent_id"], path["plugin_id"], body, context()),
            "plugin approval updated",
            200,
        ),
        "extensions_plugin_enable": (
            lambda: service.set_plugin_enabled(
                path["agent_id"], path["plugin_id"], True, context()
            ),
            "plugin enabled",
            200,
        ),
        "extensions_plugin_disable": (
            lambda: service.set_plugin_enabled(
                path["agent_id"], path["plugin_id"], False, context()
            ),
            "plugin disabled",
            200,
        ),
        "extensions_plugin_visibility": (
            lambda: service.set_plugin_visibility(
                path["agent_id"], path["plugin_id"], body, context()
            ),
            "plugin visibility updated",
            200,
        ),
        "extensions_plugin_probe": (
            lambda: service.create_operation(
                path["agent_id"],
                {"kind": "probe", "plugin_id": path["plugin_id"], **body},
                context(),
            ),
            "extension probe accepted",
            202,
        ),
        "extensions_plugin_disconnect": (
            lambda: service.disconnect_catalog(path["agent_id"], path["plugin_id"], context()),
            "extension disconnected",
            200,
        ),
        "extensions_operation_create": (
            lambda: service.create_operation(path["agent_id"], body, context()),
            "extension operation accepted",
            202,
        ),
        "extensions_operations_list": (
            lambda: service.list_operations(path["agent_id"], context()),
            "extension operations retrieved",
            200,
        ),
        "extensions_operation_get": (
            lambda: service.get_operation(path["agent_id"], path["operation_id"], context()),
            "extension operation retrieved",
            200,
        ),
        "extensions_operation_cancel": (
            lambda: service.cancel_operation(path["agent_id"], path["operation_id"], context()),
            "extension operation cancellation requested",
            200,
        ),
        "extensions_tools_list": (
            lambda: service.list_toolsets(path["agent_id"], context()),
            "toolsets retrieved",
            200,
        ),
        "extensions_tools_patch": (
            lambda: service.set_toolset(path["agent_id"], path["toolset"], body, context()),
            "toolset updated",
            200,
        ),
        "extensions_hooks_list": (
            lambda: service.list_hooks(path["agent_id"], context()),
            "hooks retrieved",
            200,
        ),
        "extensions_hook_create": (
            lambda: service.create_hook(path["agent_id"], body, context()),
            "hook created pending approval",
            201,
        ),
        "extensions_hook_approval": (
            lambda: service.approve_hook(path["agent_id"], path["hook_id"], body, context()),
            "hook approval updated",
            200,
        ),
        "extensions_hook_enable": (
            lambda: service.set_hook_enabled(path["agent_id"], path["hook_id"], True, context()),
            "hook enabled",
            200,
        ),
        "extensions_hook_disable": (
            lambda: service.set_hook_enabled(path["agent_id"], path["hook_id"], False, context()),
            "hook disabled",
            200,
        ),
        "extensions_hook_delete": (
            lambda: service.delete_hook(path["agent_id"], path["hook_id"], context()),
            "hook deleted",
            200,
        ),
    }

    async def in_worker_thread(operation: Callable[[], Any]) -> Any:
        return await asyncio.to_thread(operation)

    return {
        name: (lambda operation=operation: in_worker_thread(operation), message, status)
        for name, (operation, message, status) in entries.items()
    }
