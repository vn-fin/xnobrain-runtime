from ...trusted_context import from_request


def operations(handler, request, body):
    service = getattr(handler.service, "ui_composition", None)
    trusted = from_request(request)
    identifier = request.path_params.get("assistance_id")
    return {
        "ui_assistance_start": (lambda: service.start(body, trusted), "layout assistance", 202),
        "ui_assistance_get": (lambda: service.read(identifier, trusted), "layout assistance", 200),
        "ui_assistance_cancel": (
            lambda: service.cancel(identifier, trusted),
            "layout assistance cancelled",
            200,
        ),
    }
