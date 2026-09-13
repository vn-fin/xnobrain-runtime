"""Agent Maker blueprint operation handlers."""

from typing import Any, Callable

from ...trusted_context import from_request

Operation = tuple[Callable[[], Any], str, int]


def operations(handler: Any, request: Any, body: dict[str, Any]) -> dict[str, Operation]:
    path, query, service = request.path_params, request.query_params, handler.service
    trusted = from_request(request)

    def owner() -> str:
        value = str(query.get("agent") or "").strip()
        if not value:
            raise ValueError("agent is required")
        return value

    return {
        "agent_maker_launch": (
            lambda: service.launch_agent_maker(path["agent_id"], body, trusted),
            "agent maker workflow launched successfully",
            201,
        ),
        "agent_maker_launch_get": (
            lambda: service.get_agent_maker_launch(path["agent_id"], path["session_id"], trusted),
            "agent maker workflow retrieved successfully",
            200,
        ),
        "agent_blueprints_list": (
            lambda: service.list_agent_blueprints(owner()),
            "agent blueprints retrieved successfully",
            200,
        ),
        "agent_blueprints_create": (
            lambda: service.create_agent_blueprint(owner(), body, trusted),
            "agent blueprint created successfully",
            201,
        ),
        "agent_blueprints_get": (
            lambda: service.get_agent_blueprint(owner(), path["blueprint_id"]),
            "agent blueprint retrieved successfully",
            200,
        ),
        "agent_blueprints_patch": (
            lambda: service.patch_agent_blueprint(owner(), path["blueprint_id"], body),
            "agent blueprint updated successfully",
            200,
        ),
        "agent_blueprints_scaffold": (
            lambda: service.scaffold_agent_blueprint(
                owner(), path["blueprint_id"], body, trusted.subject
            ),
            "agent blueprint scaffolded successfully",
            200,
        ),
        "agent_blueprints_certify": (
            lambda: service.certify_agent_blueprint(owner(), path["blueprint_id"], body, trusted),
            "agent blueprint certification completed",
            200,
        ),
        "agent_blueprints_activate": (
            lambda: service.activate_agent_blueprint(
                owner(), path["blueprint_id"], body, trusted.subject
            ),
            "agent blueprint activated successfully",
            200,
        ),
        "agent_blueprints_rollback": (
            lambda: service.rollback_agent_blueprint(
                owner(), path["blueprint_id"], body, trusted.subject
            ),
            "agent blueprint activation rolled back successfully",
            200,
        ),
        "agent_blueprints_cancel": (
            lambda: service.cancel_agent_blueprint(
                owner(), path["blueprint_id"], body, trusted.subject
            ),
            "agent blueprint cancelled successfully",
            200,
        ),
        "agent_blueprints_approve": (
            lambda: service.approve_agent_blueprint(
                owner(), path["blueprint_id"], body, trusted.subject
            ),
            "agent blueprint approved successfully",
            200,
        ),
    }
