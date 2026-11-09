"""Plugin artifact authority HTTP-to-service translation."""

from typing import Any, Callable

from ...trusted_context import from_request

Operation = tuple[Callable[[], Any], str, int]


def operations(handler: Any, request: Any, body: dict[str, Any]) -> dict[str, Operation]:
    trusted = from_request(request)
    return {
        "plugin_artifact_authority": (
            lambda: handler.service.plugin_artifact.authority(body, trusted),
            "plugin artifact authority completed",
            200,
        ),
    }
