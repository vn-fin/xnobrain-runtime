"""Feature-owned operation handlers."""

import time
from typing import Any, Callable

from ..query import bucket, csv, time_range

Operation = tuple[Callable[[], Any], str, int]


def operations(handler: Any, request: Any, body: dict[str, Any]) -> dict[str, Operation]:
    p, q, s = request.path_params, request.query_params, handler.service
    agent = lambda: str(q.get("agent") or "").strip() or (_ for _ in ()).throw(ValueError("agent is required"))
    return {
        "cron_list": (s.list_crons, "cron jobs retrieved successfully", 200),
        "cron_get": (lambda: s.get_job_detail(p["job_id"]), "cron job detail retrieved successfully", 200),
        "cron_create": (lambda: s.create_cron(body), "cron job created successfully", 201),
        "cron_pause": (lambda: s.set_cron_enabled(p["job_id"], False), "cron job paused", 200),
        "cron_resume": (lambda: s.set_cron_enabled(p["job_id"], True), "cron job resumed", 200),
        "cron_run": (lambda: s.run_cron(p["job_id"]), "cron job triggered", 200),
        "cron_delete": (lambda: s.delete_cron(p["job_id"]), "cron job deleted", 200),
        "notifications": (s.repository.list_notifications, "notifications retrieved successfully", 200),
        "notification_resolve": (lambda: s.repository.resolve_notification(p["notification_id"]), "notification resolved successfully", 200),
    }
