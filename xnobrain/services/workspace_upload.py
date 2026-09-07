"""Bounded, resumable multipart uploads for agent workspaces."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path
from typing import Any

WORKSPACE_UPLOAD_CHUNK_BYTES = 768 * 1024
DEFAULT_MAX_WORKSPACE_UPLOAD_BYTES = 10 * 1024 * 1024 * 1024
UPLOAD_RETENTION_SECONDS = 24 * 60 * 60
_SAFE_UPLOAD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class WorkspaceUploadError(ValueError):
    """Expected validation or filesystem error for a workspace upload."""

    def __init__(
        self,
        message: str,
        *,
        status: int = 400,
        code: str = "invalid_workspace_upload",
    ):
        super().__init__(message)
        self.status = status
        self.code = code


class WorkspaceUploadService:
    """Persist upload parts outside workspaces and atomically publish the result."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o750)
        self._lock = threading.RLock()
        try:
            configured_limit = int(
                os.environ.get(
                    "MAX_WORKSPACE_UPLOAD_BYTES",
                    str(DEFAULT_MAX_WORKSPACE_UPLOAD_BYTES),
                )
            )
        except ValueError as exc:
            raise RuntimeError("MAX_WORKSPACE_UPLOAD_BYTES must be an integer") from exc
        if configured_limit < WORKSPACE_UPLOAD_CHUNK_BYTES:
            raise RuntimeError(
                f"MAX_WORKSPACE_UPLOAD_BYTES must be at least {WORKSPACE_UPLOAD_CHUNK_BYTES}"
            )
        self.max_upload_bytes = configured_limit
        self._cleanup_expired()

    def put_chunk(
        self,
        *,
        agent_id: str,
        upload_id: str,
        target: Path,
        workspace_root: Path,
        chunk_index: int,
        total_chunks: int,
        total_size: int,
        payload: bytes,
        publish_context: Callable[[], Any] | None = None,
    ) -> dict[str, Any]:
        upload_id = str(upload_id or "").strip()
        if not _SAFE_UPLOAD_ID.fullmatch(upload_id):
            raise WorkspaceUploadError("upload_id is invalid")
        if total_size < 0 or total_size > self.max_upload_bytes:
            raise WorkspaceUploadError(
                f"file is too large (max {self.max_upload_bytes} bytes)",
                status=413,
                code="workspace_file_too_large",
            )
        expected_chunks = max(
            1,
            (total_size + WORKSPACE_UPLOAD_CHUNK_BYTES - 1) // WORKSPACE_UPLOAD_CHUNK_BYTES,
        )
        if total_chunks != expected_chunks:
            raise WorkspaceUploadError("total_chunks does not match total_size")
        if chunk_index < 0 or chunk_index >= total_chunks:
            raise WorkspaceUploadError("chunk_index is out of range")
        expected_size = self._expected_part_size(chunk_index, total_chunks, total_size)
        if len(payload) != expected_size:
            raise WorkspaceUploadError(
                f"chunk size is invalid (expected {expected_size} bytes)",
                code="invalid_workspace_chunk",
            )

        workspace_root = workspace_root.resolve()
        target = target.resolve()
        if target == workspace_root or workspace_root not in target.parents:
            raise WorkspaceUploadError("upload path escapes the agent workspace")
        relative_target = target.relative_to(workspace_root).as_posix()
        metadata = {
            "agent_id": agent_id,
            "target": relative_target,
            "total_chunks": total_chunks,
            "total_size": total_size,
        }

        with self._lock:
            session = self.root / upload_id
            parts = session / "parts"
            stored = self._read_metadata(session)
            if stored is not None and stored != metadata:
                raise WorkspaceUploadError(
                    "upload_id is already used for another upload",
                    status=409,
                    code="workspace_upload_conflict",
                )
            parts.mkdir(parents=True, exist_ok=True, mode=0o750)
            if stored is None:
                self._atomic_write_json(session / "metadata.json", metadata)

            part = parts / f"{chunk_index:08d}.part"
            self._write_part(part, payload)
            received = [
                number for number in range(total_chunks) if (parts / f"{number:08d}.part").is_file()
            ]
            received_bytes = sum(
                (parts / f"{number:08d}.part").stat().st_size for number in received
            )
            if len(received) != total_chunks:
                return {
                    "object": "xnobrain.agent_workspace_upload",
                    "agent": agent_id,
                    "upload_id": upload_id,
                    "path": relative_target,
                    "chunk_index": chunk_index,
                    "received_chunks": len(received),
                    "total_chunks": total_chunks,
                    "received_bytes": received_bytes,
                    "size_bytes": total_size,
                    "complete": False,
                }

            target.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
            with publish_context() if publish_context is not None else nullcontext():
                self._assemble(parts, target, total_chunks, total_size)
            shutil.rmtree(session)
            self._sync_dir(self.root)
            return {
                "object": "xnobrain.agent_workspace_file",
                "agent": agent_id,
                "upload_id": upload_id,
                "path": relative_target,
                "received_chunks": total_chunks,
                "total_chunks": total_chunks,
                "received_bytes": total_size,
                "size_bytes": total_size,
                "complete": True,
            }

    @staticmethod
    def _expected_part_size(index: int, total_chunks: int, total_size: int) -> int:
        if index < total_chunks - 1:
            return WORKSPACE_UPLOAD_CHUNK_BYTES
        return total_size - (index * WORKSPACE_UPLOAD_CHUNK_BYTES)

    def _read_metadata(self, session: Path) -> dict[str, Any] | None:
        path = session / "metadata.json"
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkspaceUploadError(
                "upload state is corrupt",
                status=409,
                code="workspace_upload_conflict",
            ) from exc
        return value if isinstance(value, dict) else None

    @staticmethod
    def _write_part(path: Path, payload: bytes) -> None:
        if path.is_file():
            existing = path.read_bytes()
            if hashlib.sha256(existing).digest() == hashlib.sha256(payload).digest():
                return
            raise WorkspaceUploadError(
                "chunk was already uploaded with different content",
                status=409,
                code="workspace_upload_conflict",
            )
        fd, temporary_name = tempfile.mkstemp(prefix=".chunk-", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o640)
            os.replace(temporary, path)
            WorkspaceUploadService._sync_dir(path.parent)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _assemble(parts: Path, target: Path, total_chunks: int, total_size: int) -> None:
        fd, temporary_name = tempfile.mkstemp(prefix=".upload-", dir=target.parent)
        temporary = Path(temporary_name)
        written = 0
        try:
            with os.fdopen(fd, "wb") as output:
                for number in range(total_chunks):
                    part = parts / f"{number:08d}.part"
                    with part.open("rb") as source:
                        while block := source.read(64 * 1024):
                            output.write(block)
                            written += len(block)
                output.flush()
                os.fsync(output.fileno())
            if written != total_size:
                raise WorkspaceUploadError(
                    "assembled file size does not match total_size",
                    status=409,
                    code="workspace_upload_conflict",
                )
            os.chmod(temporary, 0o640)
            os.replace(temporary, target)
            WorkspaceUploadService._sync_dir(target.parent)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        fd, temporary_name = tempfile.mkstemp(prefix=".metadata-", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o640)
            os.replace(temporary, path)
            WorkspaceUploadService._sync_dir(path.parent)
        finally:
            temporary.unlink(missing_ok=True)

    def _cleanup_expired(self) -> None:
        cutoff = time.time() - UPLOAD_RETENTION_SECONDS
        for child in self.root.iterdir():
            if not child.is_dir():
                continue
            try:
                if child.stat().st_mtime < cutoff:
                    shutil.rmtree(child)
            except FileNotFoundError:
                continue

    @staticmethod
    def _sync_dir(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
