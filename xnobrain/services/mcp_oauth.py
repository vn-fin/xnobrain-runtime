"""Profile-bound OAuth flows for catalog MCP integrations."""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any

_TTL = 600
_MAX_PENDING_PER_PROFILE = 3
_LOCK = threading.RLock()


@dataclass
class OAuthFlowRecord:
    flow: Any
    agent_id: str
    tenant_id: str
    subject: str
    catalog_id: str
    profile: str
    expires_at: float


_FLOWS: dict[str, OAuthFlowRecord] = {}


def create_flow(flow: Any, agent_id: str, trusted: Any, catalog_id: str, profile: str) -> None:
    now = time.monotonic()
    with _LOCK:
        for key in [key for key, value in _FLOWS.items() if value.expires_at <= now]:
            _FLOWS.pop(key, None)
        pending = [value for value in _FLOWS.values() if value.profile == profile]
        if len(pending) >= _MAX_PENDING_PER_PROFILE:
            raise RuntimeError("OAuth flow limit reached")
        _FLOWS[flow.flow_id] = OAuthFlowRecord(
            flow, agent_id, trusted.tenant_id, trusted.subject, catalog_id, profile, now + _TTL
        )


def get_flow(flow_id: str, agent_id: str, trusted: Any) -> OAuthFlowRecord | None:
    now = time.monotonic()
    with _LOCK:
        for key in [key for key, value in _FLOWS.items() if value.expires_at <= now]:
            _FLOWS.pop(key, None)
        record = _FLOWS.get(flow_id)
        if (
            record is None
            or record.agent_id != agent_id
            or record.tenant_id != trusted.tenant_id
            or record.subject != trusted.subject
        ):
            return None
        return record


def remove_flow(flow_id: str) -> None:
    with _LOCK:
        _FLOWS.pop(flow_id, None)


def new_flow_id() -> str:
    return secrets.token_urlsafe(24)
