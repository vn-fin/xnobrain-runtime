"""Bounded multipart upload adapter for agent plugins."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from fastapi import Request

from ..services import EXPECTED_ERRORS
from ..trusted_context import from_request

MAX_PLUGIN_ARCHIVE = 3 * 1024 * 1024


class ExtensionUploadHandler:
    def __init__(self, handlers):
        self.handlers = handlers

    async def upload(self, request: Request):
        upload_path: Path | None = None
        temporary_path: Path | None = None
        form = None
        try:
            trusted = from_request(request)
            self.handlers.service.extensions._profile(request.path_params["agent_id"], trusted)
            form = await request.form(max_files=1, max_fields=4)
            plugin_id = form.get("plugin_id")
            file = form.get("file")
            if not isinstance(plugin_id, str) or not file or not hasattr(file, "read"):
                raise _UploadError(
                    "Plugin id and archive are required", "invalid_plugin_archive", 422
                )
            filename = str(getattr(file, "filename", "") or "")
            if Path(filename).suffix.lower() not in {".zip", ".plugin"}:
                raise _UploadError(
                    "Only .zip or .plugin archives are supported", "invalid_plugin_archive", 422
                )
            key = form.get("idempotency_key")
            if key is not None and (not isinstance(key, str) or not key or len(key) > 128):
                raise _UploadError("Idempotency key is invalid", "invalid_request", 422)
            root = self.handlers.service.extensions.repository.data_dir / "extension-staging"
            if root.is_symlink():
                raise _UploadError("Upload staging path is unsafe", "invalid_path", 409)
            uploads = root / "uploads"
            if uploads.is_symlink():
                raise _UploadError("Upload staging path is unsafe", "invalid_path", 409)
            uploads.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary_path = uploads / f"{uuid.uuid4()}.uploading"
            size = 0
            digest = hashlib.sha256()
            with temporary_path.open("xb") as output:
                while chunk := await file.read(64 * 1024):
                    size += len(chunk)
                    if size > MAX_PLUGIN_ARCHIVE:
                        raise _UploadError(
                            "Plugin archive exceeds 3 MiB", "plugin_archive_too_large", 413
                        )
                    digest.update(chunk)
                    output.write(chunk)
            operation_key = key or str(uuid.uuid4())
            identity = f"{request.path_params['agent_id']}\0{trusted.tenant_id}\0{trusted.subject}\0{operation_key}\0{digest.hexdigest()}"
            upload_id = str(uuid.uuid5(uuid.NAMESPACE_URL, identity)) if key else operation_key
            upload_path = uploads / f"{upload_id}.zip"
            if upload_path.is_symlink():
                raise _UploadError("Upload staging path is unsafe", "invalid_path", 409)
            if upload_path.exists():
                temporary_path.unlink(missing_ok=True)
            else:
                temporary_path.replace(upload_path)
            temporary_path = None
            operation = self.handlers.service.extensions.create_operation(
                request.path_params["agent_id"],
                {
                    "kind": "install",
                    "plugin_id": plugin_id,
                    "source": f"upload:{upload_id}",
                    "idempotency_key": operation_key,
                },
                trusted,
                _upload=True,
            )
            if operation.get("status") in {"PENDING", "PROCESSING"}:
                upload_path = None  # Worker owns the archive after accepting the operation.
            return self.handlers.success(operation, "extension operation accepted", 202)
        except EXPECTED_ERRORS as error:
            return self.handlers.failure(error)
        except _UploadError as error:
            return self.handlers.failure(error)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            if upload_path is not None:
                upload_path.unlink(missing_ok=True)
            if form is not None:
                await form.close()


class _UploadError(Exception):
    def __init__(self, message: str, code: str, status: int):
        super().__init__(message)
        self.code = code
        self.status = status
