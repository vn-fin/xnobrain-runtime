"""Kernel-backed app storage/lifecycle gates shared by Runtime processes.

Lock files are stable coordination inodes outside deletable app/profile trees.
No credentials or content are stored in them. Never unlink a live lock file.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import os
import re
import stat
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .base import StoreError

_EXCLUSIVE = threading.local()


def failure(code: str, status: int = 409):
    raise StoreError(code.replace("_", " "), status=status, code=code)


def _open(root: Path, name: str) -> int:
    if root.is_symlink() or not root.is_dir():
        failure("custom_page_storage_unavailable", 503)
    descriptor = None
    try:
        descriptor = os.open(
            root / name, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600
        )
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != 0:
            failure("custom_page_unsafe_storage")
        return descriptor
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        raise


def acquire(root: Path, name: str, *, shared: bool, timeout: float = 0) -> int:
    descriptor = None
    try:
        descriptor = _open(root, name)
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(
                    descriptor, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB
                )
                return descriptor
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    failure(
                        "custom_page_storage_busy" if timeout else "custom_page_jobs_active",
                        503 if timeout else 409,
                    )
                time.sleep(0.01)
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        if error.errno in {errno.ELOOP, errno.EISDIR}:
            failure("custom_page_unsafe_storage")
        failure("custom_page_storage_unavailable", 503)
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        raise


def release(descriptor: int) -> None:
    # close alone releases this open file description; avoids accidentally
    # unlocking a duplicated/inherited descriptor that a child might still use.
    os.close(descriptor)


def _identifier(value: str) -> str:
    value = str(value).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        failure("custom_page_invalid_identity", 422)
    return value


def execution_name(agent: str) -> str:
    return (
        ".custom-page-execution-"
        + hashlib.sha256(_identifier(agent).encode()).hexdigest()
        + ".lock"
    )


class ExecutionLease:
    def __init__(self, root: Path, agent: str):
        from .runtime_update_gate import WorkspaceActivity

        self.activity = WorkspaceActivity(root)
        try:
            self.descriptor = acquire(root, execution_name(agent), shared=True)
        except BaseException:
            self.activity.close()
            raise

    def close(self):
        if self.descriptor is not None:
            release(self.descriptor)
            self.descriptor = None
        self.activity.close()


class ConversationLease:
    """One admitted writer per agent/conversation across Runtime processes."""

    def __init__(self, root: Path, agent: str, conversation: str):
        key = hashlib.sha256(
            (_identifier(agent) + "\0" + _identifier(conversation)).encode()
        ).hexdigest()
        self.descriptor = acquire(root, ".custom-page-conversation-" + key + ".lock", shared=False)

    def close(self):
        if self.descriptor is not None:
            release(self.descriptor)
            self.descriptor = None


class ScheduleDispatchLease:
    """One native schedule claim/execution/finalization across processes.

    Unlike a timestamp claim this never expires while its descriptor is alive.
    Keep lifecycle/update activity until native history/output has finished too.
    """

    def __init__(self, root: Path, agent: str, schedule: str):
        key = hashlib.sha256(
            (_identifier(agent) + "\0" + _identifier(schedule)).encode()
        ).hexdigest()
        self.execution = ExecutionLease(root, agent)
        try:
            self.descriptor = acquire(root, ".custom-page-schedule-" + key + ".lock", shared=False)
        except BaseException:
            self.execution.close()
            raise

    def close(self):
        if self.descriptor is not None:
            release(self.descriptor)
            self.descriptor = None
        self.execution.close()


@contextmanager
def lifecycle_gate(root: Path, agent: str):
    """Exclusive and thread-reentrant, but never held across an async yield."""
    key = (os.getpid(), str(root.resolve()), _identifier(agent))
    held = getattr(_EXCLUSIVE, "held", None)
    if held is None:
        held = _EXCLUSIVE.held = set()
    if key in held:
        yield
        return
    from .runtime_update_gate import WorkspaceActivity

    with WorkspaceActivity(root):
        descriptor = acquire(root, execution_name(agent), shared=False)
        held.add(key)
        try:
            yield
        finally:
            held.remove(key)
            release(descriptor)


def execution_active(root: Path, agent: str) -> bool:
    try:
        descriptor = acquire(root, execution_name(agent), shared=False)
        release(descriptor)
        return False
    except StoreError as error:
        if error.code == "custom_page_jobs_active":
            return True
        raise
