#!/usr/bin/env python3
"""Root FastAPI entrypoint for the unified Brain4All and Hermes server."""

from __future__ import annotations

from brain4all.server import app, main


__all__ = ["app", "main"]


if __name__ == "__main__":
    main()
