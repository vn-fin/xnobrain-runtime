#!/usr/bin/env python3
"""Root FastAPI entrypoint for the unified XNOBrain and Hermes server."""

from __future__ import annotations

from xnobrain.server import app, main

__all__ = ["app", "main"]


if __name__ == "__main__":
    main()
