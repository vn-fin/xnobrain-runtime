"""Compatibility facade for the grouped OmniRoute integration modules."""

from __future__ import annotations

from .blends import BlendsIntegrationMixin
from .conversation_titles import ConversationTitlesMixin
from .nine_router_support import *  # noqa: F401,F403
from .nine_router_support import OMNIROUTE_BASE_URL, Path, os
from .nine_router_transport import NineRouterTransportMixin
from .provider_connections import ProviderConnectionsMixin
from .provider_models import ProviderModelsMixin
from .provider_usage import ProviderUsageMixin


class OmniRouteManager(
    ProviderConnectionsMixin,
    ProviderModelsMixin,
    ProviderUsageMixin,
    BlendsIntegrationMixin,
    ConversationTitlesMixin,
    NineRouterTransportMixin,
):
    def __init__(self, *, base_url: str | None = None, data_dir: str | Path | None = None):
        self.base_url = str(
            base_url
            or os.environ.get("OMNIROUTE_URL")
            or os.environ.get("NINE_ROUTER_URL")
            or OMNIROUTE_BASE_URL
        ).rstrip("/")
        self.data_dir = Path(
            data_dir
            or os.environ.get("OMNIROUTE_DATA_DIR")
            or os.environ.get("NINE_ROUTER_DATA_DIR")
            or Path.home() / ".omniroute"
        )


NineRouterManager = OmniRouteManager
