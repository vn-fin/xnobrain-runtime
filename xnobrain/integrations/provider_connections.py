"""Grouped ProviderConnections behavior for 9router."""

from .nine_router_support import (
    API_KEY_ROUTER_PROVIDERS,
    Any,
    Mapping,
    NineRouterAPIError,
    OAUTH_ROUTER_PROVIDERS,
    OPENAI_COMPATIBLE_PROVIDERS,
    OPENCODE_ZEN_API_BASE_URL,
    OPENCODE_ZEN_ROUTER_ALIAS,
    ROUTER_PROVIDER_BY_MODEL_OWNER,
    SUPPORTED_ROUTER_PROVIDERS,
    _OAUTH_GET_ACTIONS,
    _OAUTH_POST_ACTIONS,
    quote,
)


class ProviderConnectionsMixin:
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
