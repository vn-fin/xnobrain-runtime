"""Runtime update operation handlers."""

from typing import Any


def operations(handler: Any, _request: Any, body: dict[str, Any]):
    service = handler.service.runtime_updates
    token = _request.headers.get("x-xnobrain-update-token")
    return {
        "runtime_update_preflight": (
            lambda: service.preflight(body, update_token=token),
            "update preflight complete",
            200,
        ),
        "runtime_update_drain": (
            lambda: service.drain(body, update_token=token),
            "Runtime drained",
            200,
        ),
        "runtime_update_checkpoint": (
            lambda: service.checkpoint(body, update_token=token),
            "update checkpoint created",
            200,
        ),
        "runtime_update_readiness": (
            lambda: service.readiness(body, update_token=token),
            "candidate Runtime is ready",
            200,
        ),
        "runtime_update_post_verify": (
            lambda: service.post_verify(body, update_token=token),
            "Runtime update verified",
            200,
        ),
        "runtime_update_recovery": (
            lambda: service.recover(body, update_token=token),
            "Runtime recovery state recorded",
            200,
        ),
        "runtime_update_resume": (
            lambda: service.resume(body, update_token=token),
            "Runtime dispatch resumed",
            200,
        ),
    }
