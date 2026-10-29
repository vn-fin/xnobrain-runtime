"""Small adapter around the Hermes Kanban database.

XNOBrain leaves the native task, run, and event schemas untouched. Its
schedule metadata lives in a namespaced extension table created here, at the
only boundary where the platform service reaches into Hermes' Kanban
implementation. The import is lazy so the rest of the API can still be
imported by tooling and tests that do not install the Hermes runtime.
"""
# ruff: noqa: F401

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

_log = logging.getLogger("xnobrain.kanban")


class KanbanUnavailable(RuntimeError):
    """Raised when the Hermes Kanban package is not available."""


def _module():
    try:
        from hermes_cli import kanban_db
    except Exception as exc:  # pragma: no cover - depends on deployment image
        raise KanbanUnavailable(
            "Kanban is unavailable; install the required local runtime"
        ) from exc
    return kanban_db


@contextmanager
def connection(board: str = "default", *, exclusive: bool = False) -> Iterator[Any]:
    from ..repositories.custom_page_locks import acquire, release

    kb = _module()
    normalized = board_slug(board)
    # Keep the lock inode outside the directory that board archival renames.
    root = Path(kb.kanban_db_path(board="default")).parent
    root.mkdir(parents=True, exist_ok=True)
    lock = acquire(root, f".board-{normalized}.lock", shared=not exclusive, timeout=5)
    try:
        if normalized != "default" and not kb.board_exists(normalized):
            raise ValueError("board no longer exists")
        if hasattr(kb, "connect_closing"):
            with kb.connect_closing(board=normalized) as conn:
                yield conn
        else:
            conn = kb.connect(board=normalized)
            try:
                yield conn
            finally:
                conn.close()
    finally:
        release(lock)


def board_slug(value: str | None) -> str:
    kb = _module()
    slug = str(value or "default").strip().lower()
    normalizer = getattr(kb, "_normalize_board_slug", None)
    if normalizer is not None:
        normalized = normalizer(slug)
        if normalized:
            return normalized
    if (
        not slug
        or len(slug) > 64
        or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for ch in slug)
    ):
        raise ValueError("invalid board slug")
    return slug


__all__ = [name for name in globals() if not name.startswith("__")]
