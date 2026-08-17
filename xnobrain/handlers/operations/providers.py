"""Feature-owned operation handlers."""

import time
from typing import Any, Callable

from ..query import bucket, csv, time_range

Operation = tuple[Callable[[], Any], str, int]


def operations(handler: Any, request: Any, body: dict[str, Any]) -> dict[str, Operation]:
    p, q, s = request.path_params, request.query_params, handler.service
    agent = lambda: str(q.get("agent") or "").strip() or (_ for _ in ()).throw(ValueError("agent is required"))
    return {
        "providers": (s.providers, "providers retrieved successfully", 200),
        "provider_connect_start": (lambda: s.start_provider_connect(p["provider_id"]), "provider connection started", 200),
        "provider_connect_status": (lambda: s.provider_status(p["provider_id"]), "provider status retrieved", 200),
        "provider_connect_submit": (lambda: s.submit_provider_connect(p["provider_id"], body), "provider connected", 200),
        "provider_disconnect": (lambda: s.disconnect_provider(p["provider_id"]), "provider disconnected", 200),
        "provider_test": (lambda: s.test_provider(p["provider_id"]), "provider tested", 200),
        "provider_models": (lambda: handler._provider_models(p["provider_id"]), "models retrieved successfully", 200),
        "provider_reasoning": (lambda: {"provider_id": p["provider_id"], "model": p["model"], "reasoning": ["low", "medium", "high"]}, "reasoning options retrieved", 200),
        "provider_connections_list": (lambda: s.list_provider_connections(p["provider_id"]), "provider connections retrieved successfully", 200),
        "provider_connection_upsert": (lambda: s.upsert_provider_connection(p["provider_id"], body), "provider connection saved", 200),
        "provider_connection_patch": (lambda: s.patch_provider_connection(p["provider_id"], p["connection_id"], body), "provider connection updated", 200),
        "provider_connection_test": (lambda: s.test_provider_connection(p["provider_id"], p["connection_id"]), "provider connection tested", 200),
        "provider_connection_delete": (lambda: s.delete_provider_connection(p["provider_id"], p["connection_id"]), "provider connection deleted", 200),
        "provider_connection_usage": (lambda: s.connection_usage(p["provider_id"], p["connection_id"]), "provider connection usage retrieved", 200),
        "blends_list": (s.blends.list_blends, "blends retrieved successfully", 200),
        "blends_create": (lambda: s.blends.create_blend(body), "blend created successfully", 201),
        "blends_available_models": (s.blends.available_models, "blend-eligible models retrieved successfully", 200),
        "blends_patch": (lambda: s.blends.update_blend(p["blend_id"], body), "blend updated successfully", 200),
        "blends_delete": (lambda: s.blends.delete_blend(p["blend_id"]), "blend deleted successfully", 200),
    }
