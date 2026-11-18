"""Private facade headers carrying Control-verified request identity."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

TRUSTED_SUBJECT_HEADER = "x-xnobrain-verified-subject"
TRUSTED_TENANT_HEADER = "x-xnobrain-verified-tenant"
TRUSTED_ORGANIZATION_HEADER = "x-xnobrain-verified-organization"
TRUSTED_SIGNATURE_HEADER = "x-xnobrain-principal-signature"
SNAPSHOT_SHA_HEADER = "x-xnobrain-community-snapshot-sha256"
SNAPSHOT_SIZE_HEADER = "x-xnobrain-community-snapshot-size"
SNAPSHOT_OPERATION_HEADER = "x-xnobrain-community-snapshot-idempotency-key"
SNAPSHOT_SIGNATURE_HEADER = "x-xnobrain-community-snapshot-signature"
SNAPSHOT_PREFIX = "/xnobrain/api/runtime/v1/community/snapshots/"
TRUSTED_CONVERSATION_CONTEXT_HEADER = "x-xnobrain-verified-conversation-context"
TRUSTED_CONVERSATION_CONTEXT_SIGNATURE_HEADER = "x-xnobrain-conversation-context-signature"
TRUSTED_CATALOG_RECIPE_HEADER = "x-xnobrain-verified-catalog-recipe"
TRUSTED_CATALOG_RECIPE_SIGNATURE_HEADER = "x-xnobrain-catalog-recipe-signature"
TRUSTED_MCP_GRANT_HEADER = "x-xnobrain-mcp-grant"
TRUSTED_NATIVE_PROVIDER_INTENT_HEADER = "x-xnobrain-native-provider-intent"
TRUSTED_NATIVE_PROVIDER_SIGNATURE_HEADER = "x-xnobrain-native-provider-signature"
NATIVE_PROVIDER_INTENT_DOMAIN = "native-provider-package-v1"
NATIVE_PROVIDER_INTENT_PREFIX = "/xnobrain/api/runtime/v1/agents-extension-operations/"

TRUSTED_IDENTITY_HEADERS = frozenset(
    {
        TRUSTED_SUBJECT_HEADER,
        TRUSTED_TENANT_HEADER,
        TRUSTED_ORGANIZATION_HEADER,
        TRUSTED_SIGNATURE_HEADER,
        TRUSTED_CONVERSATION_CONTEXT_HEADER,
        TRUSTED_CONVERSATION_CONTEXT_SIGNATURE_HEADER,
        TRUSTED_CATALOG_RECIPE_HEADER,
        TRUSTED_CATALOG_RECIPE_SIGNATURE_HEADER,
        TRUSTED_NATIVE_PROVIDER_INTENT_HEADER,
        TRUSTED_NATIVE_PROVIDER_SIGNATURE_HEADER,
    }
)
_MAX_CONTEXT_HEADER_BYTES = 8 * 1024
_MAX_NATIVE_PROVIDER_INTENT_BYTES = 16 * 1024
_NATIVE_PROVIDER_ROUTE = re.compile(
    r"^/xnobrain/api/runtime/v1/agents-extension-operations/"
    r"([A-Za-z0-9][A-Za-z0-9._-]{0,127})$"
)
_NATIVE_PROVIDER_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_NATIVE_PACKAGE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
_NATIVE_PACKAGE_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_NATIVE_AGENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_NATIVE_PROVIDER_INTENT_MAX_LIFETIME = 300
_NATIVE_PROVIDER_CLOCK_SKEW = 30


@dataclass(frozen=True)
class TrustedRequestContext:
    """Identity and optional ownership claims asserted by Control."""

    subject: str = ""
    tenant_id: str = ""
    organization_id: str = ""
    ownership_context: Mapping[str, Any] | None = None
    xno_data_grant: str = field(default="", repr=False, compare=False)
    mcp_grant: str = field(default="", repr=False, compare=False)
    catalog_recipe: Mapping[str, Any] | None = None
    native_provider_intent: Mapping[str, Any] | None = None


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


def snapshot_intent(method: str, path: str) -> str:
    organization_prefix = SNAPSHOT_PREFIX + "organization/"
    if path.startswith(organization_prefix):
        parts = path[len(organization_prefix) :].split("/")
        if method == "GET" and len(parts) == 3 and parts[0] and parts[1] == "receipts" and parts[2]:
            return "receipt"
        if method == "POST" and len(parts) == 2 and parts[0] and parts[1] == "inspect":
            return "export"
        if (
            method == "POST"
            and len(parts) == 3
            and parts[0]
            and parts[1] == "refresh-checked"
            and len(parts[2]) == 64
            and all(c in "0123456789abcdef" for c in parts[2])
        ):
            return "import"
    if method == "POST" and path in {
        "/xnobrain/api/runtime/v1/skill-community/installations",
        "/xnobrain/api/runtime/v1/marketplace/install",
        "/xnobrain/api/runtime/v1/marketplace/update",
    }:
        return "import"
    if method == "GET" and (
        path.startswith(SNAPSHOT_PREFIX + "receipts/")
        or (
            path.startswith("/xnobrain/api/runtime/v1/skill-community/installations/")
            and path.endswith("/receipt")
        )
        or (
            path.startswith("/xnobrain/api/runtime/v1/marketplace/installations/")
            and path.endswith("/receipt")
        )
    ):
        return "receipt"
    if method != "POST":
        return ""
    organization_prefix = SNAPSHOT_PREFIX + "organization/"
    if path.startswith(organization_prefix):
        action = path[len(organization_prefix) :]
        if action == "import":
            return "import"
        parts = action.split("/")
        if len(parts) == 2 and parts[0] and parts[1] in {"export", "refresh"}:
            return "export" if parts[1] == "export" else "import"
        return ""
    if path == SNAPSHOT_PREFIX + "import":
        return "import"
    if path.startswith(SNAPSHOT_PREFIX) and path.endswith("/export"):
        agent_id = path[len(SNAPSHOT_PREFIX) : -len("/export")]
        if agent_id and "/" not in agent_id:
            return "export"
    return ""


def snapshot_signature(
    token: str,
    method: str,
    path: str,
    subject: str,
    tenant_id: str,
    organization_id: str,
    sha256: str,
    size: str,
    operation_id: str,
) -> str:
    material = "\x00".join(
        (subject, tenant_id, organization_id, method, path, sha256, size, operation_id)
    ).encode("utf-8")
    return hmac.new(token.encode("utf-8"), material, hashlib.sha256).hexdigest()


def snapshot_authorized(request: Any) -> bool:
    context = from_request(request)
    intent = snapshot_intent(request.method, request.url.path)
    if not context.subject or not intent:
        return False
    sha256 = request.headers.get(SNAPSHOT_SHA_HEADER, "")
    size = request.headers.get(SNAPSHOT_SIZE_HEADER, "")
    operation_id = request.headers.get(SNAPSHOT_OPERATION_HEADER, "")
    signature = request.headers.get(SNAPSHOT_SIGNATURE_HEADER, "")
    token = os.getenv("RUNTIME_INTERNAL_SERVICE_TOKEN", "").strip()
    if not token or not signature:
        return False
    if intent == "export":
        if sha256 or size or operation_id:
            return False
    elif not (
        len(sha256) == 64
        and all(char in "0123456789abcdef" for char in sha256)
        and size.isdecimal()
        and str(int(size)) == size
        and operation_id
    ):
        return False
    expected = snapshot_signature(
        token,
        request.method,
        request.url.path,
        context.subject,
        context.tenant_id,
        context.organization_id,
        sha256,
        size,
        operation_id,
    )
    return hmac.compare_digest(signature, expected)


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


def encode_catalog_recipe(recipe: Mapping[str, Any]) -> str:
    """Encode a bounded Control-owned install recipe for a private header."""
    return encode_conversation_context(recipe)


def catalog_recipe_signature(
    token: str,
    subject: str,
    tenant_id: str,
    organization_id: str,
    encoded_recipe: str,
) -> str:
    """Bind the exact recipe transport to the verified principal."""
    return conversation_context_signature(
        token,
        subject,
        tenant_id,
        organization_id,
        encoded_recipe,
    )


def decode_verified_catalog_recipe(
    token: str,
    subject: str,
    tenant_id: str,
    organization_id: str,
    encoded_recipe: str,
    supplied_signature: str,
) -> dict[str, Any] | None:
    """Return a recipe only when its canonical transport signature is valid."""
    return decode_verified_conversation_context(
        token,
        subject,
        tenant_id,
        organization_id,
        encoded_recipe,
        supplied_signature,
    )


def native_provider_intent_agent_id(path: str) -> str:
    """Return the agent bound by the exact package-intent route, or ``""``."""
    match = _NATIVE_PROVIDER_ROUTE.fullmatch(str(path or ""))
    return match.group(1) if match else ""


def native_provider_intent_signature(
    token: str,
    method: str,
    path: str,
    subject: str,
    tenant_id: str,
    organization_id: str,
    encoded_intent: str,
) -> str:
    """Bind exact Control-owned package claims to the private Runtime token."""
    material = "\x00".join(
        (
            NATIVE_PROVIDER_INTENT_DOMAIN,
            method,
            path,
            subject,
            tenant_id,
            organization_id,
            encoded_intent,
        )
    ).encode("utf-8")
    return hmac.new(token.encode("utf-8"), material, hashlib.sha256).hexdigest()


def _valid_native_provider_intent(
    value: Any,
    *,
    path: str,
    subject: str,
    tenant_id: str,
    organization_id: str,
    now: int,
) -> bool:
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        return False
    provider = value.get("provider")
    package_version = value.get("package_version")
    revision = value.get("definition_revision")
    digest = value.get("package_sha256")
    if (
        not isinstance(provider, str)
        or not _NATIVE_PROVIDER_ID.fullmatch(provider)
        or not isinstance(package_version, str)
        or not _NATIVE_PACKAGE_VERSION.fullmatch(package_version)
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or not 1 <= revision <= 1_000_000
        or not isinstance(digest, str)
        or not _NATIVE_PACKAGE_SHA256.fullmatch(digest)
    ):
        return False
    agent_id = native_provider_intent_agent_id(path)
    user_id = value.get("user_id")
    tenant_id_value = value.get("tenant_id", "")
    organization_value = value.get("organization_id", "")
    if (
        not agent_id
        or not _NATIVE_AGENT_ID.fullmatch(agent_id)
        or value.get("agent_id") != agent_id
        or value.get("kind") not in {"install", "update"}
        or value.get("path") != path
        or not isinstance(user_id, str)
        or not user_id
        or len(user_id) > 512
        or user_id != subject
        or not isinstance(tenant_id_value, str)
        or not tenant_id_value
        or len(tenant_id_value) > 256
        or tenant_id_value != tenant_id
        or organization_value != ""
        or organization_id != ""
    ):
        return False
    issued_at = value.get("issued_at")
    expires_at = value.get("expires_at")
    if (
        isinstance(issued_at, bool)
        or isinstance(expires_at, bool)
        or not isinstance(issued_at, int)
        or not isinstance(expires_at, int)
        or issued_at <= 0
        or expires_at <= issued_at
        or expires_at - issued_at > _NATIVE_PROVIDER_INTENT_MAX_LIFETIME
        or issued_at > now + _NATIVE_PROVIDER_CLOCK_SKEW
        or expires_at < now - _NATIVE_PROVIDER_CLOCK_SKEW
    ):
        return False
    return True


def decode_verified_native_provider_intent(
    token: str,
    method: str,
    path: str,
    subject: str,
    tenant_id: str,
    organization_id: str,
    encoded_intent: str,
    supplied_signature: str,
) -> dict[str, Any] | None:
    """Return package intent only for the exact signed install/update route."""
    if not all((token, subject, method == "POST", encoded_intent, supplied_signature)):
        return None
    if not native_provider_intent_agent_id(path):
        return None
    if len(encoded_intent) > _MAX_NATIVE_PROVIDER_INTENT_BYTES * 2:
        return None
    if len(supplied_signature) != 64 or not _NATIVE_PACKAGE_SHA256.fullmatch(supplied_signature):
        return None
    try:
        expected = native_provider_intent_signature(
            token,
            method,
            path,
            subject,
            tenant_id,
            organization_id,
            encoded_intent,
        )
        if not hmac.compare_digest(supplied_signature, expected):
            return None
        padded = encoded_intent + "=" * (-len(encoded_intent) % 4)
        payload = base64.urlsafe_b64decode(padded.encode("ascii"))
        if len(payload) > _MAX_NATIVE_PROVIDER_INTENT_BYTES:
            return None
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeError, ValueError, json.JSONDecodeError):
        return None
    if not _valid_native_provider_intent(
        value,
        path=path,
        subject=subject,
        tenant_id=tenant_id,
        organization_id=organization_id,
        now=int(time.time()),
    ):
        return None
    return value


def _single_header(headers: Any, name: str) -> str:
    """Return one value, or ``""`` for missing or duplicated private headers."""
    try:
        values = headers.getlist(name)
    except AttributeError:
        value = headers.get(name)
        return str(value).strip() if value else ""
    if len(values) != 1:
        return ""
    return str(values[0]).strip()


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
    recipe = decode_verified_catalog_recipe(
        token,
        subject,
        tenant_id,
        organization_id,
        str(headers.get(TRUSTED_CATALOG_RECIPE_HEADER) or "").strip(),
        str(headers.get(TRUSTED_CATALOG_RECIPE_SIGNATURE_HEADER) or "").strip(),
    )
    intent = None
    if native_provider_intent_agent_id(request.url.path):
        intent = decode_verified_native_provider_intent(
            token,
            request.method,
            request.url.path,
            subject,
            tenant_id,
            organization_id,
            _single_header(headers, TRUSTED_NATIVE_PROVIDER_INTENT_HEADER),
            _single_header(headers, TRUSTED_NATIVE_PROVIDER_SIGNATURE_HEADER),
        )
    return TrustedRequestContext(
        subject,
        tenant_id,
        organization_id,
        ownership_context,
        str(headers.get("x-xnobrain-data-grant") or ""),
        str(headers.get(TRUSTED_MCP_GRANT_HEADER) or ""),
        recipe,
        intent,
    )
