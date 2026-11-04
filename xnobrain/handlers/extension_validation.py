"""Redact credential-bearing extension requests from validation responses."""

from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def install_extension_validation_handler(app) -> None:
    if getattr(app.state, "extension_validation_installed", False):
        return
    previous = app.exception_handlers.get(
        RequestValidationError, request_validation_exception_handler
    )

    async def handle(request, error):
        operation = getattr(request.scope.get("route"), "name", "")
        if not operation.startswith("extensions_"):
            return await previous(request, error)
        return JSONResponse(
            {
                "success": False,
                "message": "Extension input is invalid",
                "error": {"code": "invalid_request"},
                "status_code": 422,
            },
            status_code=422,
        )

    app.add_exception_handler(RequestValidationError, handle)
    app.state.extension_validation_installed = True
