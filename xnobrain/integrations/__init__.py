"""Adapters for Hermes and the local OmniRoute provider runtime."""

from .config import ConfigAPIError, GlobalConfigManager
from .hermes import AgentAPIError, AgentManager
from .omniroute import OmniRouteAPIError, OmniRouteManager
from .runtime import LocalRuntimeManager
from .cron_delivery import (
    CronBlueprintInvalid,
    CronBlueprintNotFound,
    CronDeliveryAdapter,
    CronDeliveryAdapterError,
)

__all__ = [
    "AgentAPIError",
    "AgentManager",
    "ConfigAPIError",
    "GlobalConfigManager",
    "OmniRouteAPIError",
    "OmniRouteManager",
    "NineRouterAPIError",
    "NineRouterManager",
    "LocalRuntimeManager",
    "CronBlueprintInvalid",
    "CronBlueprintNotFound",
    "CronDeliveryAdapter",
    "CronDeliveryAdapterError",
]

# Compatibility aliases for extensions upgrading from pre-OmniRoute images.
NineRouterAPIError = OmniRouteAPIError
NineRouterManager = OmniRouteManager
