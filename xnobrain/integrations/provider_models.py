"""Grouped ProviderModels behavior for 9router."""

from .nine_router_support import (
    Any,
    Mapping,
    NINE_ROUTER_DEFAULT_MODEL,
    NINE_ROUTER_PROVIDER_KEY,
    NineRouterAPIError,
    OPENAI_COMPATIBLE_PROVIDERS,
    OPENCODE_ZEN_ROUTER_ALIAS,
    ROUTER_MODEL_ALIASES,
    ROUTER_PROVIDER_BY_MODEL_OWNER,
    quote,
)


class ProviderModelsMixin:
    async def list_models(self, *, ensure_auto: bool = True) -> dict[str, Any]:
        connection_payload = await self.list_connections()
        connected_providers = {
            str(item.get("provider") or "")
            for item in connection_payload["connections"]
            if item.get("active") is not False
        }
        active_owners = {
            ROUTER_MODEL_ALIASES[provider]
            for provider in connected_providers
            if provider in ROUTER_MODEL_ALIASES
        }
        if "opencode" in connected_providers:
            active_owners.add(OPENCODE_ZEN_ROUTER_ALIAS)
        active_owners.update(
            provider for provider in connected_providers
            if provider in OPENAI_COMPATIBLE_PROVIDERS
        )
        payload = await self._request("GET", "/v1/models?kind=llm")
        raw_models = payload.get("data", []) if isinstance(payload, Mapping) else []
        models: list[dict[str, str]] = []
        blends: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in raw_models if isinstance(raw_models, list) else []:
            if not isinstance(item, Mapping):
                continue
            model_id = str(item.get("id") or "").strip()
            if not model_id or model_id == NINE_ROUTER_DEFAULT_MODEL or model_id in seen:
                continue
            owner = str(item.get("owned_by") or self._model_owner(model_id)).strip()
            if owner == "combo":
                # A user-named combo is surfaced as a first-class "blend".
                seen.add(model_id)
                blends.append({"id": model_id, "provider": "blend", "name": model_id})
                continue
            if owner not in active_owners:
                continue
            public_model_id = model_id
            if owner == OPENCODE_ZEN_ROUTER_ALIAS:
                public_model_id = "oc/" + model_id.removeprefix(
                    f"{OPENCODE_ZEN_ROUTER_ALIAS}/"
                )
            if public_model_id in seen:
                continue
            seen.add(public_model_id)
            provider = next(
                (
                    provider_id
                    for provider_id, alias in ROUTER_MODEL_ALIASES.items()
                    if alias == owner
                ),
                ROUTER_PROVIDER_BY_MODEL_OWNER.get(owner, owner),
            )
            model: dict[str, Any] = {
                "id": public_model_id,
                "provider": provider,
                "name": str(item.get("name") or public_model_id),
            }
            context_length = self._model_context_length(item)
            if context_length is not None:
                model["context_length"] = context_length
            reasoning_levels = self._model_reasoning_levels(item)
            if reasoning_levels:
                model["reasoning_levels"] = reasoning_levels
            models.append(model)
        if ensure_auto:
            await self._ensure_auto_combo(models)
        models.extend(await self._opencode_free_models(seen))
        return {
            "object": "list",
            "provider": NINE_ROUTER_PROVIDER_KEY,
            "default_model": NINE_ROUTER_DEFAULT_MODEL,
            "data": [
                {
                    "id": NINE_ROUTER_DEFAULT_MODEL,
                    "provider": NINE_ROUTER_PROVIDER_KEY,
                    "name": "Auto",
                },
                *blends,
                *models,
            ],
        }


    @staticmethod
    def _model_context_length(item: Mapping[str, Any]) -> int | None:
        for key in ("context_length", "context_window", "max_input_tokens"):
            value = item.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                return int(value)
        return None


    @staticmethod
    def _model_reasoning_levels(item: Mapping[str, Any]) -> list[str]:
        raw = item.get("reasoning_levels") or item.get("supported_reasoning")
        if not isinstance(raw, list):
            return []
        allowed = {"low", "medium", "high"}
        return [str(level) for level in raw if str(level) in allowed]


    async def _opencode_free_models(self, seen: set[str]) -> list[dict[str, str]]:
        path = (
            "/api/providers/suggested-models?"
            f"url={quote('https://opencode.ai/zen/v1/models', safe='')}&"
            "type=opencode-free"
        )
        try:
            payload = await self._request("GET", path)
        except NineRouterAPIError:
            return []
        raw_models = payload.get("data", []) if isinstance(payload, Mapping) else []
        result: list[dict[str, str]] = []
        for item in raw_models if isinstance(raw_models, list) else []:
            if not isinstance(item, Mapping):
                continue
            raw_id = str(item.get("id") or "").strip()
            if not raw_id:
                continue
            model_id = raw_id if raw_id.startswith("oc/") else f"oc/{raw_id}"
            if model_id in seen:
                continue
            seen.add(model_id)
            result.append({
                "id": model_id,
                "provider": "opencode",
                "name": str(item.get("name") or model_id),
            })
        return result
