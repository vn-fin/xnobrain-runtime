"""Private Community installation HTTP adapter."""

import asyncio
import hashlib

from pydantic import ValidationError

from ..models.skill_community import CommunitySkillInstall
from ..repositories.base import StoreError
from ..services.base import ServiceError
from ..services.community_receipts import legacy_receipt
from ..trusted_context import (
    SNAPSHOT_OPERATION_HEADER,
    SNAPSHOT_SHA_HEADER,
    SNAPSHOT_SIZE_HEADER,
    from_request,
    snapshot_authorized,
)


class SkillCommunityHandlers:
    async def community_skill(self, request):
        try:
            if not snapshot_authorized(request):
                raise StoreError(
                    "Service authorization required", status=403, code="permission_denied"
                )
            context = from_request(request)
            operation = request.headers[SNAPSHOT_OPERATION_HEADER]
            if request.method == "GET":
                if request.path_params["operation_id"] != operation:
                    raise StoreError("Operation mismatch", status=403, code="permission_denied")
                if request.scope["route"].name == "community_legacy_receipt":
                    result = await asyncio.to_thread(
                        legacy_receipt,
                        self.service.repository,
                        context,
                        operation,
                        "sha256:" + request.headers[SNAPSHOT_SHA_HEADER],
                    )
                else:
                    result = await asyncio.to_thread(
                        self.service.skill_community.receipt, context, operation
                    )
            else:
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 15_000_000:
                        raise StoreError("Delivery too large", status=413, code="invalid_size")
                if (
                    str(len(body)) != request.headers[SNAPSHOT_SIZE_HEADER]
                    or hashlib.sha256(body).hexdigest() != request.headers[SNAPSHOT_SHA_HEADER]
                ):
                    raise StoreError(
                        "Delivery checksum mismatch", status=403, code="permission_denied"
                    )
                payload = CommunitySkillInstall.model_validate_json(body).model_dump()
                if payload["operation_id"] != operation:
                    raise StoreError("Operation mismatch", status=403, code="permission_denied")
                result = await asyncio.to_thread(
                    self.service.skill_community.install, context, payload
                )
            response = self.success(result, "ok", 200 if request.method == "GET" else 201)
        except (StoreError, ServiceError, ValidationError, ValueError, OSError) as error:
            response = self.failure(
                error
                if isinstance(error, (StoreError, ServiceError))
                else StoreError("Delivery failed", code="delivery_failed")
            )
        response.headers["Cache-Control"] = "private, no-store"
        return response
