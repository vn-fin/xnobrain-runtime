"""ASGI admission covering uploads and mutations through response completion."""

from starlette.responses import JSONResponse

from ..repositories.base import StoreError
from ..repositories.runtime_update_gate import WorkspaceActivity


# Explicit stop/cancel only ends existing work. It stays available during a
# drain so users can finish waiting work themselves; it still holds an activity
# lease, so the drain observes it until the request completes.
_STOP_SUFFIXES = ("/stop", "/cancel")


def _explicit_stop(method: str, path: str) -> bool:
    return method == "POST" and path.rstrip("/").endswith(_STOP_SUFFIXES)


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
        passive = scope.get("method") in {"GET", "HEAD", "OPTIONS"}
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
                        "vm-rebalance"
                        if error.code == "workspace_rebalance_maintenance"
                        else "runtime-update"
                    ),
                },
            )
            return await response(scope, receive, send)
        with activity:
            return await self.app(scope, receive, send)
