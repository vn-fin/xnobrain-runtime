"""HTTP mapping only; identity, data, and lifecycle rules stay in the service."""

import asyncio

from ...trusted_context import from_request


def operations(handler, request, body):
    service = getattr(handler.service, "custom_page", None)
    path = request.path_params
    agent = path.get("agent_id")
    trusted = from_request(request)
    selected = {
        "custom_page_removal_recovery": (
            lambda: service.removal_recovery(agent, trusted),
            "removal recovery plan",
            200,
        ),
        "custom_page_recover_removal": (
            lambda: service.recover_removal(agent, body, trusted),
            "removal recovered; app retained",
            200,
        ),
        "custom_page_removal_check": (
            lambda: service.removal_check(agent, trusted),
            "removal check",
            200,
        ),
        "custom_page_retained": (
            lambda: service.retained(
                trusted,
                int(request.query_params.get("offset", 0)),
                int(request.query_params.get("limit", 50)),
            ),
            "retained pages",
            200,
        ),
        "custom_page_remove_agent": (
            lambda: service.remove_agent(agent, body, trusted),
            "agent removed; app retained",
            200,
        ),
        "custom_page_schedule_preview": (
            lambda: service.schedules.preview(agent, body, trusted),
            "schedule preview",
            200,
        ),
        "custom_page_schedule_create": (
            lambda: service.schedules.create(agent, body, trusted),
            "schedule approved",
            201,
        ),
        "custom_page_schedule_list": (
            lambda: service.schedules.list(agent, trusted),
            "app schedules",
            200,
        ),
        "custom_page_migration_plan": (
            lambda: service.migration_plan(agent, path.get("revision"), trusted),
            "migration plan",
            200,
        ),
        "custom_page_migrate": (
            lambda: service.migrate(agent, body, trusted),
            "migration applied",
            200,
        ),
        "custom_page_export": (lambda: service.export(agent, trusted), "private app export", 200),
        "custom_page_delete": (lambda: service.delete(agent, body, trusted), "app deleted", 200),
        "custom_page_capabilities": (
            lambda: service.capabilities(agent, trusted),
            "capabilities",
            200,
        ),
        "custom_page_get": (lambda: service.read(agent, trusted), "custom page", 200),
        "custom_page_prepare": (
            lambda: service.prepare(agent, body, trusted),
            "draft prepared",
            201,
        ),
        "custom_page_preview": (
            lambda: service.preview(agent, path.get("revision"), trusted),
            "preview",
            200,
        ),
        "custom_page_activate": (
            lambda: service.activate(agent, body, trusted),
            "page activated",
            200,
        ),
        "custom_page_restore": (
            lambda: service.activate(agent, body, trusted, restore=True),
            "page restored",
            200,
        ),
        "custom_page_query": (
            lambda: service.query(agent, path.get("query_id"), body, trusted),
            "stored records",
            200,
        ),
        "custom_page_write": (
            lambda: service.write(agent, path.get("dataset_id"), body, trusted),
            "records saved",
            200,
        ),
        "custom_page_activity": (lambda: service.activity(agent, trusted), "activity", 200),
        "custom_page_archive": (
            lambda: service.archive(agent, body, trusted),
            "page archived",
            200,
        ),
        "custom_page_cancel": (
            lambda: service.cancel(agent, path.get("revision"), trusted),
            "draft cancelled",
            200,
        ),
        "custom_page_backup": (lambda: service.backup(agent, trusted), "backup created", 200),
        "custom_page_attachment": (
            lambda: service.attachment(agent, body, trusted),
            "attachment saved",
            201,
        ),
        "custom_page_attachment_read": (
            lambda: service.read_attachment(agent, path.get("attachment_id"), trusted),
            "attachment",
            200,
        ),
    }

    # File/SQLite work must not block chat/SSE on the event loop. SQLite's
    # connection timeout/progress budget bounds abandoned worker reads too.
    result = {
        name: (lambda operation=operation: asyncio.to_thread(operation), message, status)
        for name, (operation, message, status) in selected.items()
    }

    async def action():
        from ...services.conversation_authority import public_run

        return public_run(await service.run_action(agent, path.get("action_id"), body, trusted))

    result["custom_page_action"] = (
        action,
        "action run accepted",
        202,
    )
    result["custom_page_schedule_stop"] = (
        lambda: service.schedules.stop(agent, path.get("schedule_id"), body, trusted),
        "schedule stopped",
        200,
    )
    return result
