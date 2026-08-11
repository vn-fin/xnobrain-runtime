"""Small adapter around the Hermes Kanban database.

Brain4All leaves the native task, run, and event schemas untouched. Its
schedule metadata lives in a namespaced extension table created here, at the
only boundary where the platform service reaches into Hermes' Kanban
implementation. The import is lazy so the rest of the API can still be
imported by tooling and tests that do not install the Hermes runtime.
"""
# ruff: noqa: F401

from __future__ import annotations

from contextlib import contextmanager
import asyncio
import inspect
import json
import logging
from pathlib import Path
import re
import time
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
def connection(board: str = "default") -> Iterator[Any]:
    kb = _module()
    try:
        with kb.connect_closing(board=board) as conn:
            yield conn
    except AttributeError:
        # Older pinned Hermes revisions exposed connect() but not the safer
        # connect_closing().  Keep the fallback narrow and close explicitly.
        conn = kb.connect(board=board)
        try:
            yield conn
        finally:
            conn.close()


def board_slug(value: str | None) -> str:
    kb = _module()
    slug = str(value or "default").strip().lower()
    normalizer = getattr(kb, "_normalize_board_slug", None)
    if normalizer is not None:
        normalized = normalizer(slug)
        if normalized:
            return normalized
    if not slug or len(slug) > 64 or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for ch in slug):
        raise ValueError("invalid board slug")
    return slug

__all__ = [name for name in globals() if not name.startswith("__")]
