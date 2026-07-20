"""Compatibility shim for the packaged Hermes API extension.

New installs should import :mod:`api_extension` or :mod:`extensions.hermes_api`.
This module remains so older launchers that import ``hermes_api_extension`` can
still start after receiving the updated package.
"""

from api_extension import install

__all__ = ["install"]
