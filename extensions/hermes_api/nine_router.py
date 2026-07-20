"""OSS 9Router client and provider configuration contract."""

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

SUPPORTED_ROUTER_PROVIDERS = frozenset(
    {"claude", "codex", "antigravity", "openai", "anthropic", "gemini"}
)
OAUTH_ROUTER_PROVIDERS = frozenset({"claude", "codex", "antigravity"})
API_KEY_ROUTER_PROVIDERS = frozenset({"openai", "anthropic", "gemini"})
ROUTER_MODEL_ALIASES = {
    "claude": "cc",
    "codex": "cx",
    "antigravity": "ag",
    "openai": "openai",
    "anthropic": "anthropic",
    "gemini": "gemini",
}
ROUTER_PROVIDER_BY_MODEL_OWNER = {
    owner: provider for provider, owner in ROUTER_MODEL_ALIASES.items()
}

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
_OAUTH_GET_ACTIONS = frozenset(
    {"authorize", "device-code", "start-proxy", "poll-status", "stop-proxy"}
)
_OAUTH_POST_ACTIONS = frozenset({"exchange", "poll", "manual-code"})


class NineRouterAPIError(RuntimeError):
    """Expected local 9Router API failure."""

    def __init__(self, message: str, *, code: str = "nine_router_error", status: int = 502):
        super().__init__(message)
        self.code = code
        self.status = status


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
        payload = await self._request("GET", "/api/providers")
        raw_connections = payload.get("connections", []) if isinstance(payload, Mapping) else []
        connections = []
        for item in raw_connections if isinstance(raw_connections, list) else []:
            if not isinstance(item, Mapping):
                continue
            provider = str(item.get("provider") or "").strip()
            if provider not in SUPPORTED_ROUTER_PROVIDERS:
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
                }
            )
        return {"object": "nine_router.providers", "connections": connections}

    async def create_api_key_connection(self, body: Mapping[str, Any]) -> dict[str, Any]:
        provider = self._provider(body.get("provider"), API_KEY_ROUTER_PROVIDERS)
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
        payload = await self._request("GET", "/v1/models?kind=llm")
        raw_models = payload.get("data", []) if isinstance(payload, Mapping) else []
        models: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in raw_models if isinstance(raw_models, list) else []:
            if not isinstance(item, Mapping):
                continue
            model_id = str(item.get("id") or "").strip()
            if not model_id or model_id == NINE_ROUTER_DEFAULT_MODEL or model_id in seen:
                continue
            owner = str(item.get("owned_by") or self._model_owner(model_id)).strip()
            if owner not in active_owners:
                continue
            seen.add(model_id)
            provider = next(
                (
                    provider_id
                    for provider_id, alias in ROUTER_MODEL_ALIASES.items()
                    if alias == owner
                ),
                owner,
            )
            models.append(
                {
                    "id": model_id,
                    "provider": provider,
                    "name": str(item.get("name") or model_id),
                }
            )
        if ensure_auto:
            await self._ensure_auto_combo(models)
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
                *models,
            ],
        }

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
        raw_quotas = payload.get("quotas", {}) if isinstance(payload, Mapping) else {}
        quotas: list[dict[str, Any]] = []
        if isinstance(raw_quotas, Mapping):
            for name, raw_quota in raw_quotas.items():
                if not isinstance(raw_quota, Mapping):
                    continue
                quota_name = str(name or "").strip()
                if not self._quota_matches_model(provider, model_id, quota_name):
                    continue
                quotas.append(self._normalize_quota(quota_name, raw_quota))

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
            if not model_id or model_id == NINE_ROUTER_DEFAULT_MODEL or "/" not in model_id:
                continue
            owner = str(item.get("provider") or self._model_owner(model_id)).strip()
            if owner in owners:
                continue
            owners.add(owner)
            selected.append(model_id)
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
