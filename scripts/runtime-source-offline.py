"""Launch only offline update modules from this verified Runtime tree.

Run with the protected interpreter's -I -B flags as the non-root workload user.
The application's package initializers compose the server and engine; these
namespace packages let the offline helper import explicit modules without
loading those initializers or a user profile/plugin.
"""

from __future__ import annotations

import importlib
import runpy
import sys
import types
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    importlib.import_module("xnobrain")
    for name in ("integrations", "models", "repositories", "services"):
        qualified = f"xnobrain.{name}"
        package = types.ModuleType(qualified)
        package.__path__ = [str(root / "xnobrain" / name)]
        package.__package__ = qualified
        sys.modules[qualified] = package
    runpy.run_module("xnobrain.integrations.runtime_source_checkpoint", run_name="__main__")


if __name__ == "__main__":
    main()
