from ..models.marketplace import (
    MarketplaceInstallRequest,
    MarketplaceUninstallRequest,
    MarketplaceUpdateRequest,
)
from .definition import route

ROUTES = (
    route(
        "POST",
        "/marketplace/install",
        "marketplace_install",
        MarketplaceInstallRequest,
        tags=("Marketplace",),
    ),
    route(
        "POST",
        "/marketplace/update",
        "marketplace_update",
        MarketplaceUpdateRequest,
        tags=("Marketplace",),
    ),
    route(
        "POST",
        "/marketplace/uninstall",
        "marketplace_uninstall",
        MarketplaceUninstallRequest,
        tags=("Marketplace",),
    ),
)
