"""HTTP translation for skill optimization lifecycle operations."""

from typing import Any, Callable

from ...trusted_context import from_request

Operation = tuple[Callable[[], Any], str, int]


def operations(handler: Any, request: Any, body: dict[str, Any]) -> dict[str, Operation]:
    path = request.path_params
    service = handler.service.skill_optimizations
    trusted = from_request(request)
    agent_id = path.get("agent_id")
    operation_id = path.get("optimization_id")
    return {
        "skill_optimizations_create": (
            lambda: service.create(agent_id, body, trusted),
            "skill optimization created successfully",
            201,
        ),
        "skill_optimizations_get": (
            lambda: service.get(agent_id, operation_id, trusted),
            "skill optimization retrieved successfully",
            200,
        ),
        "skill_optimizations_evaluate": (
            lambda: service.evaluate(agent_id, operation_id, body, trusted),
            "skill optimization evaluated successfully",
            200,
        ),
        "skill_optimizations_approve": (
            lambda: service.approve(agent_id, operation_id, body, trusted),
            "skill optimization review recorded successfully",
            200,
        ),
        "skill_optimizations_apply": (
            lambda: service.apply(agent_id, operation_id, body, trusted),
            "skill optimization applied successfully",
            200,
        ),
        "skill_optimizations_cancel": (
            lambda: service.cancel(agent_id, operation_id, body, trusted),
            "skill optimization cancelled successfully",
            200,
        ),
        "skill_optimizations_rollback": (
            lambda: service.rollback(agent_id, operation_id, body, trusted),
            "skill optimization rolled back successfully",
            200,
        ),
    }
