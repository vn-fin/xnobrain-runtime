"""Grouped BlendsIntegration behavior for 9router."""

from .nine_router_support import (
    Any,
    Mapping,
    NINE_ROUTER_DEFAULT_MODEL,
    NineRouterAPIError,
    display_nine_router_model,
    quote,
    route_nine_router_model,
)


class BlendsIntegrationMixin:
    async def ensure_auto_combo(self) -> None:
        models = (await self.list_models(ensure_auto=False))["data"]
        if not await self._ensure_auto_combo(models):
            raise NineRouterAPIError(
                "connect at least one provider before using auto",
                code="provider_connection_required",
                status=409,
            )


    async def _ensure_auto_combo(self, models: list[Mapping[str, Any]]) -> bool:
        selected: list[str] = []
        owners: set[str] = set()
        for item in models:
            model_id = str(item.get("id") or "").strip()
            if (not model_id or model_id == NINE_ROUTER_DEFAULT_MODEL
                    or "/" not in model_id or str(item.get("provider") or "") == "blend"):
                continue
            owner = str(item.get("provider") or self._model_owner(model_id)).strip()
            if owner in owners:
                continue
            owners.add(owner)
            selected.append(route_nine_router_model(model_id))
            if len(selected) >= 12:
                break
        payload = await self._request("GET", "/api/combos")
        combos = payload.get("combos", []) if isinstance(payload, Mapping) else []
        existing = next(
            (
                item
                for item in combos if isinstance(item, Mapping)
                and str(item.get("name") or "") == NINE_ROUTER_DEFAULT_MODEL
            ),
            None,
        )
        if not selected:
            if existing is not None:
                combo_id = self._safe_id(existing.get("id"), "combo_id")
                await self._request(
                    "DELETE", f"/api/combos/{quote(combo_id, safe='')}"
                )
            return False
        if existing is None:
            await self._request(
                "POST",
                "/api/combos",
                {"name": NINE_ROUTER_DEFAULT_MODEL, "models": selected},
            )
            return True
        if list(existing.get("models") or []) == selected:
            return True
        combo_id = self._safe_id(existing.get("id"), "combo_id")
        await self._request(
            "PUT",
            f"/api/combos/{quote(combo_id, safe='')}",
            {"models": selected},
        )
        return True


    @staticmethod
    def _normalize_combo(item: Any) -> dict[str, Any]:
        item = item if isinstance(item, Mapping) else {}
        models = item.get("models")
        return {
            "id": str(item.get("id") or ""),
            "name": str(item.get("name") or ""),
            "kind": str(item.get("kind") or "") if item.get("kind") is not None else "",
            "models": [
                display_nine_router_model(model)
                for model in models
            ] if isinstance(models, list) else [],
            "created_at": str(item.get("createdAt") or ""),
            "updated_at": str(item.get("updatedAt") or ""),
        }


    async def list_combos(self) -> list[dict[str, Any]]:
        payload = await self._request("GET", "/api/combos")
        rows = payload.get("combos", []) if isinstance(payload, Mapping) else []
        return [self._normalize_combo(row) for row in rows if isinstance(row, Mapping)]


    async def create_combo(self, name: str, models: list[str]) -> dict[str, Any]:
        payload = await self._request("POST", "/api/combos", {
            "name": name,
            "models": [route_nine_router_model(model) for model in models],
        })
        combo = payload.get("combo") if isinstance(payload, Mapping) and isinstance(payload.get("combo"), Mapping) else payload
        return self._normalize_combo(combo)


    async def update_combo(
        self, combo_id: Any, *, name: str | None = None, models: list[str] | None = None,
    ) -> dict[str, Any]:
        combo_id = self._safe_id(combo_id, "combo_id")
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if models is not None:
            body["models"] = [route_nine_router_model(model) for model in models]
        payload = await self._request("PUT", f"/api/combos/{quote(combo_id, safe='')}", body)
        combo = payload.get("combo") if isinstance(payload, Mapping) and isinstance(payload.get("combo"), Mapping) else payload
        normalized = self._normalize_combo(combo)
        if not normalized["id"]:
            normalized = next((row for row in await self.list_combos() if row["id"] == combo_id), normalized)
        return normalized


    async def delete_combo(self, combo_id: Any) -> dict[str, Any]:
        combo_id = self._safe_id(combo_id, "combo_id")
        await self._request("DELETE", f"/api/combos/{quote(combo_id, safe='')}")
        return {"id": combo_id, "deleted": True}


    async def combo_settings(self) -> dict[str, Any]:
        """Return only the three combo-related settings keys — never the rest."""
        payload = await self._request("GET", "/api/settings")
        settings = payload if isinstance(payload, Mapping) else {}
        strategies = settings.get("comboStrategies")
        sticky = settings.get("comboStickyRoundRobinLimit")
        return {
            "combo_strategy": str(settings.get("comboStrategy") or "fallback"),
            "combo_strategies": dict(strategies) if isinstance(strategies, Mapping) else {},
            "combo_sticky_limit": int(sticky) if isinstance(sticky, (int, float)) else None,
        }


    async def set_combo_strategy(
        self, name: str, *, strategy: str,
        judge_model: str | None = None,
        fusion_tuning: Mapping[str, Any] | None = None,
    ) -> None:
        # 9router replaces the whole comboStrategies map on PATCH, so read then
        # merge then write the entire map (findings.md §6).
        strategies = dict((await self.combo_settings())["combo_strategies"])
        entry: dict[str, Any] = {"fallbackStrategy": strategy}
        if judge_model is not None:
            entry["judgeModel"] = judge_model
        if fusion_tuning is not None:
            entry["fusionTuning"] = dict(fusion_tuning)
        strategies[name] = entry
        await self._request("PATCH", "/api/settings", {"comboStrategies": strategies})


    async def clear_combo_strategy(self, name: str) -> None:
        strategies = dict((await self.combo_settings())["combo_strategies"])
        if name in strategies:
            strategies.pop(name, None)
            await self._request("PATCH", "/api/settings", {"comboStrategies": strategies})


    async def set_combo_sticky_limit(self, limit: int) -> None:
        await self._request("PATCH", "/api/settings", {"comboStickyRoundRobinLimit": int(limit)})
