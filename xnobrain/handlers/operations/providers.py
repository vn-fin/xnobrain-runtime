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
        "provider_models": (lambda: s.provider_models(p["provider_id"]), "models retrieved successfully", 200),
        "provider_reasoning": (lambda: s.provider_model_reasoning(p["provider_id"], p["model"]), "reasoning options retrieved", 200),
        "blends_list": (s.blends.list_blends, "blends retrieved successfully", 200),
        "blends_create": (lambda: s.blends.create_blend(body), "blend created successfully", 201),
        "blends_available_models": (s.blends.available_models, "blend-eligible models retrieved successfully", 200),
        "blends_patch": (lambda: s.blends.update_blend(p["blend_id"], body), "blend updated successfully", 200),
        "blends_delete": (lambda: s.blends.delete_blend(p["blend_id"]), "blend deleted successfully", 200),
    }
