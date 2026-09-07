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
        "bundle_export_start": (lambda: s.start_bundle_export(body), "bundle export prepared", 201),
        "bundle_export_delete": (
            lambda: s.delete_bundle_transfer("export", p["transfer_id"]),
            "bundle export deleted",
            200,
        ),
        "bundle_upload_start": (lambda: s.start_bundle_upload(body), "bundle upload created", 201),
        "bundle_upload_complete": (
            lambda: s.complete_bundle_upload(p["transfer_id"], body),
            "bundle upload completed",
            200,
        ),
        "bundle_upload_apply": (
            lambda: s.apply_bundle_upload(p["transfer_id"], body),
            "bundle imported successfully",
            201,
        ),
        "bundle_upload_delete": (
            lambda: s.delete_bundle_transfer("upload", p["transfer_id"]),
            "bundle upload deleted",
            200,
        ),
    }
