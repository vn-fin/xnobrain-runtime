"""Carry workspace activity across the supported native CLI process boundary."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from ..repositories.base import StoreError
from ..repositories.runtime_update_gate import ACTIVITY, WorkspaceActivity

ACTIVITY_FD = "XNOBRAIN_ACTIVITY_FD"


def data_root():
    root = os.getenv("DATA_DIR") or os.getenv("RUNTIME_DATA_DIR")
    if root:
        return Path(root)
    profile = os.getenv("HERMES_ROOT_PROFILE") or os.getenv("HERMES_HOME")
    return Path(profile or str(Path.home() / ".hermes")) / "xnobrain"


def cli_activity():
    root = data_root()
    raw = os.environ.get(ACTIVITY_FD)
    if not raw:
        return WorkspaceActivity(root)
    try:
        descriptor = int(raw)
        actual = os.fstat(descriptor)
        expected = (root / ACTIVITY).stat(follow_symlinks=False)
        if descriptor < 3 or (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise ValueError("invalid inherited activity")
        activity = WorkspaceActivity.__new__(WorkspaceActivity)
        activity.root = root
        activity.descriptor = os.dup(descriptor)
        activity._token = None
        return activity
    except (OSError, ValueError):
        raise StoreError(
            "Workspace activity unavailable", status=503, code="rebalance_activity_unavailable"
        ) from None


def main():
    if len(sys.argv) < 2:
        raise SystemExit(2)
    activity = cli_activity()
    os.set_inheritable(activity.descriptor, True)
    os.environ[ACTIVITY_FD] = str(activity.descriptor)
    os.execvpe(sys.argv[1], sys.argv[1:], os.environ)


if __name__ == "__main__":
    try:
        main()
    except StoreError:
        print("Workspace is undergoing maintenance; retry when ready.", file=sys.stderr)
        raise SystemExit(75) from None
