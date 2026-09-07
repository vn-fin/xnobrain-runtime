"""Private workspace side of explicitly approved organization transfers."""

from __future__ import annotations

import base64
import hashlib
import os
import re
import tempfile
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.error import URLError
from urllib.request import Request, urlopen

from .base import ServiceError
from .workspaces import _validate_create_path

_HEX = re.compile(r"^[a-f0-9]{64}$")


def _digest(value: str) -> str:
    value = value.lower().removeprefix("sha256:")
    if _HEX.fullmatch(value):
        return value
    try:
        raw = base64.b64decode(value, validate=True)
        if len(raw) == 32:
            return raw.hex()
    except Exception:
        pass
    raise ServiceError("artifact checksum is invalid", status=400, code="invalid_artifact_checksum")


def _expiry(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ServiceError(
            "transfer authorization is invalid", status=400, code="invalid_transfer_authorization"
        )


def _open(transfer: Mapping[str, Any], *, data=None):
    if _expiry(str(transfer.get("expires_at") or "")) <= datetime.now(timezone.utc):
        raise ServiceError(
            "transfer authorization expired", status=410, code="transfer_authorization_expired"
        )
    url = str(transfer.get("url") or "")
    if not url.startswith(("https://", "http://localhost:", "http://127.0.0.1:")):
        raise ServiceError(
            "transfer authorization is invalid", status=400, code="invalid_transfer_authorization"
        )
    req = Request(
        url,
        data=data,
        method=str(transfer.get("method")),
        headers={str(k): str(v) for k, v in dict(transfer.get("headers") or {}).items()},
    )
    try:
        return urlopen(req, timeout=60)
    except URLError as exc:
        raise ServiceError(
            "organization storage is unavailable", status=503, code="storage_unavailable"
        ) from exc


class OrganizationArtifactsServiceMixin:
    @staticmethod
    def publish_organization_artifact_part(transfer: Mapping[str, Any], data: bytes) -> str:
        if transfer.get("method") != "PUT":
            raise ServiceError(
                "upload capability required", status=400, code="invalid_transfer_authorization"
            )
        with _open(transfer, data=data) as response:
            if response.status >= 300:
                raise ServiceError(
                    "organization upload failed", status=503, code="storage_unavailable"
                )
            etag = str(response.headers.get("ETag") or "").strip()
            if not etag:
                raise ServiceError(
                    "organization upload response is invalid",
                    status=503,
                    code="storage_unavailable",
                )
            return etag

    def inspect_organization_artifact(
        self, agent_id: str, body: Mapping[str, Any]
    ) -> dict[str, Any]:
        relative = _validate_create_path(body.get("path"))
        source = self.workspace_file(agent_id, relative)
        if source.is_symlink() or not source.is_file():
            raise ServiceError(
                "artifact source must be a regular file", status=400, code="invalid_workspace_path"
            )
        digest = hashlib.sha256()
        size = 0
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(chunk)
                digest.update(chunk)
        return {
            "name": source.name,
            "size_bytes": size,
            "sha256": digest.hexdigest(),
            "path": relative,
            "ownership": "personal",
        }

    def publish_organization_artifact(
        self, agent_id: str, body: Mapping[str, Any]
    ) -> dict[str, Any]:
        if body.get("approved") is not True:
            raise ServiceError(
                "publication audience approval is required",
                status=409,
                code="publication_approval_required",
            )
        transfer = dict(body.get("transfer") or {})
        info = self.inspect_organization_artifact(agent_id, body)
        if transfer.get("method") != "PUT":
            raise ServiceError(
                "upload capability required", status=400, code="invalid_transfer_authorization"
            )
        source = self.workspace_file(agent_id, body.get("path"))
        expected = int(transfer.get("headers", {}).get("Content-Length", info["size_bytes"]))
        if expected != info["size_bytes"]:
            raise ServiceError("artifact size changed", status=409, code="artifact_changed")
        with source.open("rb") as stream:
            with _open(transfer, data=stream.read()) as response:
                if response.status >= 300:
                    raise ServiceError(
                        "organization upload failed", status=503, code="storage_unavailable"
                    )
        return {
            **info,
            "file_id": body["file_id"],
            "version_id": body["version_id"],
            "ownership": "organization",
        }

    def import_organization_artifact(
        self, agent_id: str, body: Mapping[str, Any]
    ) -> dict[str, Any]:
        transfer = dict(body.get("transfer") or {})
        expected = int(body["size_bytes"])
        digest_expected = _digest(str(body["sha256"]))
        relative = _validate_create_path(body.get("destination"))
        root = self.agents.workspace_dir(agent_id).resolve()
        target = self.agents._workspace_path(agent_id, relative, require_file=True)
        blocked = {"memory", "skills", "config.yaml", "credentials", ".credentials", ".env"}
        parts = set(Path(relative).parts)
        if parts & blocked:
            raise ServiceError(
                "organization import destination is protected",
                status=403,
                code="protected_workspace_destination",
            )
        if target.exists():
            collision = body.get("collision", "cancel")
            if collision == "cancel":
                raise ServiceError(
                    "destination already exists", status=409, code="destination_exists"
                )
            if collision == "keep_both":
                stem, suffix = target.stem, target.suffix
                i = 2
                while target.exists():
                    target = target.with_name(f"{stem} ({i}){suffix}")
                    i += 1
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".org-import-", dir=target.parent)
        size = 0
        digest = hashlib.sha256()
        try:
            with os.fdopen(fd, "wb") as out:
                with _open(transfer) as response:
                    while True:
                        chunk = response.read(min(1024 * 1024, expected - size + 1))
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > expected:
                            raise ServiceError(
                                "artifact size mismatch", status=422, code="file_checksum_mismatch"
                            )
                        digest.update(chunk)
                        out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            if size != expected or digest.hexdigest() != digest_expected:
                raise ServiceError(
                    "artifact checksum mismatch", status=422, code="file_checksum_mismatch"
                )
            checkpoint = (
                self.checkpoints.mutation(
                    agent_id, f"before organization artifact import: {relative}"
                )
                if target.exists() and body.get("collision") == "replace"
                else nullcontext()
            )
            with checkpoint:
                os.replace(tmp, target)
            parent_fd = os.open(target.parent, os.O_RDONLY)
            os.fsync(parent_fd)
            os.close(parent_fd)
        finally:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
        return {
            "file_id": body["file_id"],
            "version_id": body["version_id"],
            "path": target.relative_to(root).as_posix(),
            "size_bytes": size,
            "sha256": digest.hexdigest(),
            "ownership": "personal",
        }
