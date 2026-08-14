"""Restore-point operation handlers."""

from typing import Any, Callable

Operation = tuple[Callable[[], Any], str, int]


def operations(handler: Any, request: Any, body: dict[str, Any]) -> dict[str, Operation]:
    p, q, s = request.path_params, request.query_params, handler.service
    return {
        "checkpoints_status": (lambda: s.checkpoint_status(p["agent_id"]), "restore point status retrieved", 200),
        "checkpoints_list": (lambda: s.list_checkpoints(p["agent_id"], q.get("cursor"), int(q.get("limit", 20))), "restore points retrieved", 200),
        "checkpoints_file_versions": (lambda: s.checkpoint_file_versions(p["agent_id"], q.get("path")), "file versions retrieved", 200),
        "checkpoints_diff": (lambda: s.checkpoint_diff(p["agent_id"], p["checkpoint_id"]), "restore point diff retrieved", 200),
        "checkpoints_restore": (lambda: s.restore_checkpoint(p["agent_id"], p["checkpoint_id"], body), "workspace restored", 200),
    }
