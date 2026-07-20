#!/usr/bin/env python3
"""Entrypoint for the OSS-owned Hermes API extension package."""

from __future__ import annotations

try:
    # Sandboxes installs this package as <root>/extensions/hermes_api.
    from extensions.hermes_api import install
except ModuleNotFoundError:
    # Open Lumora copies the launcher and package into one runtime directory.
    from hermes_api import install

__all__ = ["install"]


def main() -> None:
    install()
    print("Hermes API extension installed")


if __name__ == "__main__":
    main()
