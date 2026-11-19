"""Create a fenced source-update checkpoint with the application stopped.

The gateway runs this module from the verified staged target tree. No server,
connector, or profile initialization is started. Existing evidence is immutable.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from ..models.runtime_updates import RuntimeRolloutRequest
from ..repositories.base import RepositoryBase
from ..repositories.runtime_update_gate import checkpoint_gate, update_operation
from ..repositories.runtime_updates import RuntimeUpdateRepository
from ..services.base import iso
from .runtime_build_identity import source_installed_identity
from .runtime_data_volume import verify_volume
from .runtime_update_storage import RuntimeUpdateStorage, RuntimeUpdateStorageError

LOCK_VERIFICATION_METHODS = {
    "verify-extension-locks": "extension_locks_v1",
    "verify-native-cache-lock": "native_cache_lock_v1",
}


def require_stopped(unit: str = "xnobrain-api.service") -> None:
    if unit not in {"xnobrain-api.service", "xnobrain-runtime.service"}:
        raise ValueError("unsupported Runtime service")
    result = subprocess.run(
        ["/usr/bin/systemctl", "show", "--property=ActiveState,MainPID,ControlPID", unit],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if (
        values.get("MainPID") != "0"
        or values.get("ControlPID") != "0"
        or values.get("ActiveState") not in {"inactive", "failed"}
    ):
        raise ValueError("Runtime must be stopped before checkpoint")


def _context(request: dict, data_anchor: Path, *, stopped: bool = True):
    unit = (
        "xnobrain-runtime.service" if data_anchor == Path("/opt/data") else "xnobrain-api.service"
    )
    if stopped:
        require_stopped(unit)
    binding = RuntimeRolloutRequest.model_validate(
        {key: request[key] for key in ("operation_id", "generation", "target")}
    ).model_dump()
    if binding["target"]["kind"] != "runtime_source_v1":
        raise ValueError("source checkpoint requires a source target")
    anchor = Path(request["data_path"])
    if anchor != data_anchor or anchor.resolve() != anchor:
        raise ValueError("unsupported data anchor")
    # A managed Incus filesystem volume may be virtiofs. Verify the exact
    # writable mount and its existing Control-owned marker, not the custom-page
    # local-filesystem policy. Recovery must never initialize/adopt a volume.
    verify_volume(anchor, os.environ.get("RUNTIME_DATA_VOLUME_ID", ""))
    paths = [Path(request[key]) for key in ("data_dir", "profiles_root", "root_profile")]
    if any(
        path.resolve() != path or not path.is_relative_to(anchor) or not path.is_dir()
        for path in paths
    ):
        raise ValueError("unsafe durable data paths")
    base = RepositoryBase(paths[0], paths[1], root_profile=paths[2])
    return paths, binding, RuntimeUpdateRepository(base)


def checkpoint(request: dict, *, data_anchor: Path = Path("/srv/xnobrain-data")) -> str:
    paths, binding, journal = _context(request, data_anchor)
    with update_operation(paths[0]), checkpoint_gate(paths[0]):
        maintenance = journal.maintenance()
        if not maintenance.get("dispatch_paused") or any(
            maintenance.get(key) != value for key, value in binding.items()
        ):
            raise ValueError("maintenance ownership mismatch")
        operation = journal.operation(binding["operation_id"])
        if not operation or any(operation.get(key) != value for key, value in binding.items()):
            raise ValueError("operation ownership mismatch")
        steps = dict(operation.get("steps") or {})
        completed = steps.get("checkpoint")
        checkpoint_id = maintenance.get("checkpoint_id")
        if completed or checkpoint_id:
            checkpoint_id = checkpoint_id or completed["checkpoint_id"]
            existing = journal.checkpoint(checkpoint_id)
            if (
                not existing
                or any(existing.get(key) != value for key, value in binding.items())
                or (completed and completed != existing)
                or existing.get("checkpoint_id") != checkpoint_id
            ):
                raise ValueError("checkpoint identity mismatch")
            completed = existing
        else:
            if maintenance.get("phase") != "drained" or not steps.get("drain", {}).get("drained"):
                raise ValueError("Runtime was not drained")
            # A crash can leave the immutable checkpoint ahead of both pointers.
            # Reconcile it before touching the data, never create a new baseline.
            matches = []
            for path in journal.checkpoints.glob("*.json"):
                existing = journal._read(path)
                if existing.get("operation_id") == binding["operation_id"]:
                    if (
                        any(existing.get(key) != value for key, value in binding.items())
                        or existing.get("checkpoint_id") != path.stem
                    ):
                        raise ValueError("checkpoint ownership mismatch")
                    matches.append(existing)
            if len(matches) > 1:
                raise ValueError("ambiguous checkpoint evidence")
            if matches:
                completed = matches[0]
                checkpoint_id = completed["checkpoint_id"]
        if completed is None:
            storage = RuntimeUpdateStorage(*paths)
            flushed = storage.flush_sqlite()
            manifest = storage.manifest()
            checkpoint_id = (
                "ckp_"
                + hashlib.sha256(
                    json.dumps(
                        {**binding, "manifest": manifest["digest"]},
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest()
            )
            completed = {
                **binding,
                "checkpoint_id": checkpoint_id,
                "manifest": manifest,
                "sqlite_checkpoints": flushed,
                "created_at": iso(),
                "recovery_mode": "forward_only",
            }
            journal.save_checkpoint(completed)
        maintenance.update(phase="checkpointed", checkpoint_id=checkpoint_id, updated_at=iso())
        journal.save_maintenance(maintenance)
        steps["checkpoint"] = completed
        journal.save_operation({**operation, "steps": steps, "updated_at": iso()})
        return checkpoint_id


def _fenced_checkpoint(journal, binding: dict, checkpoint_id: str):
    maintenance = journal.maintenance()
    operation = journal.operation(binding["operation_id"])
    steps = dict(operation.get("steps") or {})
    if (
        maintenance.get("kind") != "runtime_source"
        or maintenance.get("phase") != "checkpointed"
        or not maintenance.get("dispatch_paused")
        or not checkpoint_id
        or maintenance.get("checkpoint_id") != checkpoint_id
        or any(maintenance.get(key) != value for key, value in binding.items())
        or any(operation.get(key) != value for key, value in binding.items())
        or steps.get("resume")
        or steps.get("abort_unchanged")
    ):
        raise ValueError("source restore ownership mismatch")
    existing = journal.checkpoint(checkpoint_id)
    if (
        not existing
        or existing.get("checkpoint_id") != checkpoint_id
        or any(existing.get(key) != value for key, value in binding.items())
        or steps.get("checkpoint") != existing
    ):
        raise ValueError("source restore checkpoint mismatch")
    return operation, steps, existing


def restore_ready(request: dict, *, data_anchor: Path = Path("/srv/xnobrain-data")) -> dict:
    """Prove unchanged data while stopped, without refreshing the checkpoint."""
    paths, binding, journal = _context(request, data_anchor)
    with update_operation(paths[0]), checkpoint_gate(paths[0]):
        checkpoint_id = request.get("checkpoint_id")
        operation, steps, existing = _fenced_checkpoint(journal, binding, checkpoint_id)
        # Do not flush SQLite or regenerate evidence: failure is not permission
        # to accept target-startup mutations as a new baseline.
        verification = RuntimeUpdateStorage(*paths).verify(existing["manifest"])
        if not verification["preserved"]:
            raise ValueError("source restore data changed")
        result = {
            **binding,
            "checkpoint_id": checkpoint_id,
            "restore_ready": True,
            "checked_at": iso(),
        }
        steps["restore_ready"] = result
        journal.save_operation({**operation, "steps": steps, "updated_at": iso()})
        return result


def verify_extension_locks(request: dict, *, data_anchor: Path) -> dict:
    """Recheck the original v3 checkpoint under the running service's durable gates."""
    action = request.get("action", "verify-extension-locks")
    method = LOCK_VERIFICATION_METHODS[action]
    paths, binding, journal = _context(request, data_anchor, stopped=False)
    checkpoint_id = request["checkpoint_id"]
    with update_operation(paths[0]), checkpoint_gate(paths[0]):
        operation, steps, existing = _fenced_checkpoint(journal, binding, checkpoint_id)
        last = steps.get("post_verify") or {}
        original = steps.get("post_verify_before_lock_recovery")
        failed = original or last
        ready = steps.get("readiness") or {}
        installed = source_installed_identity()
        if (
            journal.maintenance().get("recovery_target")
            or existing["manifest"].get("version") != 3
            or ready.get("ready") is not True
            or ready.get("installed") != binding["target"]
            or installed != binding["target"]
            or not failed
            or failed.get("installed") != installed
            or failed.get("installed_identity_matches") is not True
            or failed.get("data_preserved") is not False
            or failed.get("verified") is not False
            or any(
                receipt.get(key) != binding[key]
                for key in ("operation_id", "generation")
                for receipt in (ready, failed)
            )
        ):
            raise ValueError("missing fenced legacy verification evidence")
        verification = RuntimeUpdateStorage(*paths).verify(existing["manifest"])
        count = verification.get("extension_locks_added", 0)
        changes = verification.get("changes", {})
        cache_added = verification.get("native_package_cache_lock_added", False)
        if action == "verify-native-cache-lock":
            expected_locks = (
                cache_added
                and count == 1
                and changes.get("log_appends") == 0
                and changes.get("sqlite_equivalent") == 0
                and failed.get("data_verification", {}).get("changes") == changes
            )
        else:
            expected_locks = not cache_added and count in (1, 2)
        if (
            not verification["preserved"]
            or not expected_locks
            or failed.get("data_loss_accepted")
            or changes.get("added") != count
            or changes.get("removed") != 0
            or changes.get("changed") != 0
            or failed.get("manifest_digest") != verification["manifest_digest"]
        ):
            raise ValueError("original checkpoint failed extension lock verification")
        result = {
            **binding,
            "checkpoint_id": checkpoint_id,
            "installed": installed,
            "installed_identity_matches": True,
            "verified": True,
            "data_preserved": True,
            "manifest_digest": verification["manifest_digest"],
            "data_verification": verification,
            "verification_method": method,
        }
        # A lost reply can re-enter the helper directly, or the installed
        # Runtime can repeat its failed scan before the Gateway calls us again.
        if last.get("verified") is True:
            if any(last.get(key) != value for key, value in result.items()):
                raise ValueError("previous lock verification proof differs")
            return last
        if original and {**last, "verified_at": None} != {**original, "verified_at": None}:
            raise ValueError("original failed scan differs")
        result["verified_at"] = iso()
        steps.setdefault("post_verify_before_lock_recovery", failed)
        steps["post_verify"] = result
        journal.save_operation({**operation, "steps": steps, "updated_at": iso()})
        return result


def main() -> None:
    try:
        body = sys.stdin.buffer.read(16385)
        if len(body) > 16384:
            raise ValueError("checkpoint request too large")
        request = json.loads(body)
        os.environ["RUNTIME_UPDATE_DATA_PATH"] = request["data_path"]
        data_anchor = Path(request["data_path"])
        if data_anchor not in {Path("/opt/data"), Path("/srv/xnobrain-data")}:
            raise ValueError("unsupported managed data anchor")
        action = request.get("action", "checkpoint")
        if action == "checkpoint":
            print(checkpoint(request, data_anchor=data_anchor))
        elif action == "restore-ready":
            print(json.dumps(restore_ready(request, data_anchor=data_anchor)))
        elif action in LOCK_VERIFICATION_METHODS:
            print(json.dumps(verify_extension_locks(request, data_anchor=data_anchor)))
        else:
            raise ValueError("unsupported offline action")
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
        RuntimeUpdateStorageError,
    ):
        raise SystemExit("Runtime offline checkpoint unavailable") from None


if __name__ == "__main__":
    main()
