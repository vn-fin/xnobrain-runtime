"""Read-only model catalog behavior for the centralized LLM router."""

from __future__ import annotations

from typing import Any

from ..integrations import LLMRouterAPIError
from ..integrations.provider_models import default_reasoning_level
from .base import ServiceError
from .constants import PROVIDER_DEFINITIONS, SUPPORTED_PROVIDERS
from .helpers import cached_method


class ProvidersServiceMixin:
    """Expose only the workload-scoped model catalog.

    Connections and credentials are administered by Control. The runtime's
    API key is intentionally never sent to router management APIs.
    """

    async def providers(self) -> list[dict[str, Any]]:
        providers, _router_available = await self._load_providers()
        return providers

    @cached_method("providers", cache_when=lambda result: result[1])
    async def _load_providers(self) -> tuple[list[dict[str, Any]], bool]:
        try:
            models = (await self.router.list_models())["data"]
            router_available = True
        except LLMRouterAPIError:
            models = []
            router_available = False

        result: list[dict[str, Any]] = []
        for provider in SUPPORTED_PROVIDERS:
            definition = PROVIDER_DEFINITIONS.get(provider, {})
            available_models = [
                item["id"]
                for item in models
                if item.get("provider") == provider and item.get("id") != "auto"
            ]
            model_assignments = {
                str(item["id"]): list(item.get("assignments", []))
                for item in models
                if item.get("provider") == provider
                and item.get("id") != "auto"
                and item.get("assignments")
            }
            result.append({
                "id": provider,
                "display_name": definition.get("display_name", provider.title()),
                "provider_type": provider,
                "description": definition.get(
                    "description", "Managed by your XNOBrain organization."
                ),
                "connection_mode": "managed",
                "base_url": "",
                "requires_base_url": provider == "openai-like",
                "connected": bool(available_models),
                "status": (
                    "unavailable" if not router_available
                    else "connected" if available_models
                    else "disconnected"
                ),
                "last_test_status": "unknown",
                "default_model": "auto" if available_models else "",
                "connection_count": 0,
                "available_models": available_models,
                "model_assignments": model_assignments,
            })
        return result, router_available

    async def provider_models(self, provider: str) -> dict[str, Any]:
        self._provider(provider)
        items = [
            item for item in (await self.router.list_models())["data"]
            if item.get("provider") == provider and item.get("id") != "auto"
        ]
        reasoning = [
            level
            for level in (
                "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra",
            )
            if any(level in item.get("reasoning_levels", []) for item in items)
        ]
        return {
            "provider_id": provider,
            "default_model": "auto",
            "models": [
                {
                    "id": "auto",
                    "reasoning": reasoning,
                    "default_reasoning": default_reasoning_level(reasoning),
                },
                *[
                    {
                        "id": item["id"],
                        "reasoning": list(item.get("reasoning_levels", [])),
                        "default_reasoning": default_reasoning_level(
                            item.get("reasoning_levels", [])
                        ),
                        "assignments": list(item.get("assignments", [])),
                    }
                    for item in items
                ],
            ],
        }

    async def provider_model_reasoning(self, provider: str, model: str) -> dict[str, Any]:
        catalog = await self.provider_models(provider)
        current = next(
            (item for item in catalog["models"] if item["id"] == model),
            None,
        )
        if current is None:
            raise ServiceError("provider model not found", status=404, code="not_found")
        return {
            "provider_id": provider,
            "model": model,
            "reasoning": current["reasoning"],
            "default_reasoning": current["default_reasoning"],
        }

    async def provider_runtime_health(self) -> dict[str, Any]:
        try:
            models = (await self.router.list_models())["data"]
        except LLMRouterAPIError as exc:
            raise ServiceError(
                "Centralized LLM router is unavailable",
                status=503,
                code="provider_runtime_unavailable",
            ) from exc
        return {
            "status": "ok",
            "available": True,
            "model_count": len(models),
        }

    @staticmethod
    def _provider(provider: str) -> str:
        if provider not in SUPPORTED_PROVIDERS:
            raise ServiceError("provider not found", status=404, code="not_found")
        return provider
