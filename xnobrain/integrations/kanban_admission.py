"""Intercept the pinned native claim seam without changing its scheduler/schema."""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

from ..repositories.runtime_update_gate import WorkspaceActivity
from .native_admission import NativeAdmission
from .run_admission import managed

_dispatch = ContextVar("capacity_kanban_dispatch", default=None)


def install(kb):
    for name in ("claim_task", "claim_review_task"):
        original = getattr(kb, name)
        if getattr(original, "_capacity_wrapped", False) is True:
            continue

        def wrap(function):
            @wraps(function)
            def claim(conn, task_id, *args, **kwargs):
                scope = _dispatch.get()
                if scope is None:
                    return function(conn, task_id, *args, **kwargs)
                root, board, pending = scope
                if not managed():
                    with WorkspaceActivity(root):
                        return function(conn, task_id, *args, **kwargs)
                task = kb.get_task(conn, task_id)
                if task is None:
                    return None
                key = f"kanban:{board}:{task_id}:{task.current_run_id}:{task.status}"
                gate = NativeAdmission(root, key, "kanban")
                if not gate.poll():
                    return None
                try:
                    with WorkspaceActivity(root):
                        claimed = function(conn, task_id, *args, **kwargs)
                    if claimed is None:
                        gate.defer()
                    else:
                        pending[task_id] = gate
                    return claimed
                except BaseException:
                    gate.abort()
                    raise

            claim._capacity_wrapped = True
            return claim

        setattr(kb, name, wrap(original))


@contextmanager
def dispatch_scope(kb, root, board):
    if root is None:
        yield
        return
    install(kb)
    pending = {}
    token = _dispatch.set((root, board, pending))
    try:
        yield
    finally:
        _dispatch.reset(token)
        for gate in pending.values():
            gate.defer()


def take(task_id):
    scope = _dispatch.get()
    return scope[2].pop(task_id, None) if scope else None
