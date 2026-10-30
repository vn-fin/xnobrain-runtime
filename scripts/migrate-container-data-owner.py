#!/usr/bin/env python3
"""Offline ownership migration for trusted legacy managed containers.

Run with system Python -I -S after stopping Runtime and backing up its data.
Never execute this against an active or untrusted workload.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import pwd
import stat
import subprocess
from pathlib import Path

ANCHOR = Path("/opt/data")


def migrate() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline-data-backed-up", action="store_true", required=True)
    parser.parse_args()
    if os.geteuid() != 0:
        raise ValueError("root required")
    for property_name, permitted in (
        ("ActiveState", {"inactive", "failed"}),
        ("MainPID", {"0"}),
        ("ControlPID", {"0"}),
    ):
        status = subprocess.run(
            [
                "/usr/bin/systemctl",
                "show",
                "xnobrain-runtime.service",
                "--value",
                f"--property={property_name}",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        if status.stdout.strip() not in permitted:
            raise ValueError("stop Runtime first")
    account = pwd.getpwnam("xnobrain")
    if account.pw_uid == 0 or account.pw_gid == 0:
        raise ValueError("non-root account required")
    for path in Path("/proc").glob("[0-9]*/status"):
        try:
            rows = path.read_text().splitlines()
        except FileNotFoundError:
            continue
        if any(row.startswith("Uid:") and str(account.pw_uid) in row.split()[1:] for row in rows):
            raise ValueError("workload identity still has running processes")
    specification = importlib.util.spec_from_file_location(
        "prepare_container", "/usr/local/libexec/xnobrain-prepare-container.py"
    )
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    module.verify_data(module.read_environment())
    for row in Path("/proc/self/mountinfo").read_text().splitlines():
        if row.split()[4].startswith("/opt/data/"):
            raise ValueError("nested mount in data volume")
    device = ANCHOR.stat().st_dev
    # Complete validation before making changes; the workload must stay offline.
    for _root, directories, files, descriptor in os.fwalk(ANCHOR, follow_symlinks=False):
        for name in [".", *directories, *files]:
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if info.st_dev != device or not (
                stat.S_ISREG(info.st_mode)
                or stat.S_ISDIR(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
            ):
                raise ValueError("unsupported data object")
            if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                raise ValueError("hardlinked file requires operator review")
    changed = 0
    for root, directories, files, descriptor in os.fwalk(ANCHOR, follow_symlinks=False):
        for name in [".", *directories, *files]:
            if root == str(ANCHOR) and name == ".xnobrain-volume.json":
                continue
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if info.st_uid == 0:
                os.chown(
                    name,
                    account.pw_uid,
                    account.pw_gid,
                    dir_fd=descriptor,
                    follow_symlinks=False,
                )
                changed += 1
    print(f"Migrated {changed} root-owned data entries; contents preserved")


if __name__ == "__main__":
    try:
        migrate()
    except (OSError, ValueError, KeyError):
        raise SystemExit("Offline data ownership migration failed") from None
