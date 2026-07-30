"""Portable bundle transfer HTTP handlers."""

from typing import Any

from fastapi import Request
from fastapi.responses import Response

from ..services import EXPECTED_ERRORS


class PortabilityHandlers:

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
                    result, message, status = self.service.inspect_bundle(payload), "bundle inspected successfully", 200
                elif operation == "bundle_dry_run":
                    result, message, status = self.service.dry_run_bundle(payload), "bundle dry run completed successfully", 200
                else:
                    result, message, status = self.service.apply_bundle(payload), "bundle imported successfully", 201
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
                    content_length = int(request.headers.get("content-length") or 0)
                    if content_length <= 0 or content_length > 4 * 1024 * 1024:
                        raise ValueError("upload part size is invalid")
                    payload = await request.body()
                    expected_hash = str(request.headers.get("x-part-sha256") or "").lower()
                    if expected_hash and expected_hash != __import__("hashlib").sha256(payload).hexdigest():
                        raise ValueError("upload part checksum is invalid")
                    result = self.service.put_bundle_upload_part(transfer_id, part_number, payload)
                    return self.success(result, "bundle part uploaded", 201)
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
