"""Feature-owned operation handlers."""

import time
from typing import Any, Callable

from ..query import bucket, csv, time_range

Operation = tuple[Callable[[], Any], str, int]


def operations(handler: Any, request: Any, body: dict[str, Any]) -> dict[str, Operation]:
    p, q, s = request.path_params, request.query_params, handler.service
    agent = lambda: str(q.get("agent") or "").strip() or (_ for _ in ()).throw(ValueError("agent is required"))
    return {
        "teams_list": (s.list_teams, "teams retrieved successfully", 200),
        "teams_create": (lambda: s.create_team(body), "team created successfully", 201),
        "teams_get": (lambda: s.get_team(p["team_id"]), "team retrieved successfully", 200),
        "teams_update": (lambda: s.update_team(p["team_id"], body), "team updated successfully", 200),
        "teams_delete": (lambda: s.delete_team(p["team_id"]), "team deleted successfully", 200),
        "teams_run": (lambda: s.run_team(p["team_id"], body), "team run completed successfully", 200),
        "team_runs_start": (lambda: s.team_runs.start_run(p["team_id"], body), "team run started", 202),
        "team_runs_list": (lambda: s.team_runs.list_runs(p["team_id"], q.get("limit")), "team runs retrieved successfully", 200),
        "team_runs_get": (lambda: s.team_runs.get_run(p["team_id"], p["run_id"]), "team run retrieved successfully", 200),
        "team_runs_delete": (lambda: s.team_runs.delete_run(p["team_id"], p["run_id"]), "team run deleted", 200),
        "team_runs_cancel": (lambda: s.team_runs.cancel_run(p["team_id"], p["run_id"]), "team run cancelled", 200),
    }
