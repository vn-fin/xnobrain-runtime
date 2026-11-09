"""Owner-bound Control MCP grants for channel turns, cached in memory only."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from opentelemetry import propagate

_LOGGER = logging.getLogger(__name__)
_PATH = "/xnobrain/api/control/internal/v1/workspace-mcp-grants"
_REFRESH_MARGIN_SECONDS = 120.0
_TIMEOUT_SECONDS = 10.0
_LOCK = threading.RLock()
_CACHE: dict[tuple[str, str, str], "_Grant"] = {}


@dataclass(frozen=True)
class _Grant:
    """One validated grant; the bearer must never reach a log or repr."""

    grant: str = field(repr=False)
    expires_at: float
    user_id: str
    tenant_id: str


def _matches(grant: _Grant, user_id: str, tenant_id: str) -> bool:
    return grant.user_id == user_id and grant.tenant_id == tenant_id


def _expiry(value: object) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _data(payload: object) -> dict:
    """Accept either the bare contract object or Control's data envelope."""
    if not isinstance(payload, dict):
        raise ValueError("invalid grant response")
    wrapped = payload.get("data")
    return wrapped if isinstance(wrapped, dict) else payload


def _failure(reason: str) -> None:
    # Stable reason and error class only; never a body, URL, or credential.
    _LOGGER.warning(
        "channel MCP grant unavailable",
        extra={"event": "channel_mcp_grant_unavailable", "reason": reason},
    )


class WorkspaceMcpGrantClient:
    """Mint one owner-bound grant per profile and cache it briefly in memory."""

    def __init__(self, client=None):
        self._client = client

    async def grant(
        self,
        scope: str,
        *,
        agent_id: str,
        platform: str,
        user_id: str,
        tenant_id: str,
        now: float | None = None,
    ) -> str:
        """Return a fresh, binding-matching grant or an empty string."""
        if not scope or not user_id:
            return ""
        cache_key = (str(scope), str(agent_id), str(platform))
        current = time.time() if now is None else float(now)
        with _LOCK:
            cached = _CACHE.get(cache_key)
        if cached is not None and cached.expires_at <= current:
            with _LOCK:
                _CACHE.pop(cache_key, None)
            cached = None
        if (
            cached is not None
            and _matches(cached, user_id, tenant_id)
            and cached.expires_at - current > _REFRESH_MARGIN_SECONDS
        ):
            return cached.grant
        fetched = await self._fetch(agent_id, platform)
        if fetched is None:
            # A failed refresh may keep using an unexpired matching grant.
            if (
                cached is not None
                and _matches(cached, user_id, tenant_id)
                and cached.expires_at > current
            ):
                return cached.grant
            return ""
        if not _matches(fetched, user_id, tenant_id) or fetched.expires_at <= current:
            with _LOCK:
                _CACHE.pop(cache_key, None)
            return ""
        with _LOCK:
            _CACHE[cache_key] = fetched
        return fetched.grant

    async def _fetch(self, agent_id: str, platform: str) -> _Grant | None:
        endpoint = os.getenv("RUNTIME_CONTROL_URL", "").rstrip("/")
        workspace = os.getenv("RUNTIME_WORKSPACE_ID", "").strip()
        secret = os.getenv("RUNTIME_INTERNAL_SERVICE_TOKEN", "").strip()
        if not endpoint or not workspace or not secret:
            _failure("not_configured")
            return None
        headers = {
            "Authorization": "Bearer " + secret,
            "X-XNOBrain-Workspace-ID": workspace,
        }
        propagate.inject(headers)
        payload = {"agent_id": str(agent_id), "platform": str(platform)}
        try:
            response = await self._post(endpoint + _PATH, headers, payload)
        except (httpx.HTTPError, ValueError, TypeError, AttributeError) as error:
            _failure(type(error).__name__)
            return None
        if response.status_code != 200:
            _failure("http_" + str(response.status_code))
            return None
        try:
            body = _data(response.json())
            grant = str(body.get("grant") or "")
            user_id = str(body.get("user_id") or "")
            tenant_id = str(body.get("tenant_id") or "")
            expires_at = _expiry(body.get("expires_at"))
        except (ValueError, TypeError, AttributeError):
            _failure("invalid_response")
            return None
        if not grant or not user_id or expires_at is None:
            _failure("invalid_response")
            return None
        return _Grant(grant, expires_at, user_id, tenant_id)

    async def _post(self, url: str, headers: dict[str, str], payload: dict):
        if self._client is not None:
            return await self._client.post(url, headers=headers, json=payload)
        async with httpx.AsyncClient(
            timeout=_TIMEOUT_SECONDS,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            return await client.post(url, headers=headers, json=payload)
