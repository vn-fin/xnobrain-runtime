"""Providers API route declarations."""

from ..models import BlendCreate, BlendPatch
from .definition import route

ROUTES = (
    route("GET", "/providers", "providers", tags=("Providers",)),
    route("GET", "/providers/{provider_id}/models", "provider_models", tags=("Providers",)),
    route(
        "GET",
        "/providers/{provider_id}/models/{model:path}/reasoning",
        "provider_reasoning",
        tags=("Providers",),
    ),
    route("GET", "/blends", "blends_list", tags=("Blends",)),
    route("POST", "/blends", "blends_create", BlendCreate, tags=("Blends",)),
    route("GET", "/blends/available-models", "blends_available_models", tags=("Blends",)),
    route("PATCH", "/blends/{blend_id}", "blends_patch", BlendPatch, tags=("Blends",)),
    route("DELETE", "/blends/{blend_id}", "blends_delete", tags=("Blends",)),
)
