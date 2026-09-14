"""Validate an explicitly required local workspace mount before allocating data.

Mount topology is an operator-owned contract, not permission from a browser. This
checks Linux mount identity/containment; it does not certify hardware durability.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .base import StoreError

_LOCAL_FILESYSTEMS = {"ext2", "ext3", "ext4", "xfs", "btrfs", "zfs", "f2fs"}


def unavailable():
    raise StoreError(
        "required workspace storage is unavailable",
        status=503,
        code="custom_page_storage_unavailable",
    )


def mount_table():
    try:
        with Path("/proc/self/mountinfo").open() as source:
            raw = source.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            unavailable()
        rows = []
        for line in raw.splitlines():
            before, after = line.split(" - ", 1)
            fields, filesystem = before.split(), after.split()
            decoded = re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), fields[4])
            rows.append(
                (int(fields[0]), fields[2], Path(decoded), filesystem[0], fields[5].split(","))
            )
        return rows
    except (OSError, ValueError, IndexError):
        unavailable()


def no_symlink(path: Path):
    for item in (path, *path.parents):
        if item.is_symlink():
            unavailable()


class StorageMountGuard:
    def __init__(self, data: str | Path):
        self.data = Path(data).absolute()
        configured = os.getenv("RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT", "").strip()
        self.anchor = Path(configured) if configured else None
        self.identity = None
        self.check()

    def check(self):
        if self.anchor is None:
            return
        anchor = self.anchor
        if not anchor.is_absolute() or ".." in anchor.parts or ".." in self.data.parts:
            unavailable()
        no_symlink(anchor)
        no_symlink(self.data)
        if not anchor.is_dir() or not self.data.is_relative_to(anchor):
            unavailable()
        rows = mount_table()
        covered = [row for row in rows if self.data.is_relative_to(row[2])]
        mounted = max(covered, key=lambda row: len(row[2].parts), default=None)
        if (
            mounted is None
            or mounted[2] != anchor
            or mounted[3] not in _LOCAL_FILESYSTEMS
            or "rw" not in mounted[4]
        ):
            unavailable()
        # Neither apps, creation receipts nor update journals may hide on
        # another mount beneath the promised durable data directory.
        if any(row[2] != anchor and row[2].is_relative_to(self.data) for row in rows):
            unavailable()
        identity = mounted[:4]
        if self.identity is not None and identity != self.identity:
            unavailable()
        self.identity = identity
