"""Model Blends — user-named 9router combos exposed as one virtual model.

Pure proxy: 9router's SQLite is the source of truth for combos and per-combo
strategies. Brain4All stores nothing. This service owns the policy layer (name
guards, ``auto`` read-only, strategy validation, DTO shaping); the adapter owns
the HTTP translation to ``/api/combos*`` and ``/api/settings``.
"""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from ..integrations.nine_router import NINE_ROUTER_DEFAULT_MODEL, NINE_ROUTER_PROVIDER_KEY
from .base import ServiceError


class BlendService:
    """Rules for user-named model blends over 9router combos."""

    RESERVED = NINE_ROUTER_DEFAULT_MODEL  # "auto"
    NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    MAX_MODELS = 24
    STRATEGIES = ("fallback", "round-robin", "fusion")

    def __init__(self, router: Any):
        self.router = router

    # ---- public API ---------------------------------------------------------

    async def list_blends(self) -> dict[str, Any]:
        combos = await self.router.list_combos()
        settings = await self.router.combo_settings()
        blends = [self._dto(combo, settings) for combo in combos]
        blends.sort(key=lambda blend: (0 if blend["system"] else 1, blend["name"].lower()))
        return {"blends": blends}

    async def available_models(self) -> dict[str, Any]:
        data = (await self.router.list_models())["data"]
        models = [
            {"id": item["id"], "provider": item.get("provider", ""),
             "name": item.get("name", item["id"])}
            for item in data
            if item.get("provider") not in {"blend", NINE_ROUTER_PROVIDER_KEY}
            and item.get("id") != NINE_ROUTER_DEFAULT_MODEL
        ]
        return {"data": models}

    async def create_blend(self, body: Mapping[str, Any]) -> dict[str, Any]:
        name = str(body.get("name") or "").strip()
        models = [str(model) for model in (body.get("models") or [])]
        strategy = str(body.get("strategy") or "fallback")
        judge = body.get("judge_model")
        sticky = body.get("sticky_limit")

        self._validate_name(name)
        available = await self._available_ids()
        self._check_name_free(name, [combo["name"] for combo in await self.router.list_combos()], available)
        self._validate_models(models, available)
        self._validate_strategy(strategy, models, judge, sticky, available)

        combo = await self.router.create_combo(name, models)
        try:
            if strategy != "fallback":
                await self.router.set_combo_strategy(
                    name, strategy=strategy,
                    judge_model=judge if strategy == "fusion" else None)
            if strategy == "round-robin" and sticky is not None:
                await self.router.set_combo_sticky_limit(int(sticky))
        except Exception:
            # Compensate a partial create so we never leave a combo with no strategy.
            try:
                await self.router.delete_combo(combo["id"])
            except Exception:
                pass
            raise
        settings = await self.router.combo_settings()
        return self._dto(combo, settings)

    async def update_blend(self, blend_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        combos = await self.router.list_combos()
        combo = next((item for item in combos if item["id"] == blend_id), None)
        if combo is None:
            raise ServiceError("blend not found", status=404, code="blend_not_found")
        self._reject_reserved(combo["name"])
        old_name = combo["name"]

        new_name = body.get("name")
        new_models = None if body.get("models") is None else [str(m) for m in body["models"]]
        strategy = body.get("strategy")
        judge = body.get("judge_model")
        sticky = body.get("sticky_limit")

        available = await self._available_ids()
        settings = await self.router.combo_settings()
        current = self._dto(combo, settings)
        eff_strategy = str(strategy) if strategy is not None else current["strategy"]
        eff_judge = judge if judge is not None else current["judge_model"]

        if new_name is not None and new_name != old_name:
            self._validate_name(new_name)
            self._check_name_free(
                new_name, [c["name"] for c in combos if c["id"] != blend_id], available)
        if new_models is not None:
            self._validate_models(new_models, available)
        target_models = new_models if new_models is not None else combo["models"]
        if strategy is not None or judge is not None or sticky is not None:
            self._validate_strategy(eff_strategy, target_models, eff_judge, sticky, available)

        if new_name is not None or new_models is not None:
            combo = await self.router.update_combo(blend_id, name=new_name, models=new_models)
        final_name = combo["name"]

        if new_name is not None and new_name != old_name:
            await self.router.clear_combo_strategy(old_name)
        if strategy is not None or judge is not None or (new_name is not None and new_name != old_name):
            if eff_strategy == "fallback":
                await self.router.clear_combo_strategy(final_name)
            else:
                await self.router.set_combo_strategy(
                    final_name, strategy=eff_strategy,
                    judge_model=eff_judge if eff_strategy == "fusion" else None)
        if sticky is not None and eff_strategy == "round-robin":
            await self.router.set_combo_sticky_limit(int(sticky))

        settings = await self.router.combo_settings()
        return self._dto(combo, settings)

    async def delete_blend(self, blend_id: str) -> dict[str, Any]:
        combos = await self.router.list_combos()
        combo = next((item for item in combos if item["id"] == blend_id), None)
        if combo is None:
            raise ServiceError("blend not found", status=404, code="blend_not_found")
        self._reject_reserved(combo["name"])
        await self.router.delete_combo(blend_id)
        try:
            await self.router.clear_combo_strategy(combo["name"])
        except Exception:
            pass
        return {"id": blend_id, "name": combo["name"], "deleted": True}

    # ---- internals ----------------------------------------------------------

    def _dto(self, combo: Mapping[str, Any], settings: Mapping[str, Any]) -> dict[str, Any]:
        name = str(combo.get("name") or "")
        is_auto = name == self.RESERVED
        strategies = settings.get("combo_strategies") or {}
        entry = strategies.get(name) if isinstance(strategies, Mapping) else None
        entry = entry if isinstance(entry, Mapping) else {}
        strategy = str(entry.get("fallbackStrategy") or settings.get("combo_strategy") or "fallback")
        if strategy not in self.STRATEGIES:
            strategy = "fallback"
        return {
            "id": str(combo.get("id") or ""),
            "name": name,
            "display_name": "Auto" if is_auto else name,
            "system": is_auto,
            "read_only": is_auto,
            "models": list(combo.get("models") or []),
            "strategy": strategy,
            "judge_model": entry.get("judgeModel") if strategy == "fusion" else None,
            "sticky_limit": settings.get("combo_sticky_limit") if strategy == "round-robin" else None,
            "sticky_limit_scope": "global",
            "created_at": str(combo.get("created_at") or ""),
            "updated_at": str(combo.get("updated_at") or ""),
        }

    async def _available_ids(self) -> set[str]:
        return {item["id"] for item in (await self.available_models())["data"]}

    def _reject_reserved(self, name: str) -> None:
        if name == self.RESERVED:
            raise ServiceError(
                "the Auto blend is system-managed", status=403, code="blend_reserved")

    def _validate_name(self, name: str) -> None:
        if name.lower() == self.RESERVED:
            raise ServiceError(
                "the Auto blend is system-managed", status=403, code="blend_reserved")
        if not self.NAME_RE.match(name):
            raise ServiceError(
                "blend name may use letters, digits, '.', '_', '-' (max 64)",
                status=400, code="invalid_blend_name")

    def _check_name_free(self, name: str, existing_names: list[str], available: set[str]) -> None:
        lowered = name.lower()
        if any(str(existing).lower() == lowered for existing in existing_names):
            raise ServiceError(
                "a blend with that name already exists", status=409, code="blend_name_conflict")
        if name in available:
            raise ServiceError(
                "that name matches an existing model id", status=409, code="blend_name_conflict")

    def _validate_models(self, models: list[str], available: set[str]) -> None:
        if not 1 <= len(models) <= self.MAX_MODELS:
            raise ServiceError(
                f"a blend needs 1–{self.MAX_MODELS} models", status=400, code="invalid_blend")
        if len(set(models)) != len(models):
            raise ServiceError("a blend cannot repeat a model", status=400, code="invalid_blend")
        unknown = [model for model in models if model not in available]
        if unknown:
            raise ServiceError(
                f"unknown model(s): {', '.join(unknown)}", status=400, code="invalid_blend")

    def _validate_strategy(
        self, strategy: str, models: list[str], judge: Any, sticky: Any, available: set[str],
    ) -> None:
        if strategy not in self.STRATEGIES:
            raise ServiceError("unknown blend strategy", status=400, code="invalid_blend")
        if strategy == "fusion":
            if len(models) < 2:
                raise ServiceError("fusion needs at least two models", status=400, code="invalid_blend")
            if not judge or str(judge) not in available:
                raise ServiceError(
                    "fusion needs a judge model from the available models",
                    status=400, code="invalid_blend")
        elif judge:
            raise ServiceError(
                "judge model applies only to fusion", status=400, code="invalid_blend")
        if sticky is not None and strategy != "round-robin":
            raise ServiceError(
                "sticky limit applies only to round-robin", status=400, code="invalid_blend")
