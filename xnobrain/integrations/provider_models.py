"""Workload-scoped model catalog operations for the centralized LLM router."""

import time

from .llm_router_support import (
    LLM_ROUTER_DEFAULT_MODEL,
    LLM_ROUTER_PROVIDER_KEY,
    ROUTER_PROVIDER_BY_MODEL_OWNER,
    Any,
    Mapping,
)

_REASONING_LEVELS = (
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
    "ultra",
)


def default_reasoning_level(levels: Any) -> str:
    """Choose the model catalog's second effort, or its only effort."""

    ordered = [str(level).strip().lower() for level in levels or [] if str(level).strip()]
    if len(ordered) >= 2:
        return ordered[1]
    return ordered[0] if ordered else "auto"


class ProviderModelsMixin:
    async def list_models(self, *, ensure_auto: bool = True) -> dict[str, Any]:
        # The workload credential scopes this catalog inside the central
        # router. Runtime never calls router management endpoints and does not
        # infer availability from global provider state.
        payload = await self._request("GET", "/models?kind=llm")
        raw_models = payload.get("data", []) if isinstance(payload, Mapping) else []
        models: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in raw_models if isinstance(raw_models, list) else []:
            if not isinstance(item, Mapping):
                continue
            model_id = str(item.get("id") or "").strip()
            if not model_id or model_id == LLM_ROUTER_DEFAULT_MODEL or model_id in seen:
                continue
            # GoRouter's OpenAI envelope uses owned_by="gorouter" for every
            # route. Provider ownership is encoded in the stable public model
            # prefix (for example ocz/, cx/, or anthropic/), so prefer it and
            # use owned_by only for legacy unprefixed model IDs.
            model_owner = self._model_owner(model_id)
            owner = (
                model_owner if "/" in model_id else str(item.get("owned_by") or model_owner).strip()
            )
            provider = ROUTER_PROVIDER_BY_MODEL_OWNER.get(owner, owner)
            seen.add(model_id)
            model: dict[str, Any] = {
                "id": model_id,
                "provider": provider,
                "name": str(item.get("name") or model_id),
            }
            assignments = item.get("xnobrain_assignments")
            if isinstance(assignments, list):
                model["assignments"] = [
                    {
                        "id": str(assignment.get("id") or ""),
                        "owner_type": str(assignment.get("owner_type") or ""),
                        **(
                            {"organization_id": str(assignment.get("organization_id"))}
                            if assignment.get("organization_id")
                            else {}
                        ),
                    }
                    for assignment in assignments
                    if isinstance(assignment, Mapping)
                    and str(assignment.get("id") or "")
                    and assignment.get("owner_type") in {"personal", "organization"}
                ]
            context_length = self._model_context_length(item)
            if context_length is not None:
                model["context_length"] = context_length
            reasoning_levels = self._model_reasoning_levels(item)
            if reasoning_levels:
                model["reasoning_levels"] = reasoning_levels
            models.append(model)
        return {
            "object": "list",
            "provider": LLM_ROUTER_PROVIDER_KEY,
            "default_model": LLM_ROUTER_DEFAULT_MODEL,
            "data": [
                {
                    "id": LLM_ROUTER_DEFAULT_MODEL,
                    "provider": LLM_ROUTER_PROVIDER_KEY,
                    "name": "Auto",
                },
                *models,
            ],
        }

    async def reasoning_for_model(self, model: str) -> dict[str, Any]:
        """Return live reasoning metadata for one public model ID."""

        now = time.monotonic()
        cached = getattr(self, "_model_reasoning_catalog_cache", None)
        if cached and now - cached[0] < 30:
            catalog = cached[1]
        else:
            catalog = {
                str(item.get("id") or ""): list(item.get("reasoning_levels") or [])
                for item in (await self.list_models(ensure_auto=False))["data"]
            }
            self._model_reasoning_catalog_cache = (now, catalog)
        levels = list(catalog.get(str(model or ""), []))
        result = {
            "reasoning": levels,
            "default_reasoning": default_reasoning_level(levels),
        }
        return result

    @staticmethod
    def _model_context_length(item: Mapping[str, Any]) -> int | None:
        capabilities = item.get("capabilities")
        capabilities = capabilities if isinstance(capabilities, Mapping) else {}
        for value in (
            item.get("context_length"),
            item.get("context_window"),
            item.get("max_input_tokens"),
            item.get("inputTokenLimit"),
            capabilities.get("contextWindow"),
        ):
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                return int(value)
        return None

    @staticmethod
    def _model_reasoning_levels(item: Mapping[str, Any]) -> list[str]:
        raw = item.get("reasoning_levels") or item.get("supported_reasoning")
        if isinstance(raw, list):
            values = {str(level).strip().lower() for level in raw}
            return [level for level in _REASONING_LEVELS if level in values]
        raw = item.get("supportedThinkingEfforts")
        if isinstance(raw, list):
            values = {str(level).strip().lower() for level in raw}
            return [level for level in _REASONING_LEVELS if level in values]
        capabilities = item.get("capabilities")
        if (
            item.get("supportsThinking") is True
            or isinstance(capabilities, Mapping)
            and capabilities.get("reasoning") is True
        ):
            return ["low", "medium", "high"]
        return []

    @classmethod
    def _connection_catalog_models(
        cls,
        raw_models: Any,
        *,
        collapse_reasoning_aliases: bool,
    ) -> list[dict[str, Any]]:
        """Normalize a connection catalog without naming specific model families.

        Some subscription providers publish reasoning choices as model aliases such
        as ``model-high``. Collapse only a group with a real base model and either
        multiple effort aliases or explicit reasoning metadata on the base. This
        keeps genuinely distinct models whose names happen to end in ``-max``.
        """

        rows = (
            [dict(item) for item in raw_models if isinstance(item, Mapping)]
            if isinstance(raw_models, list)
            else []
        )
        if not collapse_reasoning_aliases:
            return rows

        by_id = {
            str(item.get("id") or "").strip(): item
            for item in rows
            if str(item.get("id") or "").strip()
        }
        aliases: dict[str, dict[str, str]] = {}
        for model_id in by_id:
            for level in _REASONING_LEVELS:
                suffix = f"-{level}"
                if not model_id.endswith(suffix):
                    continue
                base_id = model_id.removesuffix(suffix)
                if base_id in by_id:
                    aliases.setdefault(base_id, {})[level] = model_id
                break

        collapsible = {
            base_id
            for base_id, variants in aliases.items()
            if len(variants) >= 2 or cls._model_reasoning_levels(by_id[base_id])
        }
        collapsed_ids = {
            alias_id for base_id in collapsible for alias_id in aliases[base_id].values()
        }
        result = []
        for item in rows:
            model_id = str(item.get("id") or "").strip()
            if model_id in collapsed_ids:
                continue
            normalized = dict(item)
            if model_id in collapsible:
                explicit = set(cls._model_reasoning_levels(item))
                explicit.update(aliases[model_id])
                normalized["reasoning_levels"] = [
                    level for level in _REASONING_LEVELS if level in explicit
                ]
            result.append(normalized)
        return result
