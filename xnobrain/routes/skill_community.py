"""Private Control-admitted Skill delivery routes."""

from .definition import route

ROUTES = (
    route(
        "POST",
        "/skill-community/exports",
        "community_skill_export",
        special="community_skill_export",
        include_in_schema=False,
    ),
    route(
        "GET",
        "/marketplace/installations/{operation_id}/receipt",
        "community_legacy_receipt",
        special="community_skill",
        include_in_schema=False,
    ),
    route(
        "POST",
        "/skill-community/installations",
        "community_skill_install",
        special="community_skill",
        include_in_schema=False,
    ),
    route(
        "GET",
        "/skill-community/installations/{operation_id}/receipt",
        "community_skill_receipt",
        special="community_skill",
        include_in_schema=False,
    ),
)
