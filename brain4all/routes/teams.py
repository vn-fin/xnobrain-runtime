"""Teams API route declarations."""

from ..models import TeamCreate, TeamRun
from .definition import route

ROUTES = (
    route("GET", "/teams", "teams_list", tags=("Teams",)),
    route("GET", "/teams/", "teams_list", tags=("Teams",)),
    route("POST", "/teams", "teams_create", TeamCreate, tags=("Teams",)),
    route("POST", "/teams/", "teams_create", TeamCreate, tags=("Teams",)),
    route("GET", "/teams/{team_id}", "teams_get", tags=("Teams",)),
    route("PUT", "/teams/{team_id}", "teams_update", TeamCreate, tags=("Teams",)),
    route("DELETE", "/teams/{team_id}", "teams_delete", tags=("Teams",)),
    route("POST", "/teams/{team_id}/run", "teams_run", TeamRun, tags=("Teams",)),
    route("POST", "/teams/{team_id}/runs", "team_runs_start", TeamRun, tags=("Teams",)),
    route("GET", "/teams/{team_id}/runs", "team_runs_list", tags=("Teams",)),
    route("GET", "/teams/{team_id}/runs/{run_id}", "team_runs_get", tags=("Teams",)),
    route("DELETE", "/teams/{team_id}/runs/{run_id}", "team_runs_delete", tags=("Teams",)),
    route("POST", "/teams/{team_id}/runs/{run_id}/cancel", "team_runs_cancel", tags=("Teams",)),
    route("GET", "/teams/{team_id}/runs/{run_id}/events", "team_run_event_stream", special="team_run_stream", tags=("Teams",)),
)
