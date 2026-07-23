"""Small adapter around the Hermes Kanban database.

Brain4All deliberately does not own a Kanban schema.  This module is the only
place where the platform service reaches into Hermes' Kanban implementation;
the import is lazy so the rest of the API can still be imported by tooling and
tests that do not install the Hermes runtime.
"""

from __future__ import annotations

from contextlib import contextmanager
import asyncio
import logging
from pathlib import Path
from typing import Any, Iterator

_log = logging.getLogger("brain4all.kanban")


class KanbanUnavailable(RuntimeError):
    """Raised when the Hermes Kanban package is not available."""


def _module():
    try:
        from hermes_cli import kanban_db
    except Exception as exc:  # pragma: no cover - depends on deployment image
        raise KanbanUnavailable(
            "Hermes Kanban is unavailable; install the pinned Hermes runtime"
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


def list_boards(*, include_archived: bool = False) -> list[dict[str, Any]]:
    return list(_module().list_boards(include_archived=include_archived))


def create_board(slug: str, **fields: Any) -> dict[str, Any]:
    return dict(_module().create_board(board_slug(slug), **fields))


def write_board_metadata(slug: str, **fields: Any) -> dict[str, Any]:
    return dict(_module().write_board_metadata(board_slug(slug), **fields))


def remove_board(slug: str, *, archive: bool = True) -> dict[str, Any]:
    return dict(_module().remove_board(board_slug(slug), archive=archive))


def current_board() -> str:
    return str(_module().get_current_board())


def select_board(slug: str) -> str:
    normalized = board_slug(slug)
    kb = _module()
    if not getattr(kb, "board_exists", lambda _slug: False)(normalized):
        raise ValueError(f"board {normalized!r} does not exist")
    kb.set_current_board(normalized)
    return normalized


def task_dependencies(conn: Any, task_id: str) -> list[str]:
    kb = _module()
    return [str(item) for item in kb.parent_ids(conn, task_id)]


def task_children(conn: Any, task_id: str) -> list[str]:
    kb = _module()
    return [str(item) for item in kb.child_ids(conn, task_id)]


def task_events(conn: Any, task_id: str) -> list[Any]:
    return list(_module().list_events(conn, task_id))


def task_comments(conn: Any, task_id: str) -> list[Any]:
    return list(_module().list_comments(conn, task_id))


def task_runs(conn: Any, task_id: str) -> list[Any]:
    return list(_module().list_runs(conn, task_id))


def task_attachments(conn: Any, task_id: str) -> list[Any]:
    return list(_module().list_attachments(conn, task_id))


def attachment_path(attachment: Any) -> Path:
    return Path(str(attachment.stored_path)).resolve()


async def dispatcher_loop(*, interval_seconds: float = 15.0) -> None:
    """Run Hermes' supported dispatcher tick inside the host FastAPI process.

    The worker spawning, claiming, recovery, and board lock all remain in
    Hermes ``dispatch_once``. Brain4All only supplies the host lifecycle; this
    is intentionally not a second worker process or cron scheduler.
    """
    while True:
        try:
            kb = _module()
            for board in kb.list_boards(include_archived=False):
                slug = str(board.get("slug") or "default")
                with connection(slug) as conn:
                    kb.dispatch_once(conn, board=slug)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # keep local manual Kanban usable if worker runtime is degraded
            _log.warning("Kanban dispatcher tick failed: %s", type(exc).__name__)
        await asyncio.sleep(max(1.0, float(interval_seconds)))
