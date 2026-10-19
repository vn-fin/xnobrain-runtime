from ..models.organization_connections import ConnectOrganization
from .definition import route

ROUTES = (
    route(
        "GET",
        "/organization-connections",
        "organization_connections_list",
        tags=("Organization connections",),
    ),
    route(
        "POST",
        "/organization-connections",
        "organization_connection_create",
        ConnectOrganization,
        tags=("Organization connections",),
    ),
    route(
        "DELETE",
        "/organization-connections/{organization_id}",
        "organization_connection_delete",
        tags=("Organization connections",),
    ),
)
