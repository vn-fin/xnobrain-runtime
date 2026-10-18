from typing import Any, Callable

Operation = tuple[Callable[[], Any], str, int]


def operations(handler: Any, request: Any, body: dict[str, Any]) -> dict[str, Operation]:
    p, connector = request.path_params, handler.service.organization_connector
    return {
        "organization_connections_list": (
            connector.connections,
            "organization connections retrieved",
            200,
        ),
        "organization_connection_create": (
            lambda: connector.connect(body),
            "organization connection requested",
            202,
        ),
        "organization_connection_delete": (
            lambda: connector.disconnect(p["organization_id"]),
            "organization connection removed",
            200,
        ),
    }
