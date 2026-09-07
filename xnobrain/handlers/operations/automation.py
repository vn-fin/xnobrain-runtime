"""Feature-owned operation handlers."""

import asyncio
import time
from typing import Any, Callable

from ..query import bucket, csv, time_range

Operation = tuple[Callable[[], Any], str, int]


def _threaded(operation: Callable[[], Any]) -> Callable[[], Any]:
    """Keep synchronous Hermes/filesystem work off FastAPI's event loop."""

    async def run() -> Any:
        return await asyncio.to_thread(operation)

    return run


def operations(handler: Any, request: Any, body: dict[str, Any]) -> dict[str, Operation]:
    p, q, s = request.path_params, request.query_params, handler.service
    agent = lambda: (
        str(q.get("agent") or "").strip() or (_ for _ in ()).throw(ValueError("agent is required"))
    )
    return {
        "cron_list": (
            _threaded(lambda: s.list_crons(q.get("agent_id"))),
            "cron jobs retrieved successfully",
            200,
        ),
        "cron_get": (
            _threaded(lambda: s.get_job_detail(p["job_id"], q.get("agent_id"))),
            "cron job detail retrieved successfully",
            200,
        ),
        "cron_create": (
            _threaded(lambda: s.create_cron(body)),
            "cron job created successfully",
            201,
        ),
        "cron_pause": (
            _threaded(lambda: s.set_cron_enabled(p["job_id"], False, q.get("agent_id"))),
            "cron job paused",
            200,
        ),
        "cron_resume": (
            _threaded(lambda: s.set_cron_enabled(p["job_id"], True, q.get("agent_id"))),
            "cron job resumed",
            200,
        ),
        "cron_run": (
            _threaded(lambda: s.run_cron(p["job_id"], q.get("agent_id"))),
            "cron job triggered",
            200,
        ),
        "cron_delete": (
            _threaded(lambda: s.delete_cron(p["job_id"], q.get("agent_id"))),
            "cron job deleted",
            200,
        ),
        "cron_blueprints": (_threaded(s.list_cron_blueprints), "cron blueprints retrieved", 200),
        "cron_blueprint_instantiate": (
            _threaded(lambda: s.instantiate_cron_blueprint(body)),
            "cron job created from blueprint",
            201,
        ),
        "cron_delivery_targets": (
            _threaded(lambda: s.list_cron_delivery_targets(q.get("agent_id"))),
            "cron delivery targets retrieved",
            200,
        ),
        "cron_job_targets_list": (
            _threaded(lambda: s.list_cron_job_targets(p["job_id"], q.get("agent_id"))),
            "cron job delivery targets retrieved",
            200,
        ),
        "cron_job_target_add": (
            _threaded(lambda: s.add_cron_job_target(p["job_id"], body, q.get("agent_id"))),
            "cron delivery target added",
            201,
        ),
        "cron_job_target_remove": (
            _threaded(
                lambda: s.remove_cron_job_target(p["job_id"], p["target_id"], q.get("agent_id"))
            ),
            "cron delivery target removed",
            200,
        ),
        "cron_trigger": (
            _threaded(lambda: s.run_cron(p["job_id"], q.get("agent_id"))),
            "cron job triggered",
            200,
        ),
        "cron_runs": (
            _threaded(
                lambda: s.list_cron_runs(p["job_id"], int(q.get("limit") or 20), q.get("agent_id"))
            ),
            "cron runs retrieved",
            200,
        ),
        "notifications": (
            s.repository.list_notifications,
            "notifications retrieved successfully",
            200,
        ),
        "notification_resolve": (
            lambda: s.repository.resolve_notification(p["notification_id"]),
            "notification resolved successfully",
            200,
        ),
    }
