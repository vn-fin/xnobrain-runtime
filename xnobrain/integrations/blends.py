"""Runtime-owned personal model blends over the centralized router."""

import json
import os
import re
import tempfile
import time
import uuid

from .llm_router_support import (
    Any,
    Mapping,
    LLM_ROUTER_DEFAULT_MODEL,
    LLMRouterAPIError,
    display_llm_model,
    route_llm_model,
)
from .provider_models import default_reasoning_level


class BlendsIntegrationMixin:
    _SMART_ROUTE_CONFIG_KEY = "xnobrainSmartRoute"
    _NATIVE_COMBO_STRATEGIES = {
        "fallback": "priority",
        "round-robin": "round-robin",
        "fusion": "fusion",
        "smart-route": "priority",
    }

    async def ensure_auto_combo(self) -> None:
        models = (await self.list_models(ensure_auto=False))["data"]
        if not await self._ensure_auto_combo(models):
            raise LLMRouterAPIError(
                "connect at least one provider before using auto",
                code="provider_connection_required",
                status=409,
            )


    async def _ensure_auto_combo(self, models: list[Mapping[str, Any]]) -> bool:
        selected: list[str] = []
        owners: set[str] = set()
        for item in models:
            model_id = str(item.get("id") or "").strip()
            if (not model_id or model_id == LLM_ROUTER_DEFAULT_MODEL
                    or "/" not in model_id or str(item.get("provider") or "") == "blend"):
                continue
            owner = str(item.get("provider") or self._model_owner(model_id)).strip()
            if owner in owners:
                continue
            owners.add(owner)
            selected.append(route_llm_model(model_id))
            if len(selected) >= 12:
                break
        combos = await self.list_combos()
        existing = next(
            (
                item
                for item in combos if isinstance(item, Mapping)
                and str(item.get("name") or "") == LLM_ROUTER_DEFAULT_MODEL
            ),
            None,
        )
        if not selected:
            if existing is not None:
                await self.delete_combo(existing.get("id"))
            return False
        if existing is None:
            await self._create_local_combo(LLM_ROUTER_DEFAULT_MODEL, selected)
            return True
        existing_models = [
            str(model.get("model") or "") if isinstance(model, Mapping) else str(model)
            for model in (existing.get("models") or [])
        ]
        if existing_models == selected:
            return True
        combo_id = self._safe_id(existing.get("id"), "combo_id")
        await self.update_combo(combo_id, models=selected)
        return True


    @staticmethod
    def _normalize_combo(item: Any) -> dict[str, Any]:
        item = item if isinstance(item, Mapping) else {}
        models = item.get("models")
        config = item.get("config")
        return {
            "id": str(item.get("id") or ""),
            "name": str(item.get("name") or ""),
            "kind": str(item.get("kind") or "") if item.get("kind") is not None else "",
            "models": [
                normalized
                for model in models
                if (normalized := display_llm_model(
                    model.get("model") if isinstance(model, Mapping) else model
                ))
            ] if isinstance(models, list) else [],
            "strategy": str(item.get("strategy") or ""),
            "config": dict(config) if isinstance(config, Mapping) else {},
            "created_at": str(item.get("createdAt") or ""),
            "updated_at": str(item.get("updatedAt") or ""),
        }


    async def list_combos(self) -> list[dict[str, Any]]:
        rows = self._read_blend_state().get("combos", [])
        return [self._normalize_combo(row) for row in rows if isinstance(row, Mapping)]


    async def create_combo(self, name: str, models: list[str]) -> dict[str, Any]:
        return await self._create_local_combo(
            name, [route_llm_model(model) for model in models]
        )


    async def _create_local_combo(self, name: str, models: list[str]) -> dict[str, Any]:
        state = self._read_blend_state()
        now = self._blend_timestamp()
        combo = {
            "id": "blend_" + uuid.uuid4().hex,
            "name": name,
            "models": list(models),
            "strategy": "priority",
            "config": {},
            "createdAt": now,
            "updatedAt": now,
        }
        state["combos"].append(combo)
        self._write_blend_state(state)
        return self._normalize_combo(combo)


    async def update_combo(
        self, combo_id: Any, *, name: str | None = None, models: list[str] | None = None,
    ) -> dict[str, Any]:
        combo_id = self._safe_id(combo_id, "combo_id")
        state = self._read_blend_state()
        combo = next(
            (row for row in state["combos"] if str(row.get("id") or "") == combo_id),
            None,
        )
        if combo is None:
            raise LLMRouterAPIError("combo not found", code="combo_not_found", status=404)
        if name is not None:
            combo["name"] = name
        if models is not None:
            combo["models"] = [route_llm_model(model) for model in models]
        combo["updatedAt"] = self._blend_timestamp()
        self._write_blend_state(state)
        return self._normalize_combo(combo)


    async def delete_combo(self, combo_id: Any) -> dict[str, Any]:
        combo_id = self._safe_id(combo_id, "combo_id")
        state = self._read_blend_state()
        before = len(state["combos"])
        state["combos"] = [
            row for row in state["combos"] if str(row.get("id") or "") != combo_id
        ]
        if len(state["combos"]) == before:
            raise LLMRouterAPIError("combo not found", code="combo_not_found", status=404)
        self._write_blend_state(state)
        return {"id": combo_id, "deleted": True}


    async def combo_settings(self) -> dict[str, Any]:
        """Return the service's combo view without exposing unrelated settings."""
        settings = self._read_blend_state()
        sticky = settings.get("stickyLimit")
        combo_strategies: dict[str, Any] = {}

        # Runtime stores strategy and routing config on each local blend. Keep
        # the legacy settings map as a compatibility fallback for older profiles.
        for combo in await self.list_combos():
            native = str(combo.get("strategy") or "")
            config = combo.get("config")
            config = config if isinstance(config, Mapping) else {}
            smart = config.get(self._SMART_ROUTE_CONFIG_KEY)
            if not native and not config:
                continue
            entry: dict[str, Any] = {
                "fallbackStrategy": {
                    "priority": "fallback",
                    "round-robin": "round-robin",
                    "fusion": "fusion",
                }.get(native, "fallback"),
            }
            if config.get("judgeModel") is not None:
                entry["judgeModel"] = config["judgeModel"]
            if isinstance(config.get("fusionTuning"), Mapping):
                entry["fusionTuning"] = dict(config["fusionTuning"])
            if isinstance(smart, Mapping):
                entry["smartRoute"] = dict(smart)
            combo_strategies[str(combo.get("name") or "")] = entry
        return {
            "combo_strategy": "fallback",
            "combo_strategies": combo_strategies,
            "combo_sticky_limit": int(sticky) if isinstance(sticky, (int, float)) else None,
        }


    async def set_combo_strategy(
        self, name: str, *, strategy: str,
        judge_model: str | None = None,
        fusion_tuning: Mapping[str, Any] | None = None,
        smart_route: Mapping[str, Any] | None = None,
    ) -> None:
        combo = next((row for row in await self.list_combos() if row["name"] == name), None)
        if combo is None:
            raise LLMRouterAPIError("combo not found", code="combo_not_found", status=404)
        config = dict(combo.get("config") or {})
        config.pop(self._SMART_ROUTE_CONFIG_KEY, None)
        config.pop("judgeModel", None)
        config.pop("fusionTuning", None)
        if judge_model is not None:
            config["judgeModel"] = route_llm_model(judge_model)
        if fusion_tuning is not None:
            config["fusionTuning"] = dict(fusion_tuning)
        if smart_route is not None:
            config[self._SMART_ROUTE_CONFIG_KEY] = dict(smart_route)
        native = self._NATIVE_COMBO_STRATEGIES.get(strategy)
        if native is None:
            raise LLMRouterAPIError("unsupported combo strategy", status=400)
        state = self._read_blend_state()
        stored = next(
            (row for row in state["combos"] if str(row.get("id") or "") == combo["id"]),
            None,
        )
        if stored is None:
            raise LLMRouterAPIError("combo not found", code="combo_not_found", status=404)
        stored["strategy"] = native
        stored["config"] = config
        stored["updatedAt"] = self._blend_timestamp()
        self._write_blend_state(state)


    async def resolve_smart_route(
        self,
        name: str,
        message: str,
        *,
        required_context_tokens: int = 0,
    ) -> dict[str, Any] | None:
        """Resolve a XNOBrain Smart Route blend to one concrete model."""
        settings = await self.combo_settings()
        strategies = settings.get("combo_strategies") or {}
        entry = strategies.get(name) if isinstance(strategies, Mapping) else None
        smart = entry.get("smartRoute") if isinstance(entry, Mapping) else None
        if not isinstance(smart, Mapping):
            return None

        groups = {
            tier: self._eligible_smart_models(
                smart.get(tier), required_context_tokens=required_context_tokens
            )
            for tier in ("quick", "normal", "difficult")
        }
        available_tiers = [tier for tier, rows in groups.items() if rows]
        if not available_tiers:
            raise LLMRouterAPIError(
                "no Smart Route model can fit this conversation",
                code="smart_route_context_unavailable",
                status=409,
            )

        uncertain = str(smart.get("uncertainTier") or "difficult")
        if uncertain not in {"normal", "difficult"}:
            uncertain = "difficult"
        tier = self._obvious_smart_route_tier(message)
        if tier is None:
            tier = available_tiers[0] if len(available_tiers) == 1 else uncertain
            # A reasoning model can fail to produce the one-word classifier
            # response, or one provider can be temporarily unavailable. Try
            # the configured models in increasing task-cost order before
            # falling back to the user's uncertain-task group.
            classifiers: list[str] = []
            for candidate in ("quick", "normal", "difficult"):
                for row in groups[candidate]:
                    model = str(row["model"])
                    if model not in classifiers:
                        classifiers.append(model)
            for classifier in classifiers:
                try:
                    tier = await self._classify_smart_route(message, classifier)
                    break
                except LLMRouterAPIError:
                    continue

        escalation = {
            "quick": ("quick", "normal", "difficult"),
            "normal": ("normal", "difficult", "quick"),
            "difficult": ("difficult", "normal", "quick"),
        }[tier]
        selected_tier = next(candidate for candidate in escalation if groups[candidate])
        selected = groups[selected_tier][0]
        reasoning = str(selected.get("reasoning") or "auto")
        if reasoning == "auto":
            try:
                live = await self.reasoning_for_model(str(selected["model"]))
            except LLMRouterAPIError:
                live = {}
            supported = live.get("reasoning") or selected.get("reasoning_levels") or []
            reasoning = default_reasoning_level(supported)
        return {
            "model": route_llm_model(selected["model"]),
            "reasoning": reasoning,
            "tier": selected_tier,
            "route": name,
            "context_length": selected.get("context_length"),
        }


    async def resolve_blend_route(
        self,
        name: str,
        message: str,
        *,
        required_context_tokens: int = 0,
    ) -> dict[str, Any] | None:
        """Resolve a runtime-owned blend to a concrete router model.

        Smart Route keeps its classifier behavior. Fallback and round-robin
        select locally so the workload never needs router-management access.
        Fusion uses its configured judge for the Hermes agent loop; candidate
        fan-out remains an execution concern and is never persisted centrally.
        """
        combo = next((row for row in await self.list_combos() if row["name"] == name), None)
        if combo is None:
            return None
        config = combo.get("config") if isinstance(combo.get("config"), Mapping) else {}
        if isinstance(config.get(self._SMART_ROUTE_CONFIG_KEY), Mapping):
            return await self.resolve_smart_route(
                name, message, required_context_tokens=required_context_tokens
            )
        models = [str(model) for model in combo.get("models", []) if str(model)]
        if not models:
            raise LLMRouterAPIError(
                "blend has no available models", code="blend_model_unavailable", status=409
            )
        native = str(combo.get("strategy") or "priority")
        if native == "round-robin":
            state = self._read_blend_state()
            counters = state.get("roundRobin")
            counters = dict(counters) if isinstance(counters, Mapping) else {}
            index = int(counters.get(str(combo["id"])) or 0)
            selected = models[index % len(models)]
            counters[str(combo["id"])] = index + 1
            state["roundRobin"] = counters
            self._write_blend_state(state)
        elif native == "fusion" and str(config.get("judgeModel") or ""):
            selected = str(config["judgeModel"])
        else:
            selected = models[0]
        return {
            "model": route_llm_model(selected),
            "reasoning": "auto",
            "tier": "",
            "route": name,
            "context_length": None,
        }


    @staticmethod
    def _eligible_smart_models(
        raw: Any,
        *,
        required_context_tokens: int,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, Mapping):
                continue
            model = str(item.get("model") or "").strip()
            if not model:
                continue
            context = item.get("context_length")
            context_length = int(context) if isinstance(context, (int, float)) and context > 0 else None
            if context_length is not None and required_context_tokens > context_length:
                continue
            rows.append({
                "model": model,
                "reasoning": str(item.get("reasoning") or "auto"),
                "context_length": context_length,
                "reasoning_levels": [
                    str(level) for level in (item.get("reasoning_levels") or [])
                    if str(level) in {
                        "none", "minimal", "low", "medium", "high",
                        "xhigh", "max", "ultra",
                    }
                ],
            })
        return rows


    async def _classify_smart_route(self, message: str, classifier_model: str) -> str:
        payload = await self._request(
            "POST",
            "/chat/completions",
            {
                "model": route_llm_model(classifier_model),
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Classify the user's task as quick, normal, or difficult. "
                            "Quick means formatting, summarizing, lookup, or a short direct answer. "
                            "Normal means ordinary analysis, writing, or contained coding. "
                            "Difficult means architecture, security, multi-file coding, advanced math, "
                            "or long multi-step reasoning. Return exactly one word: quick, normal, or difficult."
                        ),
                    },
                    {"role": "user", "content": str(message)[:6000]},
                ],
                # Some reasoning models spend completion tokens before
                # emitting visible content. Eight tokens can therefore return
                # an empty answer with finish_reason=length.
                "max_tokens": 32,
                "reasoning_effort": "low",
                "stream": False,
            },
        )
        choices = payload.get("choices")
        response = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], Mapping) else None
        content = str(response.get("content") or "") if isinstance(response, Mapping) else ""
        match = re.search(r"\b(quick|normal|difficult)\b", content.lower())
        if match is None:
            raise LLMRouterAPIError("Smart Route classifier returned an invalid result")
        return match.group(1)


    @staticmethod
    def _obvious_smart_route_tier(message: str) -> str | None:
        """Avoid a model call for greetings and other unambiguous pleasantries."""
        normalized = re.sub(r"[^\w\s]", " ", str(message).lower(), flags=re.UNICODE)
        words = normalized.split()
        if not words or len(words) > 6:
            return None
        greetings = {
            "hi", "hello", "hey", "yo", "thanks", "thank", "bye",
            "chao", "chào", "xin", "cam", "cảm", "ơn", "on",
        }
        if words[0] in greetings:
            return "quick"
        if words[:2] in (["good", "morning"], ["good", "afternoon"], ["good", "evening"]):
            return "quick"
        return None


    async def clear_combo_strategy(self, name: str) -> None:
        combo = next((row for row in await self.list_combos() if row["name"] == name), None)
        if combo is None:
            return
        config = dict(combo.get("config") or {})
        config.pop(self._SMART_ROUTE_CONFIG_KEY, None)
        config.pop("judgeModel", None)
        config.pop("fusionTuning", None)
        state = self._read_blend_state()
        stored = next(
            (row for row in state["combos"] if str(row.get("id") or "") == combo["id"]),
            None,
        )
        if stored is not None:
            stored["strategy"] = "priority"
            stored["config"] = config
            stored["updatedAt"] = self._blend_timestamp()
            self._write_blend_state(state)


    async def set_combo_sticky_limit(self, limit: int) -> None:
        state = self._read_blend_state()
        state["stickyLimit"] = int(limit)
        self._write_blend_state(state)


    def _blend_state_path(self):
        return self.data_dir / "blends.json"


    def _read_blend_state(self) -> dict[str, Any]:
        try:
            payload = json.loads(self._blend_state_path().read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            payload = {}
        combos = payload.get("combos") if isinstance(payload, Mapping) else None
        return {
            "version": 1,
            "stickyLimit": payload.get("stickyLimit") if isinstance(payload, Mapping) else None,
            "roundRobin": payload.get("roundRobin") if isinstance(payload, Mapping) else {},
            "combos": [dict(row) for row in combos if isinstance(row, Mapping)]
            if isinstance(combos, list) else [],
        }


    def _write_blend_state(self, state: Mapping[str, Any]) -> None:
        path = self._blend_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(dict(state), separators=(",", ":"), sort_keys=True).encode("utf-8")
        descriptor, temporary = tempfile.mkstemp(prefix=".blends.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


    @staticmethod
    def _blend_timestamp() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
