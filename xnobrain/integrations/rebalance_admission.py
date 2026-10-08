"""ASGI admission covering uploads and mutations through response completion."""

import re

from starlette.responses import JSONResponse

from ..repositories.base import StoreError
from ..repositories.runtime_update_gate import WorkspaceActivity

_TRANSFER = re.compile(
    r"/xnobrain/api/runtime/v1/(?:"
    r"agents-workspaces/[^/]+/(?:file|preview|workbook)|"
    r"bundles/(?:task-exports|exports)/[^/]+/parts/[^/]+|"
    r"marketplace/agents/[^/]+/export|"
    r"agents/[^/]+/custom-page/export"
    r")/?$"
)


class WorkspaceAdmissionMiddleware:
    def __init__(self, app, root):
        self.app = app
        self.root = root

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        # These are management/read-only observers, never new work. An idle SSE
        # observer is intentionally not an activity lease.
        passive = scope.get("method") in {"GET", "HEAD", "OPTIONS"} and not (
            scope.get("method") == "GET" and _TRANSFER.fullmatch(path)
        )
        management = path.startswith("/xnobrain/api/runtime/v1/system/update/")
        if passive or management:
            return await self.app(scope, receive, send)
        try:
            activity = WorkspaceActivity(self.root)
        except StoreError as error:
            response = JSONResponse(
                {
                    "success": False,
                    "status_code": error.status,
                    "message": "Workspace is undergoing maintenance",
                    "data": {"code": error.code, "retry_after_seconds": 5},
                },
                status_code=error.status,
                headers={
                    "Retry-After": "5",
                    "Cache-Control": "no-store",
                    "X-XNOBrain-Maintenance": (
                        "memory-reclaim"
                        if error.code == "workspace_memory_maintenance"
                        else "vm-rebalance"
                    ),
                },
            )
            return await response(scope, receive, send)
        with activity:
            return await self.app(scope, receive, send)
