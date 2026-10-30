"""Adapt an offline, verified VM data copy to the managed container account.

Invoked only by the fenced gateway against protected image code. This helper
never loads profile configuration or executes user code. Control owns resource
identity and has already verified the new volume and archive comparison.
"""

from __future__ import annotations

import importlib.util
import os
import pwd
import stat
from pathlib import Path

ANCHOR = Path("/opt/data")
LEGACY_ANCHOR = Path("/srv/xnobrain-data")


def verify_mount(anchor: Path) -> None:
    source = Path(__file__).with_name("runtime_data_volume.py")
    spec = importlib.util.spec_from_file_location("volume_guard", source)
    if spec is None or spec.loader is None:
        raise ValueError("volume guard unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.verify_mount(anchor)


def adapt_copy(anchor: Path, legacy: Path, uid: int, gid: int) -> None:
    """Keep file bytes and symlinks; remap ownership without following links."""
    if uid <= 0 or gid <= 0:
        raise ValueError("non-root account required")
    verify_mount(anchor)
    if legacy.is_symlink():
        if os.readlink(legacy) != str(anchor):
            raise ValueError("legacy anchor conflict")
    elif legacy.exists():
        raise ValueError("legacy anchor conflict")
    else:
        legacy.symlink_to(anchor, target_is_directory=True)
    for directory, dirs, files in os.walk(anchor, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(directory) / name
            info = path.lstat()
            if not (
                stat.S_ISDIR(info.st_mode)
                or stat.S_ISREG(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
            ):
                raise ValueError("unsupported copied file")
            # Existing marker is permitted only on retry after gateway adoption.
            if directory == str(anchor) and name == ".xnobrain-volume.json":
                continue
            os.chown(path, uid, gid, follow_symlinks=False)
    os.chown(anchor, uid, gid, follow_symlinks=False)
    os.sync()


def main() -> None:
    if os.geteuid() != 0:
        raise PermissionError("root migration required")
    account = pwd.getpwnam("xnobrain")
    adapt_copy(ANCHOR, LEGACY_ANCHOR, account.pw_uid, account.pw_gid)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError):
        raise SystemExit("Container data migration failed") from None
