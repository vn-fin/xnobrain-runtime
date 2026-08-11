"""Grouped ProviderUsage behavior for 9router."""

from .nine_router_support import (
    Any,
    Mapping,
    quote,
)


class ProviderUsageMixin:
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
