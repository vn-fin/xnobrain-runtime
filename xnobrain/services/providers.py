"""Provider connection and model routing service behavior."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import hashlib
import json
import logging
import os
from pathlib import Path
import time
from typing import Any, Mapping
import uuid

import yaml

from ..defaults import (
    BIG_BROTHER_APPROVAL_DEFAULT_MARKER,
    BIG_BROTHER_AGENT_ID,
    BIG_BROTHER_DESCRIPTION,
    BIG_BROTHER_DISPLAY_NAME,
    BIG_BROTHER_MODEL_DEFAULT_MARKER,
    BIG_BROTHER_NATIVE_TOOLSETS,
    BIG_BROTHER_SKILL_CATEGORY,
    BIG_BROTHER_SKILL_ID,
    CUSTOM_SKILL_CATEGORY,
    DEFAULT_PROFILE_MODEL,
    LEGACY_BIG_BROTHER_TOOLSET,
)
from ..integrations import AgentAPIError, ConfigAPIError, NineRouterAPIError
from ..integrations.provider_models import default_reasoning_level
from ..repositories import StoreError
from .base import ServiceError, iso, utc_now
from .constants import (
    API_KEY_PROVIDERS,
    DEVICE_CODE_PROVIDERS,
    DEFAULT_TEAM_COORDINATOR_PROMPT,
    DEFAULT_TEAM_SYNTHESIS_PROMPT,
    EVERY_SCHEDULE,
    IMPORT_TOKEN_PROVIDERS,
    NO_AUTH_PROVIDERS,
    OPENAI_COMPATIBLE_PROVIDER_DEFINITIONS,
    PROVIDER_DEFINITIONS,
    SAFE_TOOLSETS,
    SUPPORTED_PROVIDERS,
)
from .cron import CronServiceError
from .helpers import cached_method
from .workspace_preview import WorkspacePreview, WorkspacePreviewError
from .workspace_upload import WorkspaceUploadError

class ProvidersServiceMixin:
    async def providers(self) -> list[dict[str, Any]]:
        providers, _router_available = await self._load_providers()
        return providers

    @cached_method("providers", cache_when=lambda result: result[1])
    async def _load_providers(self) -> tuple[list[dict[str, Any]], bool]:
        try:
            connections = (await self.router.list_connections())["connections"]
            models = (await self.router.list_models())["data"]
            router_available = True
        except NineRouterAPIError:
            connections, models = [], []
            router_available = False
        result = []
        for provider in SUPPORTED_PROVIDERS:
            definition = PROVIDER_DEFINITIONS.get(provider, {})
            no_auth = provider in NO_AUTH_PROVIDERS
            provider_rows = sorted(
                (item for item in connections if item.get("provider") == provider),
                key=lambda item: (int(item.get("priority") or 0), str(item.get("name") or "")),
            )
            active_rows = [item for item in provider_rows if item.get("active") is not False]
            primary = active_rows[0] if active_rows else (provider_rows[0] if provider_rows else {})
            result.append({
                "id": provider,
                "display_name": definition.get("display_name", provider.title()),
                "provider_type": provider,
                "description": definition.get(
                    "description", "Credentials are managed by the local provider runtime."
                ),
                "connection_mode": (
                    "no-auth" if no_auth
                    else "api-key" if provider in API_KEY_PROVIDERS
                    else "device-code" if provider in DEVICE_CODE_PROVIDERS
                    else "cli"
                ),
                "base_url": definition.get("base_url", ""),
                "requires_base_url": provider == "openai-like",
                "connected": bool(active_rows),
                "status": (
                    "unavailable" if not router_available
                    else "available" if no_auth
                    else "connected" if active_rows
                    else "disconnected"
                ),
                "last_test_status": primary.get("test_status", "unknown"),
                "default_model": primary.get("default_model", ""),
                "connection_count": len(provider_rows),
                "available_models": [item["id"] for item in models if item.get("provider") == provider],
            })
        return result, router_available

    async def provider_status(self, provider: str) -> dict[str, Any]:
        self._provider(provider)
        attempt = self._oauth_attempts.get(provider)
        if attempt and attempt.get("flow") == "device-code":
            poll_body = {
                "deviceCode": attempt["device_code"],
                "codeVerifier": attempt.get("code_verifier"),
            }
            if attempt.get("extra_data"):
                poll_body["extraData"] = attempt["extra_data"]
            payload = await self.router.oauth(
                provider,
                "poll",
                method="POST",
                body=poll_body,
            )
            if payload.get("success"):
                self._oauth_attempts.pop(provider, None)
                self._cache.invalidate("providers")
            elif not payload.get("pending") and payload.get("error") not in {
                "authorization_pending", "slow_down",
            }:
                self._oauth_attempts.pop(provider, None)
                raise ServiceError(
                    str(
                        payload.get("errorDescription")
                        or payload.get("error")
                        or "provider authorization failed"
                    ),
                    status=400,
                    code="oauth_authorization_failed",
                )
        items = await self.providers()
        current = next(item for item in items if item["id"] == provider)
        return {"provider_id": provider, "connection_mode": current["connection_mode"], "connected": current["connected"], "status": current["status"], "default_model": current["default_model"], "available_models": current["available_models"]}

    async def provider_models(self, provider: str) -> dict[str, Any]:
        self._provider(provider)
        items = [
            item for item in (await self.router.list_models())["data"]
            if item.get("provider") == provider
        ]
        reasoning = []
        for level in ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"):
            if any(level in item.get("reasoning_levels", []) for item in items):
                reasoning.append(level)
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
        status = await self.router.status()
        if not status.get("available"):
            raise ServiceError(
                "Provider runtime is unavailable",
                status=503,
                code="provider_runtime_unavailable",
            )
        return {
            "status": "ok",
            "available": True,
            "provider_count": int(status.get("provider_count") or 0),
        }

    async def start_provider_connect(self, provider: str) -> dict[str, Any]:
        self._provider(provider)
        self._require_connection_auth(provider)
        if provider in API_KEY_PROVIDERS:
            return self._api_key_info(provider)
        if provider in IMPORT_TOKEN_PROVIDERS:
            return {
                "provider_id": provider,
                "connection_mode": "cli",
                "required_client_action": "submit_text",
                "login_url": "https://cursor.com/dashboard",
                "verification_url": "https://cursor.com/dashboard",
                "instructions": (
                    "Sign in to Cursor, then paste the Cursor access token. You may also paste "
                    "JSON containing accessToken and the optional machineId."
                ),
                "text_label": "Cursor access token or credential JSON",
                "status": "waiting_for_user",
            }
        if provider in DEVICE_CODE_PROVIDERS:
            payload = await self.router.oauth(provider, "device-code", method="GET")
            device_code = str(payload.get("device_code") or "").strip()
            user_code = str(payload.get("user_code") or "").strip()
            verification_url = str(
                payload.get("verification_uri_complete")
                or payload.get("verification_uri")
                or payload.get("verification_url")
                or ""
            ).strip()
            if not device_code or not user_code or not verification_url:
                raise ServiceError(
                    "Provider runtime returned an incomplete device authorization",
                    status=502,
                    code="router_error",
                )
            self._oauth_attempts[provider] = {
                "flow": "device-code",
                "device_code": device_code,
                "code_verifier": str(payload.get("codeVerifier") or ""),
                "extra_data": {
                    key: payload[key]
                    for key in ("_clientId", "_clientSecret", "_region", "_authMethod")
                    if payload.get(key) is not None
                },
            }
            return {
                "provider_id": provider,
                "connection_mode": "device-code",
                "required_client_action": "open_url_and_enter_code",
                "login_url": verification_url,
                "verification_url": verification_url,
                "user_code": user_code,
                "poll_interval_seconds": max(2, int(payload.get("interval") or 5)),
                "instructions": (
                    "Open the verification page, enter the code, and approve access."
                ),
                "text_label": "",
                "status": "waiting_for_user",
            }
        redirect = {
            "codex": "http://localhost:1455/auth/callback",
            "xai-oauth": "http://127.0.0.1:56121/callback",
        }.get(provider, "http://localhost:20128/callback")
        payload = await self.router.oauth(provider, "authorize", method="GET", query_string=f"redirect_uri={redirect}")
        self._oauth_attempts[provider] = {"code_verifier": str(payload.get("codeVerifier") or ""), "state": str(payload.get("state") or ""), "redirect_uri": redirect}
        url = str(payload.get("authUrl") or "")
        if not url:
            raise ServiceError("Provider runtime did not return an authorization URL", status=502, code="router_error")
        return {"provider_id": provider, "connection_mode": "cli", "required_client_action": "submit_response", "login_url": url, "verification_url": url, "instructions": "Authorize in the browser, then paste the complete callback URL.", "text_label": "Callback URL", "status": "waiting_for_user"}

    async def submit_provider_connect(self, provider: str, body: Mapping[str, Any]) -> dict[str, Any]:
        self._provider(provider)
        self._require_connection_auth(provider)
        value = str(body.get("text") or body.get("response_text") or body.get("token") or body.get("api_key") or "").strip()
        if not value:
            raise ServiceError("provider credential or callback is required")
        if provider in IMPORT_TOKEN_PROVIDERS:
            access_token, machine_id = self._cursor_credentials(value)
            result = await self.router.import_cursor_credentials(
                access_token,
                machine_id,
            )
            if not result.get("success"):
                raise ServiceError(
                    "Cursor credential import failed",
                    status=400,
                    code="oauth_authorization_failed",
                )
            self._cache.invalidate("providers")
            return {
                "provider_id": provider,
                "connection_mode": "cli",
                "connected": True,
                "status": "connected",
                "connection": result.get("connection", {}),
            }
        if provider in API_KEY_PROVIDERS:
            router_provider = provider
            if provider == "opencode":
                router_provider = await self.router.ensure_opencode_zen_provider()
            elif provider in OPENAI_COMPATIBLE_PROVIDER_DEFINITIONS:
                definition = OPENAI_COMPATIBLE_PROVIDER_DEFINITIONS[provider]
                base_url = str(
                    (body.get("base_url") or "") if provider == "openai-like"
                    else definition["base_url"]
                ).strip()
                if not base_url:
                    raise ServiceError("base_url is required", code="invalid_provider_connection")
                router_provider = await self.router.ensure_openai_compatible_provider(
                    provider,
                    display_name=definition["display_name"],
                    base_url=base_url,
                )
            await self.router.upsert_api_key_connection({
                "provider": router_provider,
                "api_key": value,
            })
            self._cache.invalidate("providers")
            return {**self._api_key_info(provider), "connected": True, "status": "connected"}
        from urllib.parse import parse_qs, urlparse
        attempt = self._oauth_attempts.get(provider)
        if not attempt:
            raise ServiceError("provider connection has not been started", status=409, code="connect_not_started")
        parsed = urlparse(value)
        query = parse_qs(parsed.query) if parsed.scheme else {}
        code = str((query.get("code") or [value])[0])
        state = str((query.get("state") or [attempt["state"]])[0])
        if attempt["state"] and state != attempt["state"]:
            raise ServiceError("provider callback state does not match", status=400, code="oauth_state_mismatch")
        payload = await self.router.oauth(provider, "exchange", method="POST", body={"code": code, "redirectUri": attempt["redirect_uri"], "codeVerifier": attempt["code_verifier"], "state": state})
        if not payload.get("success"):
            raise ServiceError("provider authorization was not accepted", status=422, code="provider_auth_failed")
        self._oauth_attempts.pop(provider, None)
        self._cache.invalidate("providers")
        return {"provider_id": provider, "connection_mode": "cli", "connected": True, "status": "connected"}

    async def disconnect_provider(self, provider: str) -> dict[str, Any]:
        self._provider(provider)
        self._require_connection_auth(provider)
        connections = (await self.router.list_connections())["connections"]
        for item in connections:
            if item.get("provider") == provider:
                await self.router.delete_connection(item["id"])
        self._oauth_attempts.pop(provider, None)
        self._cache.invalidate("providers")
        return {"provider_id": provider, "connected": False, "status": "disconnected"}

    async def test_provider(self, provider: str) -> dict[str, Any]:
        self._provider(provider)
        self._require_connection_auth(provider)
        rows = await self._provider_connections(provider)
        current = next(
            (item for item in rows if item.get("active") is not False),
            rows[0] if rows else None,
        )
        if not current:
            return {"provider_id": provider, "healthy": False, "status": "not_connected", "message": "Provider is not connected"}
        result = await self.router.test_connection(current["id"])
        self._cache.invalidate("providers")
        return {"provider_id": provider, "healthy": bool(result.get("valid")), "status": "healthy" if result.get("valid") else "unhealthy", "message": result.get("error") or ""}

    async def list_provider_connections(self, provider: str) -> dict[str, Any]:
        self._provider(provider)
        self._require_connection_auth(provider)
        connections = await self._provider_connections(provider)
        return {
            "provider_id": provider,
            "connected": any(item.get("active") is not False for item in connections),
            "connections": connections,
        }

    async def upsert_provider_connection(self, provider: str, body: Mapping[str, Any]) -> dict[str, Any]:
        self._provider(provider)
        self._require_connection_auth(provider)
        if provider not in API_KEY_PROVIDERS:
            raise ServiceError(
                "use the provider connect flow to add an OAuth account",
                status=400, code="oauth_connect_required",
            )
        router_provider = provider
        if provider == "opencode":
            router_provider = await self.router.ensure_opencode_zen_provider()
        elif provider in OPENAI_COMPATIBLE_PROVIDER_DEFINITIONS:
            definition = OPENAI_COMPATIBLE_PROVIDER_DEFINITIONS[provider]
            base_url = str(
                (body.get("base_url") or "") if provider == "openai-like"
                else definition["base_url"]
            ).strip()
            if not base_url:
                raise ServiceError("base_url is required", code="invalid_provider_connection")
            router_provider = await self.router.ensure_openai_compatible_provider(
                provider,
                display_name=definition["display_name"],
                base_url=base_url,
            )
        result = await self.router.upsert_api_key_connection({
            "provider": router_provider,
            "api_key": body.get("api_key"),
        })
        self._cache.invalidate("providers")
        return {"provider_id": provider, "connected": True, "connection": result["connection"]}

    async def patch_provider_connection(self, provider: str, connection_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        await self._owned_connection(provider, connection_id)
        active = body.get("active")
        priority = body.get("priority")
        if active is None and priority is None:
            raise ServiceError("active or priority is required", code="invalid_request")
        if priority is not None:
            priority = max(0, min(999, int(priority)))
        await self.router.update_connection(connection_id, active=active, priority=priority)
        self._cache.invalidate("providers")
        # OmniRoute owns priority normalization, so return the stored row, not the request.
        refreshed = await self._owned_connection(provider, connection_id)
        return {"provider_id": provider, "connection": refreshed}

    async def test_provider_connection(self, provider: str, connection_id: str) -> dict[str, Any]:
        await self._owned_connection(provider, connection_id)
        result = await self.router.test_connection(connection_id)
        self._cache.invalidate("providers")
        healthy = bool(result.get("valid"))
        return {
            "provider_id": provider, "connection_id": connection_id,
            "healthy": healthy, "status": "healthy" if healthy else "unhealthy",
            "message": result.get("error") or "",
        }

    async def delete_provider_connection(self, provider: str, connection_id: str) -> dict[str, Any]:
        await self._owned_connection(provider, connection_id)
        await self.router.delete_connection(connection_id)
        self._cache.invalidate("providers")
        remaining = await self._provider_connections(provider)
        return {
            "provider_id": provider, "connection_id": connection_id, "deleted": True,
            "connected": any(item.get("active") is not False for item in remaining),
        }

    async def connection_usage(self, provider: str, connection_id: str) -> dict[str, Any]:
        await self._owned_connection(provider, connection_id)
        payload = await self.router.usage_for_connection(connection_id)
        return {"provider_id": provider, **payload}

    async def _provider_connections(self, provider: str) -> list[dict[str, Any]]:
        connections = (await self.router.list_connections())["connections"]
        rows = [item for item in connections if item.get("provider") == provider]
        rows.sort(key=lambda item: (int(item.get("priority") or 0), str(item.get("name") or "")))
        return rows

    async def _owned_connection(self, provider: str, connection_id: str) -> dict[str, Any]:
        self._provider(provider)
        rows = await self._provider_connections(provider)
        current = next((item for item in rows if item.get("id") == connection_id), None)
        if current is None:
            raise ServiceError("connection not found", status=404, code="not_found")
        return current

    @staticmethod
    def _provider(provider: str) -> str:
        if provider not in SUPPORTED_PROVIDERS:
            raise ServiceError("provider not found", status=404, code="not_found")
        return provider

    @staticmethod
    def _require_connection_auth(provider: str) -> None:
        if provider in NO_AUTH_PROVIDERS:
            raise ServiceError(
                "provider is available without authentication",
                status=400,
                code="no_auth_provider",
            )

    @staticmethod
    def _api_key_info(provider: str) -> dict[str, Any]:
        info = {
            "provider_id": provider,
            "provider_type": provider,
            "connection_mode": "api-key",
            "required_client_action": "submit_text",
            "instructions": (
                "Enter the provider API key. It is stored by the local provider runtime, not "
                "XNOBrain."
            ),
            "text_label": "API key",
            "status": "waiting_for_user",
        }
        if provider == "opencode-go":
            info.update({
                "login_url": "https://opencode.ai/auth",
                "verification_url": "https://opencode.ai/auth",
                "instructions": (
                    "Sign in to OpenCode, subscribe to Go, then paste the "
                    "issued API key here. The key is stored by the local provider runtime, not "
                    "XNOBrain."
                ),
            })
        return info

    @staticmethod
    def _cursor_credentials(value: str) -> tuple[str, str]:
        if not value.lstrip().startswith("{"):
            return value.strip(), ""
        try:
            document = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ServiceError(
                "Cursor credential JSON is invalid",
                code="invalid_provider_connection",
            ) from exc
        if not isinstance(document, dict):
            raise ServiceError(
                "Cursor credential JSON must be an object",
                code="invalid_provider_connection",
            )
        access_token = str(
            document.get("accessToken")
            or document.get("access_token")
            or document.get("token")
            or ""
        ).strip()
        if not access_token:
            raise ServiceError(
                "Cursor credential JSON must contain accessToken",
                code="invalid_provider_connection",
            )
        machine_id = str(
            document.get("machineId") or document.get("machine_id") or ""
        ).strip()
        return access_token, machine_id
