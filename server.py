#!/usr/bin/env python3
"""Root FastAPI entrypoint for the unified XNOBrain and Hermes server."""

from __future__ import annotations

import os
import sys

# Source mounts and hot reload start before an image environment can apply.
# Keep plugin/profile imports source-only from the first interpreter import.
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

from xnobrain.server import app, main  # noqa: E402

__all__ = ["app", "main"]


if __name__ == "__main__":
    main()
