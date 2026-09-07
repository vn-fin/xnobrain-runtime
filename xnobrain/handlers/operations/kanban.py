"""Feature-owned operation handlers."""

import time
from typing import Any, Callable

from ..query import bucket, csv, time_range

Operation = tuple[Callable[[], Any], str, int]


def operations(handler: Any, request: Any, body: dict[str, Any]) -> dict[str, Operation]:
    p, q, s = request.path_params, request.query_params, handler.service
    agent = lambda: (
        str(q.get("agent") or "").strip() or (_ for _ in ()).throw(ValueError("agent is required"))
    )
    return {
        "kanban_boards": (
            lambda: s.kanban.list_boards(
                include_archived=str(q.get("include_archived", "false")).lower() == "true"
            ),
            "Kanban boards retrieved successfully",
            200,
        ),
        "kanban_board_create": (
            lambda: s.kanban.create_board(body),
            "Kanban board created successfully",
            201,
        ),
        "kanban_board_get": (
            lambda: s.kanban.get_board(
                p["board_slug"],
                include_archived=str(q.get("include_archived", "false")).lower() == "true",
            ),
            "Kanban board retrieved successfully",
            200,
        ),
        "kanban_board_stats": (
            lambda: s.kanban.board_stats(p["board_slug"]),
            "Kanban board statistics retrieved successfully",
            200,
        ),
        "kanban_board_patch": (
            lambda: s.kanban.patch_board(p["board_slug"], body),
            "Kanban board updated successfully",
            200,
        ),
        "kanban_board_select": (
            lambda: s.kanban.select_board(p["board_slug"]),
            "Kanban board selected",
            200,
        ),
        "kanban_board_delete": (
            lambda: s.kanban.delete_board(p["board_slug"]),
            "Kanban board archived",
            200,
        ),
        "kanban_tasks": (
            lambda: s.kanban.list_tasks(p["board_slug"], dict(q)),
            "Kanban tasks retrieved successfully",
            200,
        ),
        "kanban_task_create": (
            lambda: s.kanban.create_task(p["board_slug"], body),
            "Kanban task created successfully",
            201,
        ),
        "kanban_task_get": (
            lambda: s.kanban.get_task(p["board_slug"], p["task_id"]),
            "Kanban task retrieved successfully",
            200,
        ),
        "kanban_task_patch": (
            lambda: s.kanban.patch_task(p["board_slug"], p["task_id"], body),
            "Kanban task updated successfully",
            200,
        ),
        "kanban_task_move": (
            lambda: s.kanban.move_task(
                p["board_slug"], p["task_id"], body["status"], reason=body.get("reason")
            ),
            "Kanban task moved successfully",
            200,
        ),
        "kanban_task_assign": (
            lambda: s.kanban.assign_task(p["board_slug"], p["task_id"], body),
            "Kanban task assignment updated",
            200,
        ),
        "kanban_task_schedule": (
            lambda: s.kanban.schedule_action(p["board_slug"], p["task_id"], body["action"]),
            "Kanban task schedule updated",
            200,
        ),
        "kanban_task_archive": (
            lambda: s.kanban.archive_task(p["board_slug"], p["task_id"]),
            "Kanban task archived",
            200,
        ),
        "kanban_task_cancel": (
            lambda: s.kanban.cancel_task(p["board_slug"], p["task_id"]),
            "Kanban task cancelled",
            200,
        ),
        "kanban_team_cancel": (
            lambda: s.kanban.cancel_team_task(p["board_slug"], p["task_id"]),
            "Kanban team run cancelled",
            200,
        ),
        "kanban_task_unarchive": (
            lambda: s.kanban.archive_task(p["board_slug"], p["task_id"], unarchive=True),
            "Kanban task unarchived",
            200,
        ),
        "kanban_comments": (
            lambda: s.kanban.list_comments(p["board_slug"], p["task_id"]),
            "Kanban comments retrieved successfully",
            200,
        ),
        "kanban_comment_create": (
            lambda: s.kanban.add_comment(p["board_slug"], p["task_id"], body),
            "Kanban comment added successfully",
            201,
        ),
        "kanban_link_create": (
            lambda: s.kanban.link_tasks(p["board_slug"], body["parent_id"], body["child_id"]),
            "Kanban dependency linked successfully",
            201,
        ),
        "kanban_link_delete": (
            lambda: s.kanban.unlink_tasks(p["board_slug"], body["parent_id"], body["child_id"]),
            "Kanban dependency unlinked successfully",
            200,
        ),
        "kanban_events": (
            lambda: s.kanban.list_events(p["board_slug"], p["task_id"]),
            "Kanban events retrieved successfully",
            200,
        ),
        "kanban_diagnostics": (
            lambda: s.kanban.diagnostics(str(q.get("board") or "default")),
            "Kanban diagnostics retrieved successfully",
            200,
        ),
    }
