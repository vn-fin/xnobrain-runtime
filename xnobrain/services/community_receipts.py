"""Read-only recovery for recipient-bound snapshot imports."""

import hashlib
import json

from ..repositories.base import StoreError


def snapshot_receipt(repository, context, operation, digest):
    repository._id(operation, "operation id")
    if not context.subject:
        raise StoreError("Verified recipient required", status=403, code="permission_denied")
    recipient = "\x00".join((context.subject, context.tenant_id, context.organization_id))
    target_id = (
        "clone-" + hashlib.sha256((recipient + "\x00" + operation).encode()).hexdigest()[:32]
    )
    target = repository.profile_path(target_id)
    marker = target / ".community-clone-receipt.json"
    if target.is_symlink() or marker.is_symlink():
        raise StoreError("Unsafe receipt", status=409, code="clone_conflict")
    try:
        saved = json.loads(marker.read_text())
    except (OSError, ValueError):
        raise StoreError("Receipt not found", status=404, code="not_found") from None
    if (
        saved.get("operation_id") != operation
        or saved.get("recipient") != recipient
        or saved.get("digest") != digest
    ):
        raise StoreError("Receipt not found", status=404, code="not_found")
    return {"sha256": digest, "agent_id_mappings": {saved["source_id"]: target_id}}


def legacy_receipt(repository, context, operation, digest):
    from .base import ServiceError
    from .portability import PortabilityService

    repository._id(operation, "operation id")
    target_id = "market-" + operation.removeprefix("inst_")
    target = repository.profile_path(target_id)
    marker = target / ".community-profile-owner.json"
    config_path = target / "config.yaml"
    if target.is_symlink() or marker.is_symlink() or config_path.is_symlink():
        raise ServiceError("Unsafe receipt", status=409, code="installation_profile_conflict")
    try:
        owner = json.loads(marker.read_text())
        config = repository._read_yaml(config_path)
    except (OSError, ValueError):
        raise ServiceError("Receipt not found", status=404, code="not_found") from None
    binding = config.get("xnobrain", {})
    if (
        owner != PortabilityService.owner_record(context)
        or not context.subject
        or binding.get("marketplace_installation_id") != operation
        or (digest and binding.get("package_digest") != digest)
    ):
        raise ServiceError("Receipt not found", status=404, code="not_found")
    return {
        "installation_id": operation,
        "local_profile_id": target_id,
        "digest": binding["package_digest"],
        "ownership": "customer",
        "execution_mode": "package_visible",
    }
