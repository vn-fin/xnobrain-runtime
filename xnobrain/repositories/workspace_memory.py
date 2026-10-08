"""Cross-process idle history, serialized by the existing admission gate.

The stable guard inode invalidates old JSON before every activity transition.
Its shared mapping remains observable when an atomic JSON write fails. It is
not an idle clock or a stored active-work counter. Guest boot identity makes
its persistence across a machine restart irrelevant.
"""

from __future__ import annotations

import json
import mmap
import os
import stat
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .base import StoreError

DIRECTORY = "runtime-memory"
ACTIVITY = "activity.json"
GUARD = "activity.guard"
SCHEMA_VERSION = 1


def unavailable():
    raise StoreError(
        "Workspace activity tracking is unavailable",
        status=503,
        code="memory_activity_unavailable",
    )


def guest_boot_id() -> str:
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


class WorkspaceMemoryRepository:
    def __init__(self, root: Path, workspace_id: str, *, clock=None, boot_id=None):
        self.root = root
        self.directory = root / DIRECTORY
        self.path = self.directory / ACTIVITY
        self.workspace_id = workspace_id
        self.clock = clock or time.monotonic_ns
        self.boot_id = boot_id or guest_boot_id

    @contextmanager
    def guard(self, *, create=False):
        """Never replace this inode: every cooperating process maps the same bytes."""
        if create:
            self.directory.mkdir(mode=0o700, exist_ok=True)
        if self.directory.is_symlink() or not self.directory.is_dir():
            unavailable()
        flags = os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW
        if create:
            flags |= os.O_CREAT
        descriptor = os.open(self.directory / GUARD, flags, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                unavailable()
            if info.st_size == 0 and create:
                os.ftruncate(descriptor, 16)
                os.pwrite(descriptor, uuid.uuid4().bytes, 0)
                os.fsync(descriptor)
            elif info.st_size != 16:
                unavailable()
            with mmap.mmap(descriptor, 16, access=mmap.ACCESS_WRITE) as witness:
                yield witness
        finally:
            os.close(descriptor)

    def _read(self, witness) -> dict | None:
        try:
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            with os.fdopen(descriptor, "rb") as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 4096:
                    return None
                value = json.load(source)
            if (
                not isinstance(value, dict)
                or value.get("schema_version") != SCHEMA_VERSION
                or value.get("workspace_id") != self.workspace_id
                or value.get("guest_boot_id") != self.boot_id()
                or value.get("continuity_token") != witness[:].hex()
                or not isinstance(value.get("runtime_boot_id"), str)
                or not value["runtime_boot_id"]
                or type(value.get("activity_epoch")) is not int
                or not 0 <= value["activity_epoch"] < 2**63 - 1
            ):
                return None
            since = value.get("idle_since_monotonic_ns")
            if since is not None and (type(since) is not int or not 0 <= since <= self.clock()):
                return None
            return value
        except (OSError, ValueError, TypeError):
            return None

    def _atomic_json(self, value):
        descriptor, temporary = tempfile.mkstemp(prefix=".activity-", dir=self.directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(value, output, separators=(",", ":"))
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _fresh(self, *, busy):
        return {
            "schema_version": SCHEMA_VERSION,
            "workspace_id": self.workspace_id,
            "guest_boot_id": self.boot_id(),
            "runtime_boot_id": str(uuid.uuid4()),
            "activity_epoch": 0,
            "idle_since_monotonic_ns": None if busy else self.clock(),
        }

    def _save(self, witness, value) -> bool:
        # Invalidate first. A failed write cannot leave the old eligible JSON
        # usable by a different process, even if this writer subsequently dies.
        witness[:] = uuid.uuid4().bytes
        value["continuity_token"] = witness[:].hex()
        try:
            self._atomic_json(value)
            return True
        except OSError:
            # Also invalidate a replacement whose directory fsync failed.
            witness[:] = uuid.uuid4().bytes
            return False

    def start(self, *, busy=False) -> None:
        """Runtime startup invalidates the old incarnation, never operation receipts."""
        with self.guard(create=True) as witness:
            self._save(witness, self._fresh(busy=busy))

    def transition(self, *, busy: bool) -> None:
        with self.guard() as witness:
            value = self._read(witness) or self._fresh(busy=busy)
            value["activity_epoch"] += 1
            value["idle_since_monotonic_ns"] = None if busy else self.clock()
            self._save(witness, value)

    def observe(self, *, busy: bool, coverage_complete: bool) -> dict:
        """Call under admission, with fresh lease/registry observations."""
        unknown = {
            "workspace_id": self.workspace_id,
            "activity_state": "unknown",
            "coverage_complete": False,
            "idle_duration_ms": 0,
            "reason": "activity_unavailable",
        }
        try:
            with self.guard() as witness:
                value = self._read(witness)
                if not coverage_complete:
                    # Invalidate once; passive unknown polls need not rewrite.
                    if value is None or value["idle_since_monotonic_ns"] is not None:
                        self._save(witness, self._fresh(busy=True))
                    return unknown
                if value is None:
                    value = self._fresh(busy=busy)
                    if not self._save(witness, value):
                        return unknown
                elif (busy and value["idle_since_monotonic_ns"] is not None) or (
                    not busy and value["idle_since_monotonic_ns"] is None
                ):
                    # A missing completion callback gets a fresh interval at
                    # verified quiescence. Never backdate from an earlier poll.
                    value["activity_epoch"] += 1
                    value["idle_since_monotonic_ns"] = None if busy else self.clock()
                    if not self._save(witness, value):
                        return unknown
                since = value["idle_since_monotonic_ns"]
                return {key: item for key, item in value.items() if key != "continuity_token"} | {
                    "activity_state": "busy" if busy else "idle",
                    "coverage_complete": True,
                    "idle_duration_ms": 0 if since is None else (self.clock() - since) // 10**6,
                    "reason": "active_work" if busy else "",
                }
        except (OSError, StoreError):
            return unknown


def record_activity(root: Path, *, busy: bool) -> None:
    """Admission-gated hook shared by HTTP, executors and inherited CLI leases."""
    directory = root / DIRECTORY
    if not directory.exists():
        return
    workspace_id = os.getenv("RUNTIME_WORKSPACE_ID", "").strip()
    if not workspace_id:
        return
    try:
        WorkspaceMemoryRepository(root, workspace_id).transition(busy=busy)
    except (OSError, StoreError):
        # If even the continuity witness is unavailable, admitting unrecorded
        # work could leave an older proof valid. Existing storage admission
        # fails closed; ordinary JSON failures above do not block user work.
        unavailable()
