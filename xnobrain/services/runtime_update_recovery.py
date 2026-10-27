"""Node-local operator acceptance of expired upload loss, never a new baseline."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing

from ..integrations.runtime_update_storage import RuntimeUpdateStorageError
from ..repositories.runtime_update_gate import checkpoint_gate, update_operation
from .base import iso

STEP = "expired_transfer_acceptance"
BINDING = ("operation_id", "generation", "target", "checkpoint_id")


def accepted_paths(repository, checkpoint) -> tuple[str, ...]:
    """Load only an acceptance bound to this exact immutable checkpoint."""
    operation = repository.operation(checkpoint["operation_id"])
    receipt = operation.get("steps", {}).get(STEP)
    if not receipt:
        return ()
    if (
        receipt.get("version") != 1
        or any(receipt.get(key) != checkpoint.get(key) for key in BINDING)
        or receipt.get("manifest_digest") != checkpoint["manifest"]["digest"]
        or receipt.get("attestation") != "expired-and-unused"
        or not isinstance(receipt.get("paths"), list)
        or not receipt["paths"]
        or any(not isinstance(path, str) for path in receipt["paths"])
    ):
        raise RuntimeUpdateStorageError(
            "Expired transfer acceptance does not match checkpoint",
            code="runtime_update_acceptance_invalid",
        )
    return tuple(receipt["paths"])


def accept_expired_transfers(
    repository,
    storage,
    *,
    operation_id,
    generation,
    checkpoint_id,
    manifest_digest,
    paths,
    reason,
    attest_expired_unused=False,
):
    """Called by the stopped-service CLI under the workspace OS identity.

    Expiry is an explicit operator attestation: legacy manifests retain hashes,
    not the deleted metadata contents. All other evidence is checked here.
    """
    if not attest_expired_unused or not isinstance(reason, str) or not 10 <= len(reason) <= 1000:
        raise ValueError("Explicit expired/unused attestation and audit reason required")
    paths = list(paths)
    if not paths or any(not isinstance(path, str) for path in paths):
        raise ValueError("Exact missing transfer paths required")
    if len(set(paths)) != len(paths):
        raise ValueError("Duplicate missing transfer paths")
    with update_operation(repository.base.data_dir), checkpoint_gate(repository.base.data_dir):
        checkpoint = repository.checkpoint(checkpoint_id)
        if (
            not checkpoint
            or checkpoint.get("operation_id") != operation_id
            or checkpoint.get("generation") != generation
            or checkpoint.get("checkpoint_id") != checkpoint_id
            or checkpoint["manifest"]["digest"] != manifest_digest
        ):
            raise ValueError("Checkpoint binding mismatch")
        maintenance = repository.maintenance()
        operation = repository.operation(operation_id)
        if (
            not maintenance.get("dispatch_paused")
            or any(maintenance.get(key) != checkpoint.get(key) for key in BINDING)
            or any(operation.get(key) != checkpoint.get(key) for key in BINDING[:3])
            or operation.get("steps", {}).get("checkpoint") != checkpoint
        ):
            raise ValueError("Active maintenance operation/checkpoint mismatch")
        steps = dict(operation.get("steps", {}))
        existing = steps.get(STEP)
        if existing:
            if (
                set(accepted_paths(repository, checkpoint)) != set(paths)
                or existing["reason"] != reason
            ):
                raise ValueError("Existing acceptance cannot be widened or replaced")
            return existing
        if steps.get("post_verify", {}).get("verified") is not False:
            raise ValueError("A failed post-verification is required before recovery")
        allowed = storage.validate_transfer_acceptance(checkpoint["manifest"], paths)
        current = storage.manifest(version=checkpoint["manifest"].get("version", 1))
        before = {entry["path"] for entry in checkpoint["manifest"]["entries"]}
        after = {entry["path"] for entry in current["entries"]}
        if before - after != allowed:
            raise ValueError("Approval must list exactly every currently missing file")
        database = repository.base.data_dir / "portability.sqlite3"
        if database.is_symlink() or not database.is_file():
            raise ValueError("Authoritative upload pin database unavailable")
        # Read-only: do not initialize/migrate the task database after checkpoint.
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
            for upload_id in {path.split("/")[3] for path in allowed}:
                if connection.execute(
                    "SELECT 1 FROM portability_pins WHERE upload_id=?", (upload_id,)
                ).fetchone():
                    raise ValueError("Upload is pinned by an import task")
        verification = storage.verify(checkpoint["manifest"], tuple(paths))
        if not verification["preserved"]:
            raise ValueError("Unrelated durable data changed; acceptance refused")
        receipt = {
            "version": 1,
            **{key: checkpoint[key] for key in BINDING},
            "manifest_digest": manifest_digest,
            "paths": sorted(paths),
            "attestation": "expired-and-unused",
            "reason": reason,
            "operator_uid": os.geteuid(),
            "accepted_at": iso(),
            "data_verification": verification,
        }
        steps[STEP] = receipt
        repository.save_operation({**operation, "steps": steps, "updated_at": iso()})
        return receipt
