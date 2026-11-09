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


class FileLock:
    """Process-wide reentrant lock backed by ``flock`` on one canonical path."""

    def __init__(self, path: str | Path, *, timeout: float | None = None):
        self.path = Path(path)
        self.timeout = timeout
        self.fd: int | None = None
        self._key = str(self.path.absolute())
        self._lock: threading.RLock | None = None
        self._nested = False

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
        if self._key in held:
            held[self._key] += 1
            self._nested = True
            self._lock = lock
            return self

        descriptor: int | None = None
        try:
            descriptor = os.open(
                self.path,
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
        held[self._key] = 1
        self._lock = lock
        return self

    def __exit__(self, *_args):
        held = getattr(_LOCAL, "held", None) or {}
        if self._nested:
            if held.get(self._key):
                held[self._key] -= 1
            self._nested = False
            if self._lock is not None:
                self._lock.release()
            self._lock = None
            return

        try:
            if self.fd is not None:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
                os.close(self.fd)
        finally:
            self.fd = None
            held.pop(self._key, None)
            if self._lock is not None:
                self._lock.release()
            self._lock = None
