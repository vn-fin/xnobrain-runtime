"""Redact credential-bearing requests from validation responses."""

from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

# The Zalo Bot PATCH body carries a write-only token. FastAPI's default 422
# echoes the rejected input, so those operations return a generic error instead.
_REDACTED_OPERATION_PREFIXES = ("extensions_", "channels_zalo_bot_")


def install_extension_validation_handler(app) -> None:
    if getattr(app.state, "extension_validation_installed", False):
        return
    previous = app.exception_handlers.get(
        RequestValidationError, request_validation_exception_handler
    )

    async def handle(request, error):
        operation = getattr(request.scope.get("route"), "name", "")
        prefix = next(
            (value for value in _REDACTED_OPERATION_PREFIXES if operation.startswith(value)),
            None,
        )
        if prefix is None:
            return await previous(request, error)
        message = (
            "Channel input is invalid"
            if prefix.startswith("channels_")
            else "Extension input is invalid"
        )
        return JSONResponse(
            {
                "success": False,
                "message": message,
                "error": {"code": "invalid_request"},
                "status_code": 422,
            },
            status_code=422,
        )

    app.add_exception_handler(RequestValidationError, handle)
    app.state.extension_validation_installed = True
