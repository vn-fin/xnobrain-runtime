"""Service-admitted compatibility path for legacy Community packages."""

import hashlib

from ..repositories.base import StoreError
from ..services.community_receipts import legacy_receipt
from ..trusted_context import (
    SNAPSHOT_OPERATION_HEADER,
    SNAPSHOT_SHA_HEADER,
    SNAPSHOT_SIZE_HEADER,
    from_request,
    snapshot_authorized,
)


async def deliver(handler, request, body, action):
    if not snapshot_authorized(request):
        raise StoreError("Service authorization required", status=403, code="permission_denied")
    raw = await request.body()
    operation = request.headers[SNAPSHOT_OPERATION_HEADER]
    if (
        hashlib.sha256(raw).hexdigest() != request.headers[SNAPSHOT_SHA_HEADER]
        or str(len(raw)) != request.headers[SNAPSHOT_SIZE_HEADER]
        or body["package"].get("id") != operation
    ):
        raise StoreError("Delivery mismatch", status=403, code="permission_denied")
    context = from_request(request)
    if action == "install":
        return handler.service.marketplace.install(body["package"], recipient=context)
    legacy_receipt(handler.service.repository, context, operation, "")
    return handler.service.marketplace.update(body["package"], body["local_profile_id"])
