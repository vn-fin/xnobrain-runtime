"""Channel HTTP-to-service translation for the private gateway route."""

from typing import Any, Callable

Operation = tuple[Callable[[], Any], str, int]


def operations(handler: Any, request: Any, body: dict[str, Any]) -> dict[str, Operation]:
    service = handler.service.channels
    token = request.headers.get("x-xnobrain-internal-token")
    return {
        "channels_internal_message": (
            lambda: service.append_internal(body, token),
            "channel message appended",
            200,
        ),
    }
