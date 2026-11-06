"""Agent channel routes: inbound ingestion and the Zalo product surface."""

from ..models.channels import (
    ChannelInboundMessage,
    ChannelInboundResult,
    ChannelListResult,
    ChannelQrResult,
    ChannelState,
    ChannelZaloPatch,
    ChannelZaloUpdate,
)
from .definition import route

ROUTES = (
    route(
        "GET",
        "/agents/{agent_id}/channels",
        "channels_list",
        tags=("Channels",),
        response_data=ChannelListResult,
    ),
    route(
        "PUT",
        "/agents/{agent_id}/channels/zalo",
        "channels_zalo_update",
        ChannelZaloUpdate,
        tags=("Channels",),
        response_data=ChannelState,
    ),
    route(
        "PATCH",
        "/agents/{agent_id}/channels/zalo",
        "channels_zalo_patch",
        ChannelZaloPatch,
        tags=("Channels",),
        response_data=ChannelState,
    ),
    route(
        "GET",
        "/agents/{agent_id}/channels/zalo/qr",
        "channels_zalo_qr",
        tags=("Channels",),
        response_data=ChannelQrResult,
    ),
    route(
        "POST",
        "/agents/{agent_id}/channels/zalo/logout",
        "channels_zalo_logout",
        tags=("Channels",),
        response_data=ChannelState,
    ),
    route(
        "POST",
        "/internal/channels/messages",
        "channels_internal_message",
        ChannelInboundMessage,
        tags=("Channels",),
        response_data=ChannelInboundResult,
    ),
)
