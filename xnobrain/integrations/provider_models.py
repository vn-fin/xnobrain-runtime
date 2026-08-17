"""Provider model operations backed by OmniRoute."""

from .nine_router_support import (
    Any,
    Mapping,
    OMNIROUTE_DEFAULT_MODEL,
    OMNIROUTE_PROVIDER_KEY,
    NineRouterAPIError,
    OPENAI_COMPATIBLE_PROVIDERS,
    ROUTER_MODEL_ALIASES,
    ROUTER_PROVIDER_BY_MODEL_OWNER,
)


class ProviderModelsMixin:
    async def list_models(self, *, ensure_auto: bool = True) -> dict[str, Any]:
        connection_payload = await self.list_connections()
        connections = connection_payload["connections"]
        provider_nodes = {
            str(node.get("id") or ""): node
            for node in await self.list_provider_nodes()
            if str(node.get("id") or "")
        }
        connected_providers = {
            str(item.get("provider") or "")
            for item in connections
            if item.get("active") is not False
            and str(item.get("test_status") or "").lower() != "error"
        }
        codex_review_available = await self._codex_review_available(connections)
        active_owners = {
            ROUTER_MODEL_ALIASES[provider]
            for provider in connected_providers
            if provider in ROUTER_MODEL_ALIASES and provider != "opencode"
        }
        # OpenCode's global catalog contains free ``oc/*`` models and other
        # entries that are not tied to the user's Zen API key. OpenCode models
        # are therefore supplied exclusively by the connection-specific
        # catalog below.
        active_owners.update(
            provider for provider in connected_providers
            if provider in OPENAI_COMPATIBLE_PROVIDERS
        )
        payload = await self._request("GET", "/v1/models?kind=llm")
        raw_models = payload.get("data", []) if isinstance(payload, Mapping) else []
        models: list[dict[str, Any]] = []
        blends: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in raw_models if isinstance(raw_models, list) else []:
            if not isinstance(item, Mapping):
                continue
            model_id = str(item.get("id") or "").strip()
            if not model_id or model_id == OMNIROUTE_DEFAULT_MODEL or model_id in seen:
                continue
            # OmniRoute exposes custom nodes under both their configured public
            # prefix (for example ``ocz/model``) and an implementation ID such
            # as ``openai-compatible-chat-<uuid>/model``. Only the stable public
            # prefix belongs in XNOBrain's provider/model contract.
            if model_id.startswith("openai-compatible-"):
                continue
            if any(f"/{node_id}/" in model_id for node_id in provider_nodes):
                # OmniRoute can add derived aliases such as
                # ``no-think/<node-id>/model``. The canonical connection model
                # endpoint below supplies one stable public entry instead.
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
            if (
                provider == "codex"
                and public_model_id.lower().endswith("-review")
                and not codex_review_available
            ):
                continue
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
        for connection in connections:
            logical_provider = str(connection.get("provider") or "")
            if (
                logical_provider not in OPENAI_COMPATIBLE_PROVIDERS | {"opencode"}
                or connection.get("active") is False
                or str(connection.get("test_status") or "").lower() == "error"
            ):
                continue
            try:
                connection_catalog = await self.models_for_connection(
                    connection.get("id")
                )
            except NineRouterAPIError:
                continue
            node = provider_nodes.get(str(connection_catalog.get("provider") or ""))
            if node is None:
                continue
            prefix = str(node.get("prefix") or "").strip()
            provider = ROUTER_PROVIDER_BY_MODEL_OWNER.get(prefix, prefix)
            if not prefix or provider != logical_provider:
                continue
            for item in connection_catalog.get("models", []):
                model_id = str(item.get("id") or "").strip()
                if not model_id:
                    continue
                public_model_id = f"{prefix}/{model_id}"
                if public_model_id in seen:
                    continue
                seen.add(public_model_id)
                model = {
                    "id": public_model_id,
                    "provider": provider,
                    "name": str(item.get("name") or model_id),
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
        return {
            "object": "list",
            "provider": OMNIROUTE_PROVIDER_KEY,
            "default_model": OMNIROUTE_DEFAULT_MODEL,
            "data": [
                {
                    "id": OMNIROUTE_DEFAULT_MODEL,
                    "provider": OMNIROUTE_PROVIDER_KEY,
                    "name": "Auto",
                },
                *blends,
                *models,
            ],
        }


    async def _codex_review_available(self, connections: list[dict[str, Any]]) -> bool:
        """Report whether an active Codex account exposes review quota."""

        for connection in connections:
            if (
                connection.get("provider") != "codex"
                or connection.get("active") is False
            ):
                continue
            try:
                usage = await self.usage_for_connection(connection.get("id"))
            except NineRouterAPIError:
                # The generic catalog includes review-only aliases even when the
                # account cannot use them. Do not surface one unless entitlement
                # can be confirmed from the account quota response.
                continue
            if any(
                str(quota.get("name") or "").lower().startswith("review_")
                for quota in usage.get("quotas", [])
            ):
                return True
        return False


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
        allowed = {"low", "medium", "high"}
        if isinstance(raw, list):
            return [str(level) for level in raw if str(level) in allowed]
        raw = item.get("supportedThinkingEfforts")
        if isinstance(raw, list):
            return [str(level) for level in raw if str(level) in allowed]
        capabilities = item.get("capabilities")
        if (
            item.get("supportsThinking") is True
            or isinstance(capabilities, Mapping) and capabilities.get("reasoning") is True
        ):
            return ["low", "medium", "high"]
        return []
