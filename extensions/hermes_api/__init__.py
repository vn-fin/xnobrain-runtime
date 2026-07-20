"""OSS-owned Hermes API server extension package."""

from __future__ import annotations


def install() -> None:
    """Install the API adapter extension into the current Hermes process."""
    from .extension import install as _install

    _install()

__all__ = ["install"]
