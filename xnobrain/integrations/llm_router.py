"""Composition facade for the centralized LLM router client."""

from __future__ import annotations

import os
from pathlib import Path

from .blends import BlendsIntegrationMixin
from .conversation_titles import ConversationTitlesMixin
from .llm_router_support import *  # noqa: F401,F403
from .llm_router_support import LLM_ROUTER_BASE_URL
from .llm_router_transport import LLMRouterTransportMixin
from .provider_models import ProviderModelsMixin


class LLMRouterClient(
    ProviderModelsMixin,
    BlendsIntegrationMixin,
    ConversationTitlesMixin,
    LLMRouterTransportMixin,
):
    def __init__(
        self,
        *,
        base_url: str | None = None,
        data_dir: str | Path | None = None,
    ):
        self.base_url = str(
            base_url
            or os.environ.get("RUNTIME_LLM_ROUTER_URL")
            or LLM_ROUTER_BASE_URL
        ).rstrip("/")
        # Provider credentials and router state remain centralized. This local
        # directory contains only Runtime-owned blend definitions/counters.
        self.data_dir = Path(data_dir or os.environ.get("DATA_DIR") or "/opt/data/xnobrain")
