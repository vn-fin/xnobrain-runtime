"""ASGI admission covering uploads and mutations through response completion."""

import re

from starlette.responses import JSONResponse

from ..repositories.base import StoreError
from ..repositories.runtime_update_gate import WorkspaceActivity
from ..services.base import ServiceError
from ..services.storage_budget import require_storage_growth

_TRANSFER = re.compile(
    r"/xnobrain/api/runtime/v1/(?:"
    r"agents-workspaces/[^/]+/(?:file|preview|workbook)|"
    r"bundles/(?:task-exports|exports)/[^/]+/parts/[^/]+|"
    r"marketplace/agents/[^/]+/export|"
    r"agents/[^/]+/custom-page/export"
    r")/?$"
)


# Explicit stop/cancel only ends existing work. It stays available during a
# drain so users can finish waiting work themselves; it still holds an activity
# lease, so the drain observes it until the request completes.
_STOP_SUFFIXES = ("/stop", "/cancel")


def _explicit_stop(method: str, path: str) -> bool:
    return method == "POST" and path.rstrip("/").endswith(_STOP_SUFFIXES)


# POST read/cleanup endpoints use an explicit allowlist. GET downloads and
# DELETE remain available; starting a new export consumes temporary storage.
_STORAGE_CLEANUP = re.compile(
    r"/xnobrain/api/runtime/v1/(?:"
    r"agents-workspaces/[^/]+/(?:read|delete)|"
    r"agents/[^/]+/custom-page/(?:delete|remove-agent(?:/recover)?|query)|"
    r"agents/[^/]+/custom-page/revisions/[^/]+/cancel|"
    r"(?:sessions|conversations)/[^/]+/runs/[^/]+/approval"
    r")/?$"
)


def storage_growth_request(method: str, path: str) -> bool:
    return (
        method not in {"GET", "HEAD", "OPTIONS", "DELETE"}
        and not _explicit_stop(method, path)
        and not (method == "POST" and _STORAGE_CLEANUP.fullmatch(path))
        and not path.startswith("/xnobrain/api/runtime/v1/system/update/")
    )


class WorkspaceAdmissionMiddleware:
    def __init__(self, app, root):
        self.app = app
        self.root = root

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if storage_growth_request(scope.get("method", ""), path):
            try:
                require_storage_growth()
            except ServiceError as error:
                response = JSONResponse(
                    {
                        "success": False,
                        "status_code": error.status,
                        "message": str(error),
                        "error": {"code": error.code},
                        "data": {"code": error.code},
                    },
                    status_code=error.status,
                    headers={"Retry-After": "30", "Cache-Control": "no-store"},
                )
                return await response(scope, receive, send)
        # These are management/read-only observers, never new work. An idle SSE
        # observer is intentionally not an activity lease.
        passive = scope.get("method") in {"GET", "HEAD", "OPTIONS"} and not (
            scope.get("method") == "GET" and _TRANSFER.fullmatch(path)
        )
        management = path.startswith("/xnobrain/api/runtime/v1/system/update/")
        if passive or management:
            return await self.app(scope, receive, send)
        try:
            activity = WorkspaceActivity(
                self.root, mutation=not _explicit_stop(scope.get("method", ""), path)
            )
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
                        if error.code == "workspace_rebalance_maintenance"
                        else "runtime-update"
                    ),
                },
            )
            return await response(scope, receive, send)
        with activity:
            return await self.app(scope, receive, send)
