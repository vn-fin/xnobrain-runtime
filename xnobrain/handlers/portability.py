"""Portable bundle transfer HTTP handlers."""

import asyncio
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import Response, StreamingResponse

from ..repositories.base import StoreError
from ..services import EXPECTED_ERRORS
from ..services.portability import CHUNK_SIZE, MAX_COMPRESSED
from ..trusted_context import (
    SNAPSHOT_OPERATION_HEADER,
    SNAPSHOT_SHA_HEADER,
    SNAPSHOT_SIZE_HEADER,
    from_request,
    snapshot_authorized,
)


def _snapshot_chunks(path: Path):
    try:
        with path.open("rb") as archive:
            while chunk := archive.read(CHUNK_SIZE):
                yield chunk
    finally:
        path.unlink(missing_ok=True)


class PortabilityHandlers:
    async def community_snapshot(self, request: Request) -> Response:
        if not snapshot_authorized(request):
            return self.failure(
                StoreError(
                    "Snapshot service authorization required", status=403, code="permission_denied"
                )
            )
        try:
            if request.scope["route"].name == "community_snapshot_export":
                agent_id = request.path_params["agent_id"]
                with tempfile.NamedTemporaryFile(
                    prefix=".community-",
                    suffix=".zip",
                    dir=self.service.portability.transfer_root,
                    delete=False,
                ) as temporary:
                    archive_path = Path(temporary.name)
                try:
                    metadata = await asyncio.to_thread(
                        self.service.portability.export_shareable,
                        agent_id,
                        archive_path,
                        owner=from_request(request),
                    )
                    response = StreamingResponse(
                        _snapshot_chunks(archive_path),
                        media_type="application/zip",
                        headers={
                            "X-Snapshot-SHA256": metadata["sha256"],
                            "Content-Length": str(metadata["size"]),
                            "X-Snapshot-Size": str(metadata["size"]),
                            "X-Snapshot-Inventory": json.dumps(
                                metadata["inventory"], separators=(",", ":")
                            ),
                        },
                    )
                    response.headers["Cache-Control"] = "private, no-store"
                    return response
                except Exception:
                    archive_path.unlink(missing_ok=True)
                    raise
            if (
                request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                != "application/zip"
            ):
                raise StoreError("ZIP content type required", code="invalid_bundle")
            if not from_request(request).subject:
                raise StoreError("Verified identity required", status=403, code="permission_denied")
            with tempfile.NamedTemporaryFile(
                prefix=".community-",
                suffix=".zip",
                dir=self.service.portability.transfer_root,
                delete=False,
            ) as temporary:
                archive_path = Path(temporary.name)
                digest = hashlib.sha256()
                size = 0
                try:
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > MAX_COMPRESSED:
                            raise StoreError(
                                "snapshot exceeds size limit", status=413, code="invalid_bundle"
                            )
                        digest.update(chunk)
                        temporary.write(chunk)
                except Exception:
                    archive_path.unlink(missing_ok=True)
                    raise
            try:
                expected_digest = request.headers[SNAPSHOT_SHA_HEADER]
                expected_size = int(request.headers[SNAPSHOT_SIZE_HEADER])
                if expected_digest != digest.hexdigest() or expected_size != size:
                    raise StoreError(
                        "snapshot digest or size mismatch", code="invalid_bundle_checksum"
                    )
                operation_id = request.headers[SNAPSHOT_OPERATION_HEADER]
                context = from_request(request)
                report = await asyncio.to_thread(
                    self.service.portability.apply_shareable_file,
                    archive_path,
                    operation_id=operation_id,
                    digest=digest.hexdigest(),
                    recipient="\x00".join((context.subject, context.tenant_id, context.organization_id)),
                )
            finally:
                archive_path.unlink(missing_ok=True)
            self.service.agents.sync_profiles_registry()
            report["sha256"] = digest.hexdigest()
            report["size"] = size
            self.service._cache.invalidate("agents")
            response = self.success(report, "community snapshot imported", 201)
            response.headers["Cache-Control"] = "private, no-store"
            return response
        except EXPECTED_ERRORS as error:
            return self.failure(error)

    @staticmethod
    def bundle_task_identity(request: Request) -> tuple[str, str]:
        context = from_request(request)
        if context.subject:
            return f"{context.tenant_id}:{context.organization_id}", context.subject
        if os.getenv("RUNTIME_INTERNAL_SERVICE_TOKEN", "").strip():
            raise StoreError("Verified identity required", status=403, code="permission_denied")
        # Standalone Runtime is already protected by its host access policy and
        # has one workspace owner. Never derive this scope from browser fields.
        return "standalone", "owner"

    async def bundle_task(self, request: Request, body: dict[str, Any]) -> Response:
        try:
            scope, actor = self.bundle_task_identity(request)
            tasks = self.service.portability_tasks
            operation = request.scope["route"].name
            status = 200
            if operation in {"bundle_task_create", "bundle_task_import"}:
                result, _ = await asyncio.to_thread(
                    tasks.create_import
                    if operation == "bundle_task_import"
                    else tasks.create_export,
                    body,
                    scope=scope,
                    actor=actor,
                    key=request.headers.get("Idempotency-Key"),
                )
                status = 202 if result["status"] in {"PENDING", "PROCESSING"} else 200
            elif operation == "bundle_task_delete":
                result = await asyncio.to_thread(
                    tasks.delete_export,
                    request.path_params["transfer_id"],
                    scope=scope,
                    actor=actor,
                )
            elif operation == "bundle_task_get":
                result = await asyncio.to_thread(
                    tasks.get,
                    request.path_params["task_id"],
                    scope=scope,
                    actor=actor,
                )
            else:
                result = await asyncio.to_thread(
                    tasks.list,
                    scope=scope,
                    actor=actor,
                    before=request.query_params.get("cursor", ""),
                    limit=int(request.query_params.get("limit", "50")),
                )
            response = self.success(result, "Snapshot task", status)
            response.headers["Cache-Control"] = "private, no-store"
            return response
        except EXPECTED_ERRORS as error:
            response = self.failure(error)
            if response.status_code == 429:
                response.headers["Retry-After"] = "5"
            return response
        except (ValueError, TypeError):
            return self.failure(StoreError("Invalid task request", code="invalid_task_request"))

    async def bundle_export(self, _request: Request, body: dict[str, Any]) -> Response:
        try:
            payload, filename = self.service.export_bundle(body)
            return Response(
                payload,
                media_type="application/zip",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        except EXPECTED_ERRORS as error:
            return self.failure(error)

    async def bundle_upload(self, request: Request) -> Response:
        try:
            form = await request.form()
            upload = form.get("file")
            if upload is None or not hasattr(upload, "read"):
                raise ValueError("bundle file is required")
            payload = await upload.read()
            if hasattr(upload, "close"):
                await upload.close()
            operation = request.scope["route"].name
            if operation == "bundle_inspect":
                result, message, status = (
                    self.service.inspect_bundle(payload),
                    "bundle inspected successfully",
                    200,
                )
            elif operation == "bundle_dry_run":
                result, message, status = (
                    self.service.dry_run_bundle(payload),
                    "bundle dry run completed successfully",
                    200,
                )
            else:
                result, message, status = (
                    self.service.apply_bundle(payload),
                    "bundle imported successfully",
                    201,
                )
            return self.success(result, message, status)
        except EXPECTED_ERRORS as error:
            return self.failure(error)
        except (ValueError, KeyError, TypeError) as error:
            error.status, error.code = 400, "invalid_bundle"
            return self.failure(error)

    async def bundle_part(self, request: Request) -> Response:
        try:
            transfer_id = request.path_params["transfer_id"]
            part_number = request.path_params["part_number"]
            operation = request.scope["route"].name
            if operation == "bundle_upload_part":
                content_length = request.headers.get("content-length")
                if content_length is not None:
                    declared_length = int(content_length)
                    if declared_length <= 0 or declared_length > CHUNK_SIZE:
                        raise ValueError("upload part size is invalid")
                payload_buffer = bytearray()
                async for chunk in request.stream():
                    if len(payload_buffer) + len(chunk) > CHUNK_SIZE:
                        raise ValueError("upload part size is invalid")
                    payload_buffer.extend(chunk)
                if not payload_buffer:
                    raise ValueError("upload part size is invalid")
                payload = bytes(payload_buffer)
                expected_hash = str(request.headers.get("x-part-sha256") or "").lower()
                if (
                    expected_hash
                    and expected_hash != __import__("hashlib").sha256(payload).hexdigest()
                ):
                    raise ValueError("upload part checksum is invalid")
                result = self.service.put_bundle_upload_part(transfer_id, part_number, payload)
                return self.success(result, "bundle part uploaded", 201)
            if operation == "bundle_task_part":
                scope, actor = self.bundle_task_identity(request)
                payload, metadata = await asyncio.to_thread(
                    self.service.portability_tasks.read_part,
                    transfer_id,
                    part_number,
                    scope=scope,
                    actor=actor,
                )
            else:
                payload, metadata = self.service.bundle_export_part(transfer_id, part_number)
            return Response(
                payload,
                media_type="application/octet-stream",
                headers={
                    "Content-Disposition": f'attachment; filename="{metadata["filename"]}.part{int(part_number):08d}"',
                    "X-Transfer-Filename": metadata["filename"],
                    "X-Part-Number": str(part_number),
                    "X-Total-Parts": str(metadata["total_parts"]),
                    "X-Part-SHA256": __import__("hashlib").sha256(payload).hexdigest(),
                },
            )
        except EXPECTED_ERRORS as error:
            return self.failure(error)
        except (ValueError, KeyError, TypeError) as error:
            error.status, error.code = 400, "invalid_bundle_part"
            return self.failure(error)
