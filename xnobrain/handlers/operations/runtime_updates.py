"""Runtime update operation handlers."""

from typing import Any


async def checkpoint_receipt(service: Any, body: dict[str, Any], token: str | None):
    """Keep preservation evidence local; expose a bounded checkpoint receipt."""
    checkpoint = await service.checkpoint(body, update_token=token)
    manifest = checkpoint["manifest"]
    return {
        key: checkpoint[key]
        for key in ("checkpoint_id", "operation_id", "generation", "created_at")
    } | {
        "manifest": {
            key: manifest[key]
            for key in ("version", "algorithm", "digest", "file_count", "total_bytes")
            if key in manifest
        },
    }


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
            lambda: checkpoint_receipt(service, body, token),
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
        "runtime_update_abort_unchanged": (
            lambda: service.abort_unchanged(body, update_token=token),
            "Runtime update aborted unchanged",
            200,
        ),
        "runtime_update_restore_ready": (
            lambda: service.restore_ready(body, update_token=token),
            "Runtime source restoration verified",
            200,
        ),
        "runtime_update_resume": (
            lambda: service.resume(body, update_token=token),
            "Runtime dispatch resumed",
            200,
        ),
    }
