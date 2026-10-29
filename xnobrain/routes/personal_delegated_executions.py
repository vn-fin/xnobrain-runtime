"""Control-only execution lifecycle on the initiating personal workspace."""

from ..models.personal_delegated_executions import PersonalDelegatedExecutionStart
from .definition import route

_PATH = "/personal-delegated-executions/{team_id}/{run_id}"
ROUTES = (
    route("POST", _PATH, "personal_delegated_execution", PersonalDelegatedExecutionStart),
    route("GET", _PATH, "personal_delegated_execution"),
    route("DELETE", _PATH, "personal_delegated_execution"),
)
