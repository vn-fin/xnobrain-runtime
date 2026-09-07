"""Private facade headers carrying Control-verified request identity."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from typing import Any, Mapping

TRUSTED_SUBJECT_HEADER = "x-xnobrain-verified-subject"
TRUSTED_TENANT_HEADER = "x-xnobrain-verified-tenant"
TRUSTED_ORGANIZATION_HEADER = "x-xnobrain-verified-organization"
TRUSTED_SIGNATURE_HEADER = "x-xnobrain-principal-signature"
TRUSTED_CONVERSATION_CONTEXT_HEADER = "x-xnobrain-verified-conversation-context"
TRUSTED_CONVERSATION_CONTEXT_SIGNATURE_HEADER = "x-xnobrain-conversation-context-signature"
TRUSTED_IDENTITY_HEADERS = frozenset(
    {
        TRUSTED_SUBJECT_HEADER,
        TRUSTED_TENANT_HEADER,
        TRUSTED_ORGANIZATION_HEADER,
        TRUSTED_SIGNATURE_HEADER,
        TRUSTED_CONVERSATION_CONTEXT_HEADER,
        TRUSTED_CONVERSATION_CONTEXT_SIGNATURE_HEADER,
    }
)
_MAX_CONTEXT_HEADER_BYTES = 8 * 1024


@dataclass(frozen=True)
class TrustedRequestContext:
    """Identity and optional ownership claims asserted by Control."""

    subject: str = ""
    tenant_id: str = ""
    organization_id: str = ""
    ownership_context: Mapping[str, Any] | None = None


def _principal_material(
    subject: str,
    tenant_id: str,
    organization_id: str,
) -> bytes:
    return "\x00".join((subject, tenant_id, organization_id)).encode("utf-8")


def principal_signature(
    token: str,
    subject: str,
    tenant_id: str = "",
    organization_id: str = "",
) -> str:
    """Bind principal fields to the private Runtime service credential."""
    return hmac.new(
        token.encode("utf-8"),
        _principal_material(subject, tenant_id, organization_id),
        hashlib.sha256,
    ).hexdigest()


def encode_conversation_context(context: Mapping[str, Any]) -> str:
    """Encode bounded canonical claims for transport in a private header."""
    payload = json.dumps(
        dict(context),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(payload) > _MAX_CONTEXT_HEADER_BYTES:
        raise ValueError("conversation context header is too large")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def conversation_context_signature(
    token: str,
    subject: str,
    tenant_id: str,
    organization_id: str,
    encoded_context: str,
) -> str:
    """Bind exact ownership claims and principal to Control's credential."""
    material = _principal_material(subject, tenant_id, organization_id)
    material += b"\x00" + encoded_context.encode("ascii")
    return hmac.new(token.encode("utf-8"), material, hashlib.sha256).hexdigest()


def decode_verified_conversation_context(
    token: str,
    subject: str,
    tenant_id: str,
    organization_id: str,
    encoded_context: str,
    supplied_signature: str,
) -> dict[str, Any] | None:
    """Return claims only when their canonical transport signature is valid."""
    if not all((token, subject, encoded_context, supplied_signature)):
        return None
    if len(encoded_context) > _MAX_CONTEXT_HEADER_BYTES * 2:
        return None
    try:
        expected = conversation_context_signature(
            token,
            subject,
            tenant_id,
            organization_id,
            encoded_context,
        )
        if not hmac.compare_digest(supplied_signature, expected):
            return None
        padded = encoded_context + "=" * (-len(encoded_context) % 4)
        payload = base64.urlsafe_b64decode(padded.encode("ascii"))
        if len(payload) > _MAX_CONTEXT_HEADER_BYTES:
            return None
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def from_request(request: Any) -> TrustedRequestContext:
    """Verify facade identity without logging or retaining request headers."""
    headers = request.headers
    subject = str(headers.get(TRUSTED_SUBJECT_HEADER) or "").strip()
    tenant_id = str(headers.get(TRUSTED_TENANT_HEADER) or "").strip()
    organization_id = str(headers.get(TRUSTED_ORGANIZATION_HEADER) or "").strip()
    supplied = str(headers.get(TRUSTED_SIGNATURE_HEADER) or "").strip()
    token = os.getenv("RUNTIME_INTERNAL_SERVICE_TOKEN", "").strip()
    if not subject or not supplied or not token:
        return TrustedRequestContext()
    expected = principal_signature(token, subject, tenant_id, organization_id)
    if not hmac.compare_digest(supplied, expected):
        return TrustedRequestContext()
    encoded_context = str(headers.get(TRUSTED_CONVERSATION_CONTEXT_HEADER) or "").strip()
    context_signature = str(
        headers.get(TRUSTED_CONVERSATION_CONTEXT_SIGNATURE_HEADER) or ""
    ).strip()
    ownership_context = decode_verified_conversation_context(
        token,
        subject,
        tenant_id,
        organization_id,
        encoded_context,
        context_signature,
    )
    return TrustedRequestContext(
        subject,
        tenant_id,
        organization_id,
        ownership_context,
    )
