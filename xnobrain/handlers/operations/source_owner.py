"""Source-owner probe HTTP-to-service translation."""

from typing import Any, Callable

from ...trusted_context import from_request

Operation = tuple[Callable[[], Any], str, int]


def operations(handler: Any, request: Any, body: dict[str, Any]) -> dict[str, Operation]:
    trusted = from_request(request)
    return {
        "source_owner_probe": (
            lambda: handler.service.source_owner.probe(body, trusted),
            "source owner probe completed",
            200,
        ),
    }
