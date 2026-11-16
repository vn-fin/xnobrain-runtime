"""Channel HTTP-to-service translation for agent channel routes."""

from typing import Any, Callable

from ...trusted_context import from_request

Operation = tuple[Callable[[], Any], str, int]


def operations(handler: Any, request: Any, body: dict[str, Any]) -> dict[str, Operation]:
    service = handler.service.channels
    path = getattr(request, "path_params", {}) or {}
    token = request.headers.get("x-xnobrain-internal-token")
    trusted = from_request(request)
    return {
        "channels_list": (
            lambda: service.list_channels(path["agent_id"]),
            "channels retrieved successfully",
            200,
        ),
        "channels_zalo_update": (
            lambda: service.set_channel_enabled(path["agent_id"], bool(body["enabled"]), trusted),
            "channel updated successfully",
            200,
        ),
        "channels_zalo_patch": (
            lambda: service.patch_channel(path["agent_id"], body, trusted),
            "channel updated successfully",
            200,
        ),
        "channels_zalo_qr": (
            lambda: service.channel_qr(path["agent_id"]),
            "channel QR retrieved successfully",
            200,
        ),
        "channels_zalo_logout": (
            lambda: service.logout_channel(path["agent_id"], trusted),
            "channel logged out successfully",
            200,
        ),
        "channels_zalo_bot_update": (
            lambda: service.set_zalo_bot_enabled(path["agent_id"], bool(body["enabled"]), trusted),
            "channel updated successfully",
            200,
        ),
        "channels_zalo_bot_patch": (
            lambda: service.patch_zalo_bot(path["agent_id"], body, trusted),
            "channel updated successfully",
            200,
        ),
        "channels_zalo_bot_logout": (
            lambda: service.logout_zalo_bot(path["agent_id"], trusted),
            "channel logged out successfully",
            200,
        ),
        "channels_telegram_update": (
            lambda: service.set_telegram_enabled(path["agent_id"], bool(body["enabled"]), trusted),
            "channel updated successfully",
            200,
        ),
        "channels_telegram_patch": (
            lambda: service.patch_telegram(path["agent_id"], body, trusted),
            "channel updated successfully",
            200,
        ),
        "channels_telegram_logout": (
            lambda: service.logout_telegram(path["agent_id"], trusted),
            "channel logged out successfully",
            200,
        ),
        "channels_pairings_list": (
            lambda: service.list_pairings(path["agent_id"], path["platform"], trusted),
            "channel pairings retrieved successfully",
            200,
        ),
        "channels_pairings_approve": (
            lambda: service.approve_pairing(
                path["agent_id"], path["platform"], body["request_id"], trusted
            ),
            "pairing request approved",
            200,
        ),
        "channels_pairings_deny": (
            lambda: service.deny_pairing(
                path["agent_id"], path["platform"], body["request_id"], trusted
            ),
            "pairing request denied",
            200,
        ),
        "channels_pairings_revoke": (
            lambda: service.revoke_pairing(
                path["agent_id"], path["platform"], body["user_id"], trusted
            ),
            "pairing approval reviewed",
            200,
        ),
        "channels_groups_list": (
            lambda: service.list_groups(path["agent_id"], path["platform"], trusted),
            "channel groups retrieved successfully",
            200,
        ),
        "channels_groups_approve": (
            lambda: service.approve_group(
                path["agent_id"], path["platform"], body["group_id"], trusted
            ),
            "channel group approved",
            200,
        ),
        "channels_groups_deny": (
            lambda: service.deny_group(
                path["agent_id"], path["platform"], body["group_id"], trusted
            ),
            "channel group denied",
            200,
        ),
        "channels_groups_revoke": (
            lambda: service.revoke_group(
                path["agent_id"], path["platform"], body["group_id"], trusted
            ),
            "channel group approval reviewed",
            200,
        ),
        "channels_groups_settings": (
            lambda: service.update_group_settings(
                path["agent_id"], path["platform"], body["daily_limit"], trusted
            ),
            "channel group settings updated",
            200,
        ),
        "channels_internal_groups_seen": (
            lambda: service.record_group_seen(body, token),
            "channel group seen recorded",
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
