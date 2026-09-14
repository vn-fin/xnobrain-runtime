"""Environment-owned release feature switches.

Feature switches default off and accept only explicit boolean-like values. They
control product exposure, never identity, authorization, or tenant ownership.
"""

from __future__ import annotations

import os

FEATURE_UI_CUSTOMIZATION = "UI_CUSTOMIZATION"
FEATURE_AGENT_CUSTOM_PAGE = "AGENT_CUSTOM_PAGE"

# Experimental features are the only exceptions to the enabled-by-default rule.
_DEFAULTS = {
    FEATURE_UI_CUSTOMIZATION: False,
    FEATURE_AGENT_CUSTOM_PAGE: False,
}


def enabled(feature: str) -> bool:
    """Resolve ``FT_ENABLE_<feature>``; new features default enabled."""
    value = os.getenv(f"FT_ENABLE_{feature}", "").strip().lower()
    if not value:
        return _DEFAULTS.get(feature, True)
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return False
