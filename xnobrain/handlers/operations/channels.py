"""Channel HTTP-to-service translation for agent channel routes."""

from typing import Any, Callable

Operation = tuple[Callable[[], Any], str, int]


def operations(handler: Any, request: Any, body: dict[str, Any]) -> dict[str, Operation]:
    service = handler.service.channels
    path = getattr(request, "path_params", {}) or {}
    token = request.headers.get("x-xnobrain-internal-token")
    return {
        "channels_list": (
            lambda: service.list_channels(path["agent_id"]),
            "channels retrieved successfully",
            200,
        ),
        "channels_zalo_update": (
            lambda: service.set_channel_enabled(path["agent_id"], bool(body["enabled"])),
            "channel updated successfully",
            200,
        ),
        "channels_zalo_patch": (
            lambda: service.patch_channel(path["agent_id"], body),
            "channel updated successfully",
            200,
        ),
        "channels_zalo_qr": (
            lambda: service.channel_qr(path["agent_id"]),
            "channel QR retrieved successfully",
            200,
        ),
        "channels_zalo_logout": (
            lambda: service.logout_channel(path["agent_id"]),
            "channel logged out successfully",
            200,
        ),
        "channels_zalo_bot_update": (
            lambda: service.set_zalo_bot_enabled(path["agent_id"], bool(body["enabled"])),
            "channel updated successfully",
            200,
        ),
        "channels_zalo_bot_patch": (
            lambda: service.patch_zalo_bot(path["agent_id"], body),
            "channel updated successfully",
            200,
        ),
        "channels_zalo_bot_logout": (
            lambda: service.logout_zalo_bot(path["agent_id"]),
            "channel logged out successfully",
            200,
        ),
        "channels_internal_message": (
            lambda: service.append_internal(body, token),
            "channel message appended",
            200,
        ),
        "channels_internal_turn": (
            lambda: service.run_internal_turn(body, token),
            "channel turn completed",
            200,
        ),
    }
