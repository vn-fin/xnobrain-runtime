"""Kanban board operations."""

from .kanban_support import (
    Any,
    _module,
    board_slug,
    connection,
)

def list_boards(*, include_archived: bool = False) -> list[dict[str, Any]]:
    return list(_module().list_boards(include_archived=include_archived))


def create_board(slug: str, **fields: Any) -> dict[str, Any]:
    return dict(_module().create_board(board_slug(slug), **fields))


def write_board_metadata(slug: str, **fields: Any) -> dict[str, Any]:
    return dict(_module().write_board_metadata(board_slug(slug), **fields))


def remove_board(slug: str, *, archive: bool = True) -> dict[str, Any]:
    return dict(_module().remove_board(board_slug(slug), archive=archive))


def delete_assignee_tasks(assignee: str) -> int:
    """Permanently delete every task assigned to a profile on every board."""
    kb = _module()
    deleted = 0
    for board in kb.list_boards(include_archived=True):
        slug = board_slug(str(board.get("slug") or "default"))
        with connection(slug) as conn:
            tasks = kb.list_tasks(conn, assignee=assignee, include_archived=True)
            for task in tasks:
                if kb.delete_task(conn, str(task.id)):
                    deleted += 1
    return deleted


def current_board() -> str:
    return str(_module().get_current_board())


def select_board(slug: str) -> str:
    normalized = board_slug(slug)
    kb = _module()
    if not getattr(kb, "board_exists", lambda _slug: False)(normalized):
        raise ValueError(f"board {normalized!r} does not exist")
    kb.set_current_board(normalized)
    return normalized
