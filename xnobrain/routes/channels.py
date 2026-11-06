"""Private channel-ingestion routes for the bundled gateway plugin."""

from ..models.channels import ChannelInboundMessage, ChannelInboundResult
from .definition import route

ROUTES = (
    route(
        "POST",
        "/internal/channels/messages",
        "channels_internal_message",
        ChannelInboundMessage,
        tags=("Channels",),
        response_data=ChannelInboundResult,
    ),
)
