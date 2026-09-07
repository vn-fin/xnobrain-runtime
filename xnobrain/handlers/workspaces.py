"""Workspace upload, download, and document preview HTTP handlers."""

import asyncio
from typing import Any
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import FileResponse, Response

from ..services import EXPECTED_ERRORS
from ..services.workspace_upload import WORKSPACE_UPLOAD_CHUNK_BYTES


class WorkspaceHandlers:
    async def workspace_upload(self, request: Request) -> Response:
        try:
            form = await request.form()
            upload = form.get("file")
            if upload is None or not hasattr(upload, "read"):
                raise ValueError("file is required")
            content = await upload.read()
            if hasattr(upload, "close"):
                await upload.close()
            result = self.service.write_workspace(
                request.path_params["agent_id"],
                {
                    "path": str(form.get("path") or getattr(upload, "filename", "upload.bin")),
                    "content_base64": __import__("base64").b64encode(content).decode("ascii"),
                },
            )
            return self.success(result, "file uploaded successfully", 201)
        except EXPECTED_ERRORS as error:
            return self.failure(error)
        except ValueError as error:
            error.status, error.code = 400, "invalid_request"
            return self.failure(error)

    async def workspace_upload_chunk(self, request: Request) -> Response:
        try:
            form = await request.form()
            upload = form.get("chunk")
            if upload is None or not hasattr(upload, "read"):
                raise ValueError("chunk is required")
            content = await upload.read(WORKSPACE_UPLOAD_CHUNK_BYTES + 1)
            if hasattr(upload, "close"):
                await upload.close()
            if len(content) > WORKSPACE_UPLOAD_CHUNK_BYTES:
                error = ValueError(f"chunk is too large (max {WORKSPACE_UPLOAD_CHUNK_BYTES} bytes)")
                error.status, error.code = 413, "workspace_chunk_too_large"
                raise error
            result = self.service.upload_workspace_chunk(
                request.path_params["agent_id"],
                path=form.get("path"),
                file_name=form.get("file_name"),
                upload_id=form.get("upload_id"),
                chunk_index=int(str(form.get("chunk_index") or "")),
                total_chunks=int(str(form.get("total_chunks") or "")),
                total_size=int(str(form.get("total_size") or "")),
                payload=content,
            )
            status = 201 if result.get("complete") else 200
            message = (
                "file uploaded successfully"
                if result.get("complete")
                else "chunk uploaded successfully"
            )
            return self.success(result, message, status)
        except EXPECTED_ERRORS as error:
            return self.failure(error)
        except ValueError as error:
            if not hasattr(error, "status"):
                error.status, error.code = 400, "invalid_request"
            return self.failure(error)

    async def workspace_file(self, request: Request) -> Response:
        try:
            source = self.service.workspace_file(
                request.path_params["agent_id"],
                request.query_params.get("path"),
            )
            return FileResponse(
                source,
                filename=source.name,
                content_disposition_type="inline",
                headers={
                    "Cache-Control": "private, no-cache",
                    "X-Content-Type-Options": "nosniff",
                },
            )
        except EXPECTED_ERRORS as error:
            return self.failure(error)

    async def workspace_preview(self, request: Request) -> Response:
        try:
            preview = await asyncio.to_thread(
                self.service.preview_workspace,
                request.path_params["agent_id"],
                request.query_params.get("path"),
            )
            return Response(
                preview.content,
                media_type=preview.media_type,
                headers={
                    "Cache-Control": "private, max-age=300",
                    "Content-Disposition": f"inline; filename*=UTF-8''{quote(preview.filename, safe='')}",
                    "X-Content-Type-Options": "nosniff",
                },
            )
        except EXPECTED_ERRORS as error:
            return self.failure(error)

    async def workspace_workbook(self, request: Request) -> Response:
        try:
            preview = await asyncio.to_thread(
                self.service.workbook_workspace,
                request.path_params["agent_id"],
                request.query_params.get("path"),
            )
            return Response(
                preview.content,
                media_type=preview.media_type,
                headers={
                    "Cache-Control": "private, max-age=300",
                    "Content-Disposition": f"inline; filename*=UTF-8''{quote(preview.filename, safe='')}",
                    "X-Content-Type-Options": "nosniff",
                },
            )
        except EXPECTED_ERRORS as error:
            return self.failure(error)
