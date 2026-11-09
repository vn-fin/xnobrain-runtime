"""Cross-process exclusive file locks with thread reentrancy and timeout."""

from __future__ import annotations

import fcntl
import os
import threading
import time
from pathlib import Path

_REGISTRY_LOCK = threading.RLock()
_LOCKS: dict[str, threading.RLock] = {}
_LOCAL = threading.local()


class FileLockTimeout(TimeoutError):
    """The requested lock could not be acquired before its deadline."""


def _flock(descriptor: int, deadline: float | None) -> None:
    if deadline is None:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FileLockTimeout("file lock timed out") from None
            time.sleep(min(0.05, remaining))


def _acquire_thread_lock(lock: threading.RLock, deadline: float | None) -> None:
    if deadline is None:
        lock.acquire()
        return
    remaining = deadline - time.monotonic()
    if remaining <= 0 or not lock.acquire(timeout=remaining):
        raise FileLockTimeout("file lock timed out")


def _canonical_path(path: Path) -> Path:
    """Resolve the parent but preserve a final symlink for O_NOFOLLOW."""
    parent = Path(os.path.realpath(path.parent))
    return parent / path.name


class FileLock:
    """Process-wide reentrant lock backed by ``flock`` on one canonical path."""

    def __init__(self, path: str | Path, *, timeout: float | None = None):
        self.path = Path(path)
        self.timeout = timeout
        self.fd: int | None = None
        self._path = _canonical_path(self.path)
        self._key = str(self._path)
        self._lock: threading.RLock | None = None
        self._depth = 0

    def __enter__(self):
        deadline = (
            None if self.timeout is None else time.monotonic() + max(0.0, float(self.timeout))
        )
        with _REGISTRY_LOCK:
            lock = _LOCKS.setdefault(self._key, threading.RLock())
        _acquire_thread_lock(lock, deadline)
        held = getattr(_LOCAL, "held", None)
        if held is None:
            held = {}
            _LOCAL.held = held
        previous = held.get(self._key, 0)

        if previous == 0 and self.fd is None:
            descriptor: int | None = None
            try:
                descriptor = os.open(
                    self._path,
                    os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
                    0o600,
                )
                _flock(descriptor, deadline)
            except BaseException:
                if descriptor is not None:
                    os.close(descriptor)
                lock.release()
                raise
            self.fd = descriptor

        held[self._key] = previous + 1
        self._depth += 1
        self._lock = lock
        return self

    def __exit__(self, *_args):
        if self._depth <= 0:
            return
        self._depth -= 1
        descriptor = self.fd if self._depth == 0 else None
        if descriptor is not None:
            self.fd = None
        try:
            if descriptor is not None:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)
        finally:
            held = getattr(_LOCAL, "held", None) or {}
            if held.get(self._key):
                held[self._key] -= 1
                if held[self._key] == 0:
                    held.pop(self._key, None)
            lock = self._lock
            if self._depth == 0:
                self._lock = None
            if lock is not None:
                lock.release()
