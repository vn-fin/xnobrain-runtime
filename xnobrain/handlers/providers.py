"""Provider-specific asynchronous HTTP handlers."""

from typing import Any


class ProviderHandlers:

    async def _provider_models(self, provider: str) -> dict[str, Any]:
            items = (await self.service.router.list_models())["data"]
            return {"provider_id": provider, "default_model": "auto", "models": [{"id": "auto", "reasoning": ["low", "medium", "high"]}] + [{"id": x["id"], "reasoning": ["low", "medium", "high"]} for x in items if x.get("provider") == provider]}
