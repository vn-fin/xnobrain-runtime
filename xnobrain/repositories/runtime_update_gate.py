"""Cooperative workspace admission/activity gates for FT0015 update compatibility.

Admission is short-lived: publish maintenance or check it and register activity.
Activity lasts through the actual storage/executor work. Checkpoint takes activity
exclusively, and never treats coroutine cancellation as executor completion.
"""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from inspect import iscoroutinefunction
from pathlib import Path

from .base import StoreError
from .custom_page_locks import _open, acquire, release

ADMISSION = ".runtime-update-admission.lock"
ACTIVITY = ".runtime-update-activity.lock"
OPERATION = ".runtime-update-operation.lock"
_lineage = ContextVar("workspace_activity", default=None)


def maintenance(root: Path) -> dict:
    # Import locally: RuntimeUpdateRepository publishes using admission_gate.
    from .runtime_updates import RuntimeUpdateRepository

    return RuntimeUpdateRepository._read(root / "runtime-updates" / "maintenance.json")


# Maintenance kinds whose drain lets already admitted work finish, including
# legitimate descendants created inside that work's own activity lineage.
LINEAGE_KINDS = frozenset({"vm_rebalance", "runtime_source"})


def require_admission(root: Path) -> None:
    gate = maintenance(root)
    parent = _lineage.get()
    if (
        gate.get("kind") in LINEAGE_KINDS
        and parent is not None
        and parent.root == root
        and parent.descriptor is not None
    ):
        return
    if gate.get("dispatch_paused"):
        raise StoreError(
            "Workspace is undergoing maintenance",
            status=503,
            code="workspace_rebalance_maintenance"
            if gate.get("kind") == "vm_rebalance"
            else "runtime_update_maintenance",
        )


@contextmanager
def admission_gate(root: Path):
    descriptor = acquire(root, ADMISSION, shared=False, timeout=2)
    try:
        yield
    finally:
        release(descriptor)


class WorkspaceActivity:
    def __init__(self, root: Path, *, mutation: bool = True):
        self.root = root
        self.descriptor = None
        self._token = None
        with admission_gate(root):
            if mutation:
                require_admission(root)
            self.descriptor = acquire(root, ACTIVITY, shared=True)

    def close(self):
        if self.descriptor is not None:
            release(self.descriptor)
            self.descriptor = None

    def __enter__(self):
        self._token = _lineage.set(self)
        return self

    def __exit__(self, *_error):
        if self._token is not None:
            _lineage.reset(self._token)
            self._token = None
        self.close()

    @contextmanager
    def lineage(self):
        """Attach an already-owned lease to an async child without closing it."""
        token = _lineage.set(self)
        try:
            yield
        finally:
            _lineage.reset(token)

    async def run(self, function, *args):
        with self.lineage():
            return await function(*args)


def workspace_activity(root_for):
    """Admit before the first await/claim and retain ownership through completion."""

    def decorate(function):
        if iscoroutinefunction(function):

            @wraps(function)
            async def asynchronous(self, *args, **kwargs):
                with WorkspaceActivity(root_for(self)):
                    return await function(self, *args, **kwargs)

            return asynchronous

        @wraps(function)
        def synchronous(self, *args, **kwargs):
            with WorkspaceActivity(root_for(self)):
                return function(self, *args, **kwargs)

        return synchronous

    return decorate


@contextmanager
def checkpoint_gate(root: Path):
    # Drain has already persisted maintenance, so new mutations cannot enter.
    descriptor = acquire(root, ACTIVITY, shared=False)
    try:
        yield
    finally:
        release(descriptor)


def activity_present(root: Path) -> bool:
    try:
        with checkpoint_gate(root):
            return False
    except StoreError as error:
        if error.code == "custom_page_jobs_active":
            return True
        raise


@contextmanager
def update_operation(root: Path):
    """Serialize update commands across processes (owned fd, no thread reentry)."""
    descriptor = acquire(root, OPERATION, shared=False)
    try:
        yield
    finally:
        release(descriptor)


def is_coordination_file(path: Path, root: Path) -> bool:
    if path.parent != root:
        return False
    name = path.name
    return name in {ADMISSION, ACTIVITY, OPERATION, ".custom-page-storage.lock"} or bool(
        re.fullmatch(
            r"\.(?:custom-page-(?:execution|conversation|schedule)|conversation-creation)-[a-f0-9]{64}\.lock",
            name,
        )
    )


def validate_coordination_file(path: Path) -> None:
    # The file content is not durable data, but unsafe/nonempty files cannot hide
    # from integrity verification by choosing a reserved lock filename.
    descriptor = _open(path.parent, path.name)
    try:
        if os.fstat(descriptor).st_size:
            raise StoreError(
                "invalid coordination file", status=409, code="runtime_update_unsafe_data"
            )
    finally:
        release(descriptor)
