from ..models.ui_composition import AssistLayout
from .definition import route

ROUTES = (
    route("POST", "/ui-assistance", "ui_assistance_start", AssistLayout, tags=("UI composition",)),
    route("GET", "/ui-assistance/{assistance_id}", "ui_assistance_get", tags=("UI composition",)),
    route(
        "POST",
        "/ui-assistance/{assistance_id}/cancel",
        "ui_assistance_cancel",
        tags=("UI composition",),
    ),
)
