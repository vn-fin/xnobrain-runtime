"""Public OmniRoute integration facade.

The legacy module remains as a compatibility shim for existing profile and
extension imports; new runtime code should import this module.
"""

from .nine_router import OmniRouteManager
from .nine_router_support import OmniRouteAPIError

__all__ = ["OmniRouteAPIError", "OmniRouteManager"]
