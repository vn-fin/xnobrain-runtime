"""Durable file-backed queue for bounded agent extension operations."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from .base import StoreError

_AGENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
TERMINAL = frozenset({"COMPLETED", "FAILED", "CANCELLED", "BLOCKED"})
RETENTION_SECONDS = 7 * 24 * 60 * 60


class ExtensionOperationStore:
    """Persist operations as owner-scoped atomic JSON records."""

    def __init__(self, data_dir: Path):
        self.root = Path(data_dir) / "extension-operations"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink():
            raise StoreError("Extension operation storage is unsafe", status=503)
        self.lock_path = self.root / ".lock"
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        self.worker_lock_path = self.root / ".worker.lock"
        fd = os.open(self.worker_lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)

    @staticmethod
    def _agent_hash(agent_id: str) -> str:
        if not _AGENT_ID.fullmatch(str(agent_id)):
            raise StoreError("Invalid agent id", status=400, code="invalid_agent_id")
        return hashlib.sha256(agent_id.encode()).hexdigest()

    def _path(self, agent_id: str) -> Path:
        return self.root / f"{self._agent_hash(agent_id)}.json"

    def _locked(self):
        return _FileLock(self.lock_path)

    def acquire_worker(self) -> int | None:
        """Fence queue reconciliation and processing to one Runtime process."""
        descriptor = os.open(self.worker_lock_path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            return None
        return descriptor

    @staticmethod
    def release_worker(descriptor: int) -> None:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)

    def _read(self, agent_id: str) -> list[dict[str, Any]]:
        path = self._path(agent_id)
        if path.is_symlink():
            raise StoreError("Extension operation storage is unsafe", status=503)
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError) as error:
            raise StoreError("Extension operation storage is invalid", status=503) from error
        if not isinstance(envelope, dict) or envelope.get("agent_id") != agent_id:
            raise StoreError("Extension operation storage is invalid", status=503)
        payload = envelope.get("operations")
        if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
            raise StoreError("Extension operation storage is invalid", status=503)
        return payload

    def _write(self, agent_id: str, rows: list[dict[str, Any]]) -> None:
        path = self._path(agent_id)
        fd, temporary = tempfile.mkstemp(prefix=".operation-", dir=self.root)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(
                    {"agent_id": agent_id, "operations": rows},
                    stream,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                stream.flush()
                os.fsync(stream.fileno())
            if rows:
                os.replace(temporary, path)
            else:
                os.unlink(temporary)
                path.unlink(missing_ok=True)
            directory = os.open(self.root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    def create(
        self,
        *,
        agent_id: str,
        owner: str,
        kind: str,
        inputs: dict[str, Any],
        idempotency_key: str,
    ) -> tuple[dict[str, Any], bool]:
        if not owner or not idempotency_key or len(idempotency_key) > 128:
            raise StoreError("Invalid operation identity", status=422, code="invalid_request")
        digest = hashlib.sha256(idempotency_key.encode()).hexdigest()
        now = time.time()
        with self._locked():
            rows = self._read(agent_id)
            self._prune(rows, now)
            for row in rows:
                if row.get("idempotency_digest") == digest and row.get("owner") == owner:
                    if row.get("inputs") != inputs or row.get("kind") != kind:
                        raise StoreError(
                            "Idempotency key conflicts with an earlier operation",
                            status=409,
                            code="idempotency_conflict",
                        )
                    self._write(agent_id, rows)
                    return self.public(row), False
            if any(row.get("status") in {"PENDING", "PROCESSING"} for row in rows):
                raise StoreError(
                    "An extension operation is already active for this profile",
                    status=409,
                    code="extension_operation_active",
                )
            operation = {
                "operation_id": uuid.uuid4().hex,
                "agent_id": agent_id,
                "owner": owner,
                "kind": kind,
                "inputs": inputs,
                "idempotency_digest": digest,
                "status": "PENDING",
                "revision": 1,
                "created_at": now,
                "updated_at": now,
                "progress": None,
                "result": None,
                "error": None,
                "cancel_requested": False,
            }
            rows.append(operation)
            self._write(agent_id, rows)
            return self.public(operation), True

    def list(self, agent_id: str, owner: str) -> list[dict[str, Any]]:
        with self._locked():
            rows = self._read(agent_id)
            return [self.public(row) for row in rows if row.get("owner") == owner]

    def get(self, agent_id: str, owner: str, operation_id: str) -> dict[str, Any]:
        with self._locked():
            row = self._find(self._read(agent_id), operation_id)
            self._require_owner(row, owner)
            return self.public(row)

    def claim_next(self) -> dict[str, Any] | None:
        with self._locked():
            for path in sorted(self.root.glob("*.json")):
                agent_id = self._agent_from_path(path)
                rows = self._read(agent_id)
                row = next((item for item in rows if item.get("status") == "PENDING"), None)
                if row is None:
                    continue
                row["status"] = "PROCESSING"
                row["revision"] = int(row.get("revision", 0)) + 1
                row["updated_at"] = time.time()
                self._write(agent_id, rows)
                return dict(row)
        return None

    def update(self, operation: dict[str, Any], *, status: str, **changes: Any) -> dict[str, Any]:
        with self._locked():
            rows = self._read(operation["agent_id"])
            row = self._find(rows, operation["operation_id"])
            if row.get("status") in TERMINAL:
                return self.public(row)
            row.update(changes)
            row["status"] = status
            row["revision"] = int(row.get("revision", 0)) + 1
            row["updated_at"] = time.time()
            self._write(operation["agent_id"], rows)
            return self.public(row)

    def cancel(self, agent_id: str, owner: str, operation_id: str) -> dict[str, Any]:
        with self._locked():
            rows = self._read(agent_id)
            row = self._find(rows, operation_id)
            self._require_owner(row, owner)
            if row["status"] == "PENDING":
                row.update(status="CANCELLED", cancel_requested=True)
            elif row["status"] == "PROCESSING":
                row["cancel_requested"] = True
            else:
                raise StoreError(
                    "Operation is already terminal", status=409, code="operation_terminal"
                )
            row["revision"] = int(row.get("revision", 0)) + 1
            row["updated_at"] = time.time()
            self._write(agent_id, rows)
            return self.public(row)

    def reconcile(self) -> None:
        with self._locked():
            for path in sorted(self.root.glob("*.json")):
                agent_id = self._agent_from_path(path)
                rows = self._read(agent_id)
                changed = False
                for row in rows:
                    if row.get("status") == "PROCESSING":
                        row.update(
                            status="FAILED",
                            error={"code": "worker_restarted", "message": "Operation interrupted"},
                            updated_at=time.time(),
                            revision=int(row.get("revision", 0)) + 1,
                        )
                        changed = True
                before_prune = len(rows)
                self._prune(rows, time.time())
                changed = changed or len(rows) != before_prune
                if changed:
                    self._write(agent_id, rows)

    def cancelled(self, operation: dict[str, Any]) -> bool:
        with self._locked():
            row = self._find(self._read(operation["agent_id"]), operation["operation_id"])
            return bool(row.get("cancel_requested"))

    @staticmethod
    def public(row: dict[str, Any]) -> dict[str, Any]:
        public = {
            key: row.get(key)
            for key in (
                "operation_id",
                "agent_id",
                "kind",
                "status",
                "revision",
                "created_at",
                "updated_at",
                "progress",
                "result",
                "error",
            )
        }
        inputs = row.get("inputs")
        public["plugin_id"] = inputs.get("plugin_id") if isinstance(inputs, dict) else None
        return public

    @staticmethod
    def _find(rows: list[dict[str, Any]], operation_id: str) -> dict[str, Any]:
        row = next((item for item in rows if item.get("operation_id") == operation_id), None)
        if row is None:
            raise StoreError("Extension operation not found", status=404, code="not_found")
        return row

    @staticmethod
    def _require_owner(row: dict[str, Any], owner: str) -> None:
        if row.get("owner") != owner:
            raise StoreError("Extension operation not found", status=404, code="not_found")

    @staticmethod
    def _agent_from_path(path: Path) -> str:
        # Filenames intentionally contain only a digest, so use the stored id.
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            agent_id = str(envelope["agent_id"])
            if not _AGENT_ID.fullmatch(agent_id):
                raise ValueError("invalid agent id")
            if not isinstance(envelope.get("operations"), list):
                raise ValueError("invalid operation list")
            return agent_id
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise StoreError("Extension operation storage is invalid", status=503) from error

    @staticmethod
    def _prune(rows: list[dict[str, Any]], now: float) -> None:
        rows[:] = [
            row
            for row in rows
            if row.get("status") not in TERMINAL
            or now - float(row.get("updated_at", now)) < RETENTION_SECONDS
        ]


class _FileLock:
    def __init__(self, path: Path):
        self.path = path
        self.fd: int | None = None

    def __enter__(self):
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *_args):
        assert self.fd is not None
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)
