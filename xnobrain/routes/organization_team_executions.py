"""Private execution routes; all methods require a signed Control request."""

from ..models.organization_team_executions import OrganizationTeamExecutionStart
from .definition import route

_PATH = "/organization-team-executions/{organization_id}/{team_id}/{run_id}"
ROUTES = (
    route("POST", _PATH, "organization_team_execution", OrganizationTeamExecutionStart),
    route("GET", _PATH, "organization_team_execution"),
    route("DELETE", _PATH, "organization_team_execution"),
)
