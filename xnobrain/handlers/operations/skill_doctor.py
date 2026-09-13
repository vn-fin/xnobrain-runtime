"""HTTP translation for Skill Doctor lifecycle operations."""

from typing import Any, Callable

from ...trusted_context import from_request

Operation = tuple[Callable[[], Any], str, int]


def operations(handler: Any, request: Any, body: dict[str, Any]) -> dict[str, Operation]:
    path = request.path_params
    service = handler.service.skill_doctor
    trusted = from_request(request)
    agent_id = path.get("agent_id")
    return {
        "skill_doctor_launch": (
            lambda: service.launch(agent_id, body, trusted),
            "skill doctor workflow launched successfully",
            201,
        ),
        "skill_doctor_launch_get": (
            lambda: service.get_launch(agent_id, path.get("session_id"), trusted),
            "skill doctor workflow retrieved successfully",
            200,
        ),
        "skill_doctor_reports_create": (
            lambda: service.create_report(agent_id, body, trusted),
            "skill doctor report created successfully",
            201,
        ),
        "skill_doctor_reports_get": (
            lambda: service.get_report(agent_id, path.get("report_id"), trusted),
            "skill doctor report retrieved successfully",
            200,
        ),
        "skill_doctor_plans_create": (
            lambda: service.create_plan(
                agent_id,
                path.get("report_id"),
                body,
                trusted,
            ),
            "skill doctor plan created successfully",
            201,
        ),
        "skill_doctor_plans_apply": (
            lambda: service.apply(agent_id, path.get("plan_id"), body, trusted),
            "skill doctor plan applied successfully",
            200,
        ),
        "skill_doctor_operations_rollback": (
            lambda: service.rollback(agent_id, path.get("plan_id"), body, trusted),
            "skill doctor operation rolled back successfully",
            200,
        ),
    }
