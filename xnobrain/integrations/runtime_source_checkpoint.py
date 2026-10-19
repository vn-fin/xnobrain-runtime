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
from .runtime_update_storage import RuntimeUpdateStorage


def require_stopped() -> None:
    result = subprocess.run(
        ["systemctl", "show", "--property=ActiveState,MainPID", "xnobrain-api.service"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if values.get("MainPID") != "0" or values.get("ActiveState") not in {"inactive", "failed"}:
        raise ValueError("Runtime must be stopped before checkpoint")


def checkpoint(request: dict, *, data_anchor: Path = Path("/srv/xnobrain-data")) -> str:
    require_stopped()
    binding = RuntimeRolloutRequest.model_validate(
        {key: request[key] for key in ("operation_id", "generation", "target")}
    ).model_dump()
    if binding["target"]["kind"] != "runtime_source_v1":
        raise ValueError("source checkpoint requires a source target")
    anchor = Path(request["data_path"])
    if anchor != data_anchor or anchor.resolve() != anchor:
        raise ValueError("unsupported data anchor")
    paths = [Path(request[key]) for key in ("data_dir", "profiles_root", "root_profile")]
    if any(
        path.resolve() != path or not path.is_relative_to(anchor) or not path.is_dir()
        for path in paths
    ):
        raise ValueError("unsafe durable data paths")
    base = RepositoryBase(paths[0], paths[1], root_profile=paths[2])
    journal = RuntimeUpdateRepository(base)
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


def main() -> None:
    try:
        body = sys.stdin.buffer.read(16385)
        if len(body) > 16384:
            raise ValueError("checkpoint request too large")
        request = json.loads(body)
        os.environ["RUNTIME_UPDATE_DATA_PATH"] = request["data_path"]
        print(checkpoint(request))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        raise SystemExit("Runtime offline checkpoint unavailable") from None


if __name__ == "__main__":
    main()
