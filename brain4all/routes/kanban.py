"""Kanban API route declarations."""

from ..models import GenericObject, KanbanAssign, KanbanBoardCreate, KanbanComment, KanbanLink, KanbanMove, KanbanScheduleAction, KanbanTaskCreate, KanbanTaskPatch
from .definition import route

ROUTES = (
    route("GET", "/kanban/boards", "kanban_boards", tags=("Kanban",)),
    route("POST", "/kanban/boards", "kanban_board_create", KanbanBoardCreate, tags=("Kanban",)),
    route("GET", "/kanban/boards/{board_slug}", "kanban_board_get", tags=("Kanban",)),
    route("PATCH", "/kanban/boards/{board_slug}", "kanban_board_patch", GenericObject, tags=("Kanban",)),
    route("POST", "/kanban/boards/{board_slug}/select", "kanban_board_select", tags=("Kanban",)),
    route("DELETE", "/kanban/boards/{board_slug}", "kanban_board_delete", tags=("Kanban",)),
    route("GET", "/kanban/boards/{board_slug}/tasks", "kanban_tasks", tags=("Kanban",)),
    route("POST", "/kanban/boards/{board_slug}/tasks", "kanban_task_create", KanbanTaskCreate, tags=("Kanban",)),
    route("GET", "/kanban/boards/{board_slug}/tasks/{task_id}", "kanban_task_get", tags=("Kanban",)),
    route("PATCH", "/kanban/boards/{board_slug}/tasks/{task_id}", "kanban_task_patch", KanbanTaskPatch, tags=("Kanban",)),
    route("POST", "/kanban/boards/{board_slug}/tasks/{task_id}/move", "kanban_task_move", KanbanMove, tags=("Kanban",)),
    route("POST", "/kanban/boards/{board_slug}/tasks/{task_id}/assign", "kanban_task_assign", KanbanAssign, tags=("Kanban",)),
    route("POST", "/kanban/boards/{board_slug}/tasks/{task_id}/schedule", "kanban_task_schedule", KanbanScheduleAction, tags=("Kanban",)),
    route("POST", "/kanban/boards/{board_slug}/tasks/{task_id}/archive", "kanban_task_archive", tags=("Kanban",)),
    route("POST", "/kanban/boards/{board_slug}/tasks/{task_id}/cancel", "kanban_task_cancel", tags=("Kanban",)),
    route("POST", "/kanban/boards/{board_slug}/tasks/{task_id}/team/cancel", "kanban_team_cancel", tags=("Kanban",)),
    route("POST", "/kanban/boards/{board_slug}/tasks/{task_id}/unarchive", "kanban_task_unarchive", tags=("Kanban",)),
    route("GET", "/kanban/boards/{board_slug}/tasks/{task_id}/comments", "kanban_comments", tags=("Kanban",)),
    route("POST", "/kanban/boards/{board_slug}/tasks/{task_id}/comments", "kanban_comment_create", KanbanComment, tags=("Kanban",)),
    route("POST", "/kanban/boards/{board_slug}/tasks/{task_id}/links", "kanban_link_create", KanbanLink, tags=("Kanban",)),
    route("DELETE", "/kanban/boards/{board_slug}/tasks/{task_id}/links", "kanban_link_delete", KanbanLink, tags=("Kanban",)),
    route("GET", "/kanban/boards/{board_slug}/tasks/{task_id}/events", "kanban_events", tags=("Kanban",)),
    route("GET", "/kanban/boards/{board_slug}/events/stream", "kanban_event_stream", special="kanban_stream", tags=("Kanban",)),
    route("GET", "/kanban/diagnostics", "kanban_diagnostics", tags=("Kanban",)),
)
