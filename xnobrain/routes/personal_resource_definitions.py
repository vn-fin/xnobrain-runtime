"""Private, body-bound Control snapshot route."""

from ..models.personal_resource_definitions import PersonalResourceDefinitionRequest
from ..models.teams import TeamCreate
from .definition import route

ROUTES = (
    route(
        "POST",
        "/personal-resource-definitions/{kind}/{source_id}",
        "personal_resource_definition",
        PersonalResourceDefinitionRequest,
    ),
    route("PUT", "/personal-agent-teams/{team_id}", "personal_agent_team_save", TeamCreate),
)
