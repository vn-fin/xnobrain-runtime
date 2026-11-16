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
        "channels_social_tools_set": (
            lambda: service.set_social_tools(
                path["agent_id"], bool(body["enabled"]), trusted
            ),
            "channel social tools updated successfully",
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
        "channels_discord_update": (
            lambda: service.set_discord_enabled(path["agent_id"], bool(body["enabled"]), trusted),
            "channel updated successfully",
            200,
        ),
        "channels_discord_patch": (
            lambda: service.patch_discord(path["agent_id"], body, trusted),
            "channel updated successfully",
            200,
        ),
        "channels_discord_logout": (
            lambda: service.logout_discord(path["agent_id"], trusted),
            "channel logged out successfully",
            200,
        ),
        "channels_slack_update": (
            lambda: service.set_slack_enabled(path["agent_id"], bool(body["enabled"]), trusted),
            "channel updated successfully",
            200,
        ),
        "channels_slack_patch": (
            lambda: service.patch_slack(path["agent_id"], body, trusted),
            "channel updated successfully",
            200,
        ),
        "channels_slack_logout": (
            lambda: service.logout_slack(path["agent_id"], trusted),
            "channel logged out successfully",
            200,
        ),
        "channels_whatsapp_update": (
            lambda: service.set_whatsapp_enabled(path["agent_id"], bool(body["enabled"]), trusted),
            "channel updated successfully",
            200,
        ),
        "channels_whatsapp_patch": (
            lambda: service.patch_whatsapp(path["agent_id"], body, trusted),
            "channel updated successfully",
            200,
        ),
        "channels_whatsapp_qr": (
            lambda: service.channel_qr_whatsapp(path["agent_id"]),
            "channel QR retrieved successfully",
            200,
        ),
        "channels_whatsapp_logout": (
            lambda: service.logout_whatsapp(path["agent_id"], trusted),
            "channel logged out successfully",
            200,
        ),
        "channels_email_update": (
            lambda: service.set_platform_channel_enabled(
                path["agent_id"], bool(body["enabled"]), trusted, "email"
            ),
            "channel updated successfully",
            200,
        ),
        "channels_email_patch": (
            lambda: service.patch_platform_channel(
                path["agent_id"], body, trusted, "email"
            ),
            "channel updated successfully",
            200,
        ),
        "channels_email_logout": (
            lambda: service.logout_platform_channel(
                path["agent_id"], trusted, "email"
            ),
            "channel logged out successfully",
            200,
        ),
        "channels_mattermost_update": (
            lambda: service.set_platform_channel_enabled(
                path["agent_id"], bool(body["enabled"]), trusted, "mattermost"
            ),
            "channel updated successfully",
            200,
        ),
        "channels_mattermost_patch": (
            lambda: service.patch_platform_channel(
                path["agent_id"], body, trusted, "mattermost"
            ),
            "channel updated successfully",
            200,
        ),
        "channels_mattermost_logout": (
            lambda: service.logout_platform_channel(
                path["agent_id"], trusted, "mattermost"
            ),
            "channel logged out successfully",
            200,
        ),
        "channels_irc_update": (
            lambda: service.set_platform_channel_enabled(
                path["agent_id"], bool(body["enabled"]), trusted, "irc"
            ),
            "channel updated successfully",
            200,
        ),
        "channels_irc_patch": (
            lambda: service.patch_platform_channel(
                path["agent_id"], body, trusted, "irc"
            ),
            "channel updated successfully",
            200,
        ),
        "channels_irc_logout": (
            lambda: service.logout_platform_channel(
                path["agent_id"], trusted, "irc"
            ),
            "channel logged out successfully",
            200,
        ),
        "channels_wecom_update": (
            lambda: service.set_platform_channel_enabled(
                path["agent_id"], bool(body["enabled"]), trusted, "wecom"
            ),
            "channel updated successfully",
            200,
        ),
        "channels_wecom_patch": (
            lambda: service.patch_platform_channel(
                path["agent_id"], body, trusted, "wecom"
            ),
            "channel updated successfully",
            200,
        ),
        "channels_wecom_logout": (
            lambda: service.logout_platform_channel(
                path["agent_id"], trusted, "wecom"
            ),
            "channel logged out successfully",
            200,
        ),
        "channels_qqbot_update": (
            lambda: service.set_platform_channel_enabled(
                path["agent_id"], bool(body["enabled"]), trusted, "qqbot"
            ),
            "channel updated successfully",
            200,
        ),
        "channels_qqbot_patch": (
            lambda: service.patch_platform_channel(
                path["agent_id"], body, trusted, "qqbot"
            ),
            "channel updated successfully",
            200,
        ),
        "channels_qqbot_logout": (
            lambda: service.logout_platform_channel(
                path["agent_id"], trusted, "qqbot"
            ),
            "channel logged out successfully",
            200,
        ),
        "channels_yuanbao_update": (
            lambda: service.set_platform_channel_enabled(
                path["agent_id"], bool(body["enabled"]), trusted, "yuanbao"
            ),
            "channel updated successfully",
            200,
        ),
        "channels_yuanbao_patch": (
            lambda: service.patch_platform_channel(
                path["agent_id"], body, trusted, "yuanbao"
            ),
            "channel updated successfully",
            200,
        ),
        "channels_yuanbao_logout": (
            lambda: service.logout_platform_channel(
                path["agent_id"], trusted, "yuanbao"
            ),
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
