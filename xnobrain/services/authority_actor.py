"""Shared authorization gate for verified private authority endpoints."""

from __future__ import annotations

from typing import Any, Mapping

from ..trusted_context import TrustedRequestContext
from .base import ServiceError


def require_personal_actor(trusted: Any, *, label: str = "verified actor") -> tuple[str, str]:
    """Return the signed Personal subject and tenant, never request values."""
    if not isinstance(trusted, TrustedRequestContext):
        raise ServiceError(
            f"{label} is required",
            status=401,
            code="trusted_context_required",
        )
    subject = str(trusted.subject or "").strip()
    tenant = str(trusted.tenant_id or "").strip()
    if not subject or not tenant:
        raise ServiceError(
            f"{label} is required",
            status=401,
            code="trusted_context_required",
        )
    context = trusted.ownership_context
    if str(trusted.organization_id or "") or (
        isinstance(context, Mapping)
        and (
            str(context.get("owner_kind") or "") not in {"", "personal"}
            or context.get("organization_id")
            or str(context.get("state") or "active") != "active"
        )
    ):
        raise ServiceError(
            "a Personal context is required",
            status=403,
            code="permission_denied",
        )
    return subject, tenant
