"""FastAPI handlers layered over the XNOBrain platform service."""

from __future__ import annotations

import inspect
import time
from typing import Any, Callable

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from ..services import EXPECTED_ERRORS, PlatformService
from ..services.public_text import public_error_message
from .operations import resolve as resolve_operation
from .portability import PortabilityHandlers
from .streaming import StreamingHandlers
from .workspaces import WorkspaceHandlers


class APIHandlers(WorkspaceHandlers, PortabilityHandlers, StreamingHandlers):
    """Expose the stable XNOBrain contract without duplicating Hermes APIs."""

    def __init__(self, service: PlatformService):
        self.service = service
        self.started_at = time.time()

    @staticmethod
    def success(data: Any, message: str = "ok", status: int = 200) -> JSONResponse:
        return JSONResponse(
            {"success": True, "data": data, "message": message, "status_code": status},
            status_code=status,
        )

    @staticmethod
    def failure(error: Exception) -> JSONResponse:
        status = int(getattr(error, "status", 500))
        return JSONResponse(
            {
                "success": False,
                "message": public_error_message(error),
                "error": {"code": str(getattr(error, "code", "internal_error"))},
                "status_code": status,
            },
            status_code=status,
        )

    async def dispatch(self, request: Request, body: dict[str, Any]) -> Response:
        name = request.scope["route"].name
        try:
            operation, message, status = self._operation(name, request, body)
            result = operation()
            if inspect.isawaitable(result):
                result = await result
            return self.success(result, message, status)
        except EXPECTED_ERRORS as error:
            return self.failure(error)
        except (ValueError, KeyError, TypeError) as error:
            if not hasattr(error, "status"):
                error.status, error.code = 400, "invalid_request"
            return self.failure(error)

    def _operation(
        self, name: str, request: Request, body: dict[str, Any]
    ) -> tuple[Callable[[], Any], str, int]:
        return resolve_operation(self, name, request, body)
