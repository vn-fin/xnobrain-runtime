"""Client adapter for 9router providers, models, OAuth, and quotas."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import socket
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiohttp


NINE_ROUTER_PROVIDER_KEY = "nine-router"
NINE_ROUTER_PROVIDER = f"custom:{NINE_ROUTER_PROVIDER_KEY}"
NINE_ROUTER_BASE_URL = "http://127.0.0.1:20128"
NINE_ROUTER_API_BASE_URL = f"{NINE_ROUTER_BASE_URL}/v1"
NINE_ROUTER_KEY_ENV = "NINE_ROUTER_API_KEY"
NINE_ROUTER_DEFAULT_MODEL = "auto"
OPENCODE_ZEN_ROUTER_ALIAS = "ocz"
OPENCODE_ZEN_API_BASE_URL = "https://opencode.ai/zen/v1"

SUPPORTED_ROUTER_PROVIDERS = frozenset({
    "claude", "codex", "antigravity", "openai", "anthropic", "gemini",
    "opencode-go", "opencode",
})
OAUTH_ROUTER_PROVIDERS = frozenset({"claude", "codex", "antigravity"})
API_KEY_ROUTER_PROVIDERS = frozenset({
    "openai", "anthropic", "gemini", "opencode-go",
})
OPENAI_COMPATIBLE_PROVIDERS = frozenset(
    {"deepseek", "moonshot", "qwen", "openai-like"}
)
ROUTER_MODEL_ALIASES = {
    "claude": "cc",
    "codex": "cx",
    "antigravity": "ag",
    "openai": "openai",
    "anthropic": "anthropic",
    "gemini": "gemini",
    "opencode-go": "ocg",
    "opencode": "oc",
}
ROUTER_PROVIDER_BY_MODEL_OWNER = {
    owner: provider for provider, owner in ROUTER_MODEL_ALIASES.items()
}
ROUTER_PROVIDER_BY_MODEL_OWNER[OPENCODE_ZEN_ROUTER_ALIAS] = "opencode"

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
_OAUTH_GET_ACTIONS = frozenset(
    {"authorize", "device-code", "start-proxy", "poll-status", "stop-proxy"}
)
_OAUTH_POST_ACTIONS = frozenset({"exchange", "poll", "manual-code"})
_OPENCODE_FREE_MODEL_IDS = frozenset({"big-pickle"})


class NineRouterAPIError(RuntimeError):
    """Expected local 9Router API failure."""

    def __init__(self, message: str, *, code: str = "nine_router_error", status: int = 502):
        super().__init__(message)
        self.code = code
        self.status = status


def route_nine_router_model(model: Any) -> str:
    model_id = str(model or "").strip()
    if not model_id.startswith("oc/"):
        return model_id
    upstream_id = model_id.removeprefix("oc/")
    if upstream_id.endswith("-free") or upstream_id in _OPENCODE_FREE_MODEL_IDS:
        return model_id
    return f"{OPENCODE_ZEN_ROUTER_ALIAS}/{upstream_id}"


def display_nine_router_model(model: Any) -> str:
    model_id = str(model or "").strip()
    if model_id.startswith(f"{OPENCODE_ZEN_ROUTER_ALIAS}/"):
        return "oc/" + model_id.removeprefix(f"{OPENCODE_ZEN_ROUTER_ALIAS}/")
    return model_id


def normalize_nine_router_config(config: dict[str, Any], model: str | None = None) -> str:
    """Force one Hermes provider while preserving unrelated profile settings."""

    model_config = config.get("model")
    if not isinstance(model_config, dict):
        model_config = {}
        config["model"] = model_config

    selected_model = str(
        model
        or model_config.get("default")
        or model_config.get("model")
        or NINE_ROUTER_DEFAULT_MODEL
    ).strip()
    if not selected_model:
        selected_model = NINE_ROUTER_DEFAULT_MODEL
    selected_model = route_nine_router_model(selected_model)

    model_config["provider"] = NINE_ROUTER_PROVIDER
    model_config["default"] = selected_model
    model_config["base_url"] = NINE_ROUTER_API_BASE_URL
    model_config.pop("model", None)

    config["providers"] = {
        NINE_ROUTER_PROVIDER_KEY: {
            "name": "9Router",
            "api": NINE_ROUTER_API_BASE_URL,
            "api_mode": "chat_completions",
            "default_model": selected_model,
            "model": selected_model,
            "key_env": NINE_ROUTER_KEY_ENV,
            "request_timeout_seconds": 1800,
            "models": {selected_model: {}},
        }
    }
    config.pop("fallback_providers", None)
    return selected_model


class NineRouterManager:
    """Small, filtered facade over the sandbox-local 9Router APIs."""

    def __init__(self, *, base_url: str | None = None, data_dir: str | Path | None = None):
        self.base_url = str(
            base_url or os.environ.get("NINE_ROUTER_URL") or NINE_ROUTER_BASE_URL
        ).rstrip("/")
        self.data_dir = Path(
            data_dir
            or os.environ.get("NINE_ROUTER_DATA_DIR")
            or Path.home() / ".9router"
        )

    async def status(self) -> dict[str, Any]:
        try:
            connections = await self.list_connections()
        except NineRouterAPIError as exc:
            return {
                "object": "nine_router.status",
                "available": False,
                "base_url": self.base_url,
                "error": str(exc),
            }
        return {
            "object": "nine_router.status",
            "available": True,
            "base_url": self.base_url,
            "provider_count": len(connections["connections"]),
        }

    async def list_connections(self) -> dict[str, Any]:
        custom_nodes = await self.list_provider_nodes()
        custom_provider_ids = {
            str(node.get("id") or ""): ROUTER_PROVIDER_BY_MODEL_OWNER.get(
                str(node.get("prefix") or ""),
                str(node.get("prefix") or ""),
            )
            for node in custom_nodes
            if ROUTER_PROVIDER_BY_MODEL_OWNER.get(
                str(node.get("prefix") or ""),
                str(node.get("prefix") or ""),
            ) in OPENAI_COMPATIBLE_PROVIDERS | {"opencode"}
        }
        payload = await self._request("GET", "/api/providers")
        raw_connections = payload.get("connections", []) if isinstance(payload, Mapping) else []
        connections = []
        for item in raw_connections if isinstance(raw_connections, list) else []:
            if not isinstance(item, Mapping):
                continue
            router_provider = str(item.get("provider") or "").strip()
            provider = custom_provider_ids.get(router_provider, router_provider)
            if provider not in SUPPORTED_ROUTER_PROVIDERS | OPENAI_COMPATIBLE_PROVIDERS:
                continue
            connections.append(
                {
                    "id": str(item.get("id") or ""),
                    "provider": provider,
                    "auth_type": str(item.get("authType") or ""),
                    "name": str(item.get("name") or item.get("displayName") or provider),
                    "active": item.get("isActive") is not False,
                    "default_model": str(item.get("defaultModel") or ""),
                    "test_status": str(item.get("testStatus") or "unknown"),
                    "last_error": str(item.get("lastError") or ""),
                    "email": str(item.get("email") or ""),
                    "priority": self._number(item.get("priority")),
                }
            )
        return {"object": "nine_router.providers", "connections": connections}

    async def list_provider_nodes(self) -> list[dict[str, str]]:
        payload = await self._request("GET", "/api/provider-nodes")
        raw_nodes = payload.get("nodes", []) if isinstance(payload, Mapping) else []
        result = []
        for item in raw_nodes if isinstance(raw_nodes, list) else []:
            if not isinstance(item, Mapping):
                continue
            result.append({
                "id": str(item.get("id") or ""),
                "name": str(item.get("name") or ""),
                "prefix": str(item.get("prefix") or ""),
                "type": str(item.get("type") or ""),
                "api_type": str(item.get("apiType") or ""),
                "base_url": str(item.get("baseUrl") or ""),
            })
        return result

    async def ensure_openai_compatible_provider(
        self,
        provider: Any,
        *,
        display_name: Any,
        base_url: Any,
        router_prefix: Any = None,
    ) -> str:
        provider = self._provider(
            provider,
            OPENAI_COMPATIBLE_PROVIDERS | {"opencode"},
        )
        prefix = str(router_prefix or provider).strip()
        normalized_url = str(base_url or "").strip().rstrip("/")
        if not normalized_url.startswith(("https://", "http://")):
            raise NineRouterAPIError(
                "base_url must be an HTTP or HTTPS URL",
                code="invalid_provider_connection",
                status=400,
            )
        name = str(display_name or provider).strip() or provider
        current = next(
            (node for node in await self.list_provider_nodes() if node["prefix"] == prefix),
            None,
        )
        body = {
            "name": name,
            "prefix": prefix,
            "type": "openai-compatible",
            "apiType": "chat",
            "baseUrl": normalized_url,
        }
        if current is None:
            payload = await self._request("POST", "/api/provider-nodes", body)
            node = payload.get("node", {}) if isinstance(payload, Mapping) else {}
            node_id = str(node.get("id") or "") if isinstance(node, Mapping) else ""
        else:
            node_id = current["id"]
            if current["base_url"].rstrip("/") != normalized_url or current["name"] != name:
                payload = await self._request(
                    "PUT", f"/api/provider-nodes/{quote(node_id, safe='')}", body
                )
                node = payload.get("node", {}) if isinstance(payload, Mapping) else {}
                if isinstance(node, Mapping):
                    node_id = str(node.get("id") or node_id)
        return self._safe_id(node_id, "provider_node_id")

    async def ensure_opencode_zen_provider(self) -> str:
        return await self.ensure_openai_compatible_provider(
            "opencode",
            display_name="OpenCode Zen",
            base_url=OPENCODE_ZEN_API_BASE_URL,
            router_prefix=OPENCODE_ZEN_ROUTER_ALIAS,
        )

    async def openai_compatible_provider_id(self, provider: Any) -> str:
        """Resolve a logical compatible-provider name to 9router's node id."""
        provider = self._provider(
            provider,
            OPENAI_COMPATIBLE_PROVIDERS | {"opencode"},
        )
        prefix = (
            OPENCODE_ZEN_ROUTER_ALIAS
            if provider == "opencode"
            else provider
        )
        current = next(
            (node for node in await self.list_provider_nodes() if node["prefix"] == prefix),
            None,
        )
        if current is None:
            raise NineRouterAPIError(
                "provider base URL must be configured first",
                code="invalid_provider_connection",
                status=400,
            )
        return self._safe_id(current["id"], "provider_node_id")

    async def create_api_key_connection(self, body: Mapping[str, Any]) -> dict[str, Any]:
        raw_provider = str(body.get("provider") or "").strip()
        if raw_provider.startswith("openai-compatible-"):
            provider = self._safe_id(raw_provider, "provider")
        else:
            provider = self._provider(raw_provider, API_KEY_ROUTER_PROVIDERS)
        api_key = str(body.get("api_key") or body.get("apiKey") or "").strip()
        if not api_key:
            raise NineRouterAPIError(
                "api_key is required", code="invalid_provider_connection", status=400
            )
        request_body: dict[str, Any] = {
            "provider": provider,
            "apiKey": api_key,
            "name": str(body.get("name") or body.get("display_name") or provider).strip(),
        }
        default_model = str(body.get("default_model") or body.get("defaultModel") or "").strip()
        if default_model:
            request_body["defaultModel"] = default_model
        payload = await self._request("POST", "/api/providers", request_body)
        await self.ensure_auto_combo()
        return self._filtered_connection_response(payload)

    async def delete_connection(self, connection_id: Any) -> dict[str, Any]:
        connection_id = self._safe_id(connection_id, "connection_id")
        await self._request("DELETE", f"/api/providers/{quote(connection_id, safe='')}")
        models = (await self.list_models(ensure_auto=False))["data"]
        await self._ensure_auto_combo(models)
        return {
            "object": "nine_router.provider_delete",
            "id": connection_id,
            "deleted": True,
        }

    async def update_connection(
        self,
        connection_id: Any,
        *,
        active: bool | None = None,
        priority: int | None = None,
    ) -> dict[str, Any]:
        """PUT one connection's isActive/priority. Never touches credentials.

        9router re-normalizes priority (verified: sending 5 stored 1), so the
        caller must read back the stored value rather than trust the request.
        """

        connection_id = self._safe_id(connection_id, "connection_id")
        request_body: dict[str, Any] = {}
        if active is not None:
            request_body["isActive"] = bool(active)
        if priority is not None:
            request_body["priority"] = int(priority)
        if not request_body:
            raise NineRouterAPIError(
                "active or priority is required",
                code="invalid_provider_connection",
                status=400,
            )
        payload = await self._request(
            "PUT", f"/api/providers/{quote(connection_id, safe='')}", request_body
        )
        # Deactivating a provider's last active account can drop its models, so
        # re-ensure the auto combo the same way delete_connection() does.
        models = (await self.list_models(ensure_auto=False))["data"]
        await self._ensure_auto_combo(models)
        if isinstance(payload, Mapping) and isinstance(payload.get("connection"), Mapping):
            return self._filtered_connection_response(payload)
        return {
            "object": "nine_router.provider_update",
            "id": connection_id,
            "updated": True,
        }

    async def test_connection(self, connection_id: Any) -> dict[str, Any]:
        connection_id = self._safe_id(connection_id, "connection_id")
        payload = await self._request(
            "POST", f"/api/providers/{quote(connection_id, safe='')}/test", {}
        )
        if not isinstance(payload, Mapping):
            return {"valid": False}
        return {
            "valid": bool(payload.get("valid")),
            "error": str(payload.get("error") or ""),
        }

    async def oauth(
        self,
        provider: Any,
        action: Any,
        *,
        method: str,
        query_string: str = "",
        body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        provider = self._provider(provider, OAUTH_ROUTER_PROVIDERS)
        action = self._safe_id(action, "action")
        method = method.upper()
        allowed = _OAUTH_GET_ACTIONS if method == "GET" else _OAUTH_POST_ACTIONS
        if action not in allowed:
            raise NineRouterAPIError(
                "unsupported OAuth action", code="invalid_oauth_action", status=400
            )
        path = f"/api/oauth/{quote(provider, safe='')}/{quote(action, safe='')}"
        if query_string:
            path += "?" + query_string
        payload = await self._request(method, path, body if method != "GET" else None)
        if method != "GET" and bool(payload.get("success")):
            await self.ensure_auto_combo()
        return dict(payload)

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
            models.append(
                {
                    "id": public_model_id,
                    "provider": provider,
                    "name": str(item.get("name") or public_model_id),
                }
            )
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

    async def usage(self, model: Any) -> dict[str, Any]:
        """Return filtered quota windows for the provider behind one model."""

        model_id = str(model or "").strip()
        provider = self._provider_for_model(model_id)
        if not provider:
            return self._empty_usage(
                model_id,
                "Usage becomes available after auto selects a provider model.",
            )

        connections = (await self.list_connections())["connections"]
        connection = next(
            (
                item
                for item in connections
                if item.get("provider") == provider and item.get("active") is not False
            ),
            None,
        )
        if connection is None:
            return self._empty_usage(
                model_id,
                "Usage is unavailable because the current model connection is not active.",
                provider=provider,
            )

        connection_id = self._safe_id(connection.get("id"), "connection_id")
        payload = await self._request(
            "GET", f"/api/usage/{quote(connection_id, safe='')}"
        )
        quotas = self._quota_list(payload, provider=provider, model_id=model_id)
        message = str(payload.get("message") or "") if isinstance(payload, Mapping) else ""
        return {
            "object": "router.usage",
            "available": bool(quotas),
            "provider": provider,
            "model": model_id,
            "plan": str(payload.get("plan") or "") if isinstance(payload, Mapping) else "",
            "message": message,
            "quotas": quotas[:6],
        }

    async def usage_for_connection(self, connection_id: Any) -> dict[str, Any]:
        """Account-scoped quota windows for one connection, unfiltered by model."""

        connection_id = self._safe_id(connection_id, "connection_id")
        payload = await self._request(
            "GET", f"/api/usage/{quote(connection_id, safe='')}"
        )
        quotas = self._quota_list(payload)
        is_mapping = isinstance(payload, Mapping)
        return {
            "object": "router.connection_usage",
            "connection_id": connection_id,
            "available": bool(quotas),
            "plan": str(payload.get("plan") or "") if is_mapping else "",
            "message": str(payload.get("message") or "") if is_mapping else "",
            "quotas": quotas[:12],
        }

    def _quota_list(
        self, payload: Any, *, provider: str = "", model_id: str = ""
    ) -> list[dict[str, Any]]:
        """Normalize a 9router usage payload's quota windows.

        When ``model_id`` is given the windows are filtered to that model
        (the ``usage(model)`` view); otherwise every window is returned (the
        account-scoped ``usage_for_connection`` view)."""

        raw_quotas = payload.get("quotas", {}) if isinstance(payload, Mapping) else {}
        quotas: list[dict[str, Any]] = []
        if isinstance(raw_quotas, Mapping):
            for name, raw_quota in raw_quotas.items():
                if not isinstance(raw_quota, Mapping):
                    continue
                quota_name = str(name or "").strip()
                if model_id and not self._quota_matches_model(provider, model_id, quota_name):
                    continue
                quotas.append(self._normalize_quota(quota_name, raw_quota))
        return quotas

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

    # --- public combo (blend) CRUD + strategy settings ------------------------

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

    async def generate_conversation_title(self, message: str, model: str) -> str:
        """Generate one small title through the same local model router."""
        payload = await self._request(
            "POST",
            "/v1/chat/completions",
            {
                "model": model or NINE_ROUTER_DEFAULT_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Write a concise title for this conversation. Use 3 to 7 words and "
                            "at most 48 characters. Return only the title, without quotes, a "
                            "label, or ending punctuation."
                        ),
                    },
                    {"role": "user", "content": str(message)[:4000]},
                ],
                "max_tokens": 48,
                "stream": False,
            },
        )
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            return ""
        response = choices[0].get("message")
        if not isinstance(response, Mapping):
            return ""
        content = response.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(
                str(part.get("text") or "")
                for part in content
                if isinstance(part, Mapping)
            )
        return ""

    async def _request(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not path.startswith(("/api/", "/v1/")):
            raise NineRouterAPIError("invalid 9Router path", status=400)
        timeout = aiohttp.ClientTimeout(total=30)
        try:
            async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
                for attempt in range(2):
                    headers = {"Accept": "application/json"}
                    cli_token = self._cli_token()
                    if cli_token:
                        headers["x-9r-cli-token"] = cli_token
                    async with session.request(
                        method,
                        self.base_url + path,
                        json=dict(body) if body is not None else None,
                        headers=headers,
                    ) as response:
                        try:
                            payload = await response.json(content_type=None)
                        except Exception:
                            payload = {"error": (await response.text()).strip()}
                        if response.status == 401 and not cli_token and attempt == 0:
                            continue
                        if response.status < 200 or response.status >= 300:
                            message = self._error_message(payload) or f"9Router returned HTTP {response.status}"
                            raise NineRouterAPIError(message, status=response.status)
                        return dict(payload) if isinstance(payload, Mapping) else {"data": payload}
        except NineRouterAPIError:
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise NineRouterAPIError(
                "9Router is unavailable", code="nine_router_unavailable", status=503
            ) from exc

    def _filtered_connection_response(self, payload: Any) -> dict[str, Any]:
        connection = payload.get("connection", {}) if isinstance(payload, Mapping) else {}
        if not isinstance(connection, Mapping):
            connection = {}
        return {
            "object": "nine_router.provider",
            "connection": {
                "id": str(connection.get("id") or ""),
                "provider": str(connection.get("provider") or ""),
                "auth_type": str(connection.get("authType") or ""),
                "name": str(connection.get("name") or connection.get("displayName") or ""),
                "active": connection.get("isActive") is not False,
                "default_model": str(connection.get("defaultModel") or ""),
                "email": str(connection.get("email") or ""),
                "priority": self._number(connection.get("priority")),
            },
        }

    def _provider(self, value: Any, allowed: frozenset[str]) -> str:
        provider = str(value or "").strip().lower()
        if provider not in allowed:
            raise NineRouterAPIError(
                "unsupported provider", code="unsupported_provider", status=400
            )
        return provider

    def _provider_for_model(self, model_id: str) -> str:
        if not model_id or model_id == NINE_ROUTER_DEFAULT_MODEL:
            return ""
        owner = model_id.split("/", 1)[0]
        return ROUTER_PROVIDER_BY_MODEL_OWNER.get(owner, "")

    def _empty_usage(
        self,
        model_id: str,
        message: str,
        *,
        provider: str = "",
    ) -> dict[str, Any]:
        return {
            "object": "router.usage",
            "available": False,
            "provider": provider,
            "model": model_id,
            "plan": "",
            "message": message,
            "quotas": [],
        }

    def _quota_matches_model(self, provider: str, model_id: str, name: str) -> bool:
        model_name = model_id.split("/", 1)[-1].lower()
        quota_name = name.lower()
        if provider == "antigravity":
            return quota_name == model_name
        if provider == "codex":
            is_review_model = model_name.endswith("-review")
            is_review_quota = quota_name.startswith("review_")
            return is_review_model == is_review_quota
        if provider == "claude" and quota_name.startswith("weekly "):
            families = ("opus", "sonnet", "haiku")
            quota_family = next((item for item in families if item in quota_name), "")
            return not quota_family or quota_family in model_name
        return True

    def _normalize_quota(
        self,
        name: str,
        quota: Mapping[str, Any],
    ) -> dict[str, Any]:
        used = self._number(quota.get("used"))
        total = self._number(quota.get("total"))
        remaining = self._number(quota.get("remaining"))
        remaining_percent = self._number(quota.get("remainingPercentage"))
        if remaining_percent == 0 and quota.get("remainingPercentage") is None:
            if total > 0:
                remaining_percent = round(max(0, total - used) / total * 100)
            elif quota.get("remaining") is not None and 0 <= remaining <= 100:
                remaining_percent = remaining
        remaining_percent = max(0, min(100, remaining_percent))
        return {
            "name": name,
            "used": used,
            "total": total,
            "remaining_percent": remaining_percent,
            "reset_at": str(quota.get("resetAt") or ""),
            "unlimited": bool(quota.get("unlimited")),
        }

    def _number(self, value: Any) -> int:
        try:
            return round(float(value or 0))
        except (TypeError, ValueError):
            return 0

    def _safe_id(self, value: Any, field: str) -> str:
        normalized = str(value or "").strip()
        if not _SAFE_ID_RE.fullmatch(normalized):
            raise NineRouterAPIError(f"{field} is invalid", status=400)
        return normalized

    def _model_owner(self, model_id: str) -> str:
        return model_id.split("/", 1)[0] if "/" in model_id else NINE_ROUTER_PROVIDER_KEY

    def _cli_token(self) -> str:
        machine_id = self._read_or_create_secret(
            self.data_dir / "machine-id", self._native_machine_id()
        )
        cli_secret = self._read_or_create_secret(
            self.data_dir / "auth" / "cli-secret", secrets.token_hex(32)
        )
        if not machine_id or not cli_secret:
            return ""
        raw = f"{machine_id}9r-cli-auth{cli_secret}".encode()
        return hashlib.sha256(raw).hexdigest()[:16]

    def _native_machine_id(self) -> str:
        value = ""
        for source in (Path("/var/lib/dbus/machine-id"), Path("/etc/machine-id")):
            try:
                value = source.read_text(encoding="utf-8")
            except OSError:
                continue
            if value.strip():
                break
        normalized = "".join(value.split()).lower() or socket.gethostname().lower()
        return hashlib.sha256(normalized.encode()).hexdigest()

    def _read_or_create_secret(self, path: Path, generated: str) -> str:
        try:
            existing = path.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        except OSError:
            pass
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(generated + "\n")
            return generated
        except FileExistsError:
            try:
                return path.read_text(encoding="utf-8").strip()
            except OSError:
                return ""
        except OSError:
            return ""

    def _error_message(self, payload: Any) -> str:
        if not isinstance(payload, Mapping):
            return ""
        error = payload.get("error")
        if isinstance(error, Mapping):
            return str(error.get("message") or error.get("detail") or "").strip()
        return str(error or payload.get("message") or "").strip()
