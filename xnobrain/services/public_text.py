"""Safe product-facing text normalization for upstream runtime failures."""

from __future__ import annotations

import re

_UPSTREAM_BRAND = re.compile(r"\bHermes(?:\s+(?:Agent|CLI))?\b", re.IGNORECASE)
_PROVIDER_UNREACHABLE = re.compile(
    r"Agent can't reach the model provider\. You may be offline\. "
    r"Check your internet connection and try again\.",
    re.IGNORECASE,
)


def public_error_message(value: object, fallback: str = "Request failed") -> str:
    """Remove implementation branding and misleading network diagnosis."""

    message = str(value or "").strip() or fallback
    message = _UPSTREAM_BRAND.sub("Agent", message)
    return _PROVIDER_UNREACHABLE.sub(
        "Agent could not reach the model provider. The provider or router "
        "may be temporarily unavailable. Check the connection and try again.",
        message,
    )
