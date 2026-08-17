"""Providers API route declarations."""

from ..models import BlendCreate, BlendPatch, ConnectionPatch, ConnectionUpsert, ProviderCredential
from .definition import route

ROUTES = (
    route("GET", "/providers", "providers", tags=("Providers",)),
    route("POST", "/providers/{provider_id}/connect", "provider_connect_start", tags=("Providers",)),
    route("GET", "/providers/{provider_id}/connect", "provider_connect_status", tags=("Providers",)),
    route("PUT", "/providers/{provider_id}/connect", "provider_connect_submit", ProviderCredential, tags=("Providers",)),
    route("POST", "/providers/{provider_id}/disconnect", "provider_disconnect", tags=("Providers",)),
    route("POST", "/providers/{provider_id}/test", "provider_test", tags=("Providers",)),
    route("GET", "/providers/{provider_id}/models", "provider_models", tags=("Providers",)),
    route("GET", "/providers/{provider_id}/models/{model}/reasoning", "provider_reasoning", tags=("Providers",)),
    route("GET", "/providers/{provider_id}/connections", "provider_connections_list", tags=("Providers",)),
    route("POST", "/providers/{provider_id}/connections", "provider_connection_upsert", ConnectionUpsert, tags=("Providers",)),
    route("PATCH", "/providers/{provider_id}/connections/{connection_id}", "provider_connection_patch", ConnectionPatch, tags=("Providers",)),
    route("POST", "/providers/{provider_id}/connections/{connection_id}/test", "provider_connection_test", tags=("Providers",)),
    route("DELETE", "/providers/{provider_id}/connections/{connection_id}", "provider_connection_delete", tags=("Providers",)),
    route("GET", "/providers/{provider_id}/connections/{connection_id}/usage", "provider_connection_usage", tags=("Providers",)),
    route("GET", "/blends", "blends_list", tags=("Blends",)),
    route("POST", "/blends", "blends_create", BlendCreate, tags=("Blends",)),
    route("GET", "/blends/available-models", "blends_available_models", tags=("Blends",)),
    route("PATCH", "/blends/{blend_id}", "blends_patch", BlendPatch, tags=("Blends",)),
    route("DELETE", "/blends/{blend_id}", "blends_delete", tags=("Blends",)),
)
