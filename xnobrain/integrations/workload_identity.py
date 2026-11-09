"""Private Control workload identity used for owner-bound grant renewal.

Control owns the opaque identity. Runtime reads one private source file or the
complete managed provisioning environment, validates it against the verified
channel owner binding, and keeps only a non-secret cache fingerprint.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_IDENTITY_PATH = "/run/xnobrain-runtime/workload-identity.json"
IDENTITY_FILE_ENV = "RUNTIME_WORKLOAD_IDENTITY_FILE"
MAX_IDENTITY_BYTES = 16 * 1024


class WorkloadIdentityError(Exception):
    """Safe identity failure; the message is a stable reason, never a secret."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class WorkloadIdentity:
    workspace_id: str
    workload_id: str
    user_id: str
    tenant_id: str
    token: str = field(repr=False)
    kind: str
    token_fingerprint: str = field(repr=False)

    @property
    def cache_fingerprint(self) -> tuple[str, ...]:
        return (
            self.kind,
            self.workspace_id,
            self.workload_id,
            self.user_id,
            self.tenant_id,
            self.token_fingerprint,
        )


def _token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _text(value: object, reason: str) -> str:
    if not isinstance(value, str):
        raise WorkloadIdentityError(reason)
    text = value.strip()
    if not text or len(text) > 512:
        raise WorkloadIdentityError(reason)
    if any(ord(char) < 0x21 or ord(char) == 0x7F for char in text):
        raise WorkloadIdentityError(reason)
    return text


def _read_file(path: Path) -> dict | None:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
    except FileNotFoundError:
        return None
    except OSError as error:
        raise WorkloadIdentityError("identity_unreadable") from error
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise WorkloadIdentityError("identity_not_regular")
        if details.st_uid != os.geteuid():
            raise WorkloadIdentityError("identity_owner_invalid")
        if details.st_mode & 0o077:
            raise WorkloadIdentityError("identity_permissions_invalid")
        if details.st_size <= 0 or details.st_size > MAX_IDENTITY_BYTES:
            raise WorkloadIdentityError("identity_size_invalid")
        chunks = []
        remaining = MAX_IDENTITY_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
    finally:
        os.close(descriptor)
    if len(raw) > MAX_IDENTITY_BYTES:
        raise WorkloadIdentityError("identity_size_invalid")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise WorkloadIdentityError("identity_malformed") from error
    if not isinstance(value, dict):
        raise WorkloadIdentityError("identity_malformed")
    return value


def _source_identity(
    value: dict, expected_user_id: str, expected_tenant_id: str
) -> WorkloadIdentity:
    version = value.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
        raise WorkloadIdentityError("identity_version_invalid")
    workspace_id = _text(value.get("workspace_id"), "identity_malformed")
    workload_id = _text(value.get("workload_id"), "identity_malformed")
    user_id = _text(value.get("user_id"), "identity_malformed")
    tenant_id = _text(value.get("tenant_id"), "identity_malformed")
    token = _text(value.get("token"), "identity_malformed")
    if user_id != expected_user_id or tenant_id != expected_tenant_id:
        raise WorkloadIdentityError("identity_owner_mismatch")
    return WorkloadIdentity(
        workspace_id=workspace_id,
        workload_id=workload_id,
        user_id=user_id,
        tenant_id=tenant_id,
        token=token,
        kind="source",
        token_fingerprint=_token_fingerprint(token),
    )


def resolve_workload_identity(
    *,
    expected_user_id: str,
    expected_tenant_id: str,
    environ: dict[str, str] | None = None,
) -> WorkloadIdentity:
    """Resolve the complete managed env or one private source identity file.

    Managed env wins. Any partial workload env fails closed. An explicit identity
    path is authoritative and never falls back. Only the installation-derived
    legacy token with a workspace ID remains supported.
    """
    env = os.environ if environ is None else environ
    expected_user_id = str(expected_user_id or "").strip()
    expected_tenant_id = str(expected_tenant_id or "").strip()
    if not expected_user_id or not expected_tenant_id:
        raise WorkloadIdentityError("channel_owner_unavailable")

    workspace_id = str(env.get("RUNTIME_WORKSPACE_ID") or "").strip()
    workload_id = str(env.get("RUNTIME_WORKLOAD_ID") or "").strip()
    workload_token = str(env.get("RUNTIME_WORKLOAD_TOKEN") or "").strip()
    owner_tenant_id = str(env.get("RUNTIME_WORKSPACE_OWNER_TENANT_ID") or "").strip()

    if workload_id or workload_token or owner_tenant_id:
        if not workspace_id or not workload_id or not workload_token or not owner_tenant_id:
            raise WorkloadIdentityError("managed_identity_partial")
        workspace_id = _text(workspace_id, "managed_identity_malformed")
        workload_id = _text(workload_id, "managed_identity_malformed")
        workload_token = _text(workload_token, "managed_identity_malformed")
        owner_tenant_id = _text(owner_tenant_id, "managed_identity_malformed")
        if owner_tenant_id != expected_tenant_id:
            raise WorkloadIdentityError("identity_owner_mismatch")
        return WorkloadIdentity(
            workspace_id=workspace_id,
            workload_id=workload_id,
            user_id=expected_user_id,
            tenant_id=owner_tenant_id,
            token=workload_token,
            kind="managed",
            token_fingerprint=_token_fingerprint(workload_token),
        )

    configured = str(env.get(IDENTITY_FILE_ENV) or "").strip()
    if configured:
        path = Path(configured)
        if not path.is_absolute() or ".." in path.parts:
            raise WorkloadIdentityError("identity_path_invalid")
        value = _read_file(path)
        if value is None:
            raise WorkloadIdentityError("identity_unavailable")
        return _source_identity(value, expected_user_id, expected_tenant_id)

    value = _read_file(Path(DEFAULT_IDENTITY_PATH))
    if value is not None:
        return _source_identity(value, expected_user_id, expected_tenant_id)

    legacy_token = str(env.get("RUNTIME_INTERNAL_SERVICE_TOKEN") or "").strip()
    if not workspace_id or not legacy_token:
        raise WorkloadIdentityError("identity_unavailable")
    workspace_id = _text(workspace_id, "legacy_identity_malformed")
    legacy_token = _text(legacy_token, "legacy_identity_malformed")
    return WorkloadIdentity(
        workspace_id=workspace_id,
        workload_id="",
        user_id=expected_user_id,
        tenant_id=expected_tenant_id,
        token=legacy_token,
        kind="legacy",
        token_fingerprint=_token_fingerprint(legacy_token),
    )
