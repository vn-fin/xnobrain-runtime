"""FT0013 Time Control HTTP-to-service translation."""

import asyncio
from typing import Any, Callable

Operation = tuple[Callable[[], Any], str, int]


def _threaded(operation: Callable[[], Any]) -> Callable[[], Any]:
    async def run() -> Any:
        return await asyncio.to_thread(operation)

    return run


def operations(handler: Any, request: Any, body: dict[str, Any]) -> dict[str, Operation]:
    from ...trusted_context import from_request

    service = handler.service.time_control
    token = request.headers.get("x-xnobrain-time-token")
    trusted = from_request(request)
    workspace = body.get("workspace")

    def authorize_context() -> None:
        service.authorize_workspace_context(workspace, trusted)

    return {
        "time_control_observe": (
            _threaded(lambda: service.observe(time_token=token)),
            "Runtime Time Control observed",
            200,
        ),
        "time_control_schedule_migration_preview": (
            _threaded(
                lambda: (
                    authorize_context(),
                    service.preview(body, time_token=token),
                )[1]
            ),
            "schedule migration preview calculated",
            200,
        ),
        "time_control_apply": (
            lambda: (
                authorize_context(),
                service.apply(body, time_token=token),
            )[1],
            "Runtime timezone applied",
            200,
        ),
        "time_control_schedule_migrate": (
            lambda: (
                authorize_context(),
                service.migrate(body, time_token=token),
            )[1],
            "Runtime schedules migrated",
            200,
        ),
    }
