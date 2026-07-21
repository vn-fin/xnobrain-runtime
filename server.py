#!/usr/bin/env python3
"""Root FastAPI entrypoint for the unified Open Lumora and Hermes server."""

from __future__ import annotations

from open_lumora.server import app, main


__all__ = ["app", "main"]


if __name__ == "__main__":
    main()
