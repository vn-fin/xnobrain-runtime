"""Prepare copied legacy data for the existing Runtime maintenance lifecycle.

Invoked by the node, with application units stopped, after transport verification.
The request contains paths and immutable identities, never executable migrations.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from ..models.runtime_updates import RuntimeRolloutRequest
from ..repositories.base import RepositoryBase
from ..repositories.runtime_updates import RuntimeUpdateRepository
from .runtime_build_identity import canonical_digest, incus_installed_identity
from .runtime_update_storage import RuntimeUpdateStorage


def bootstrap(request: dict, *, identity_directory: Path = Path("/etc/xnobrain")) -> str:
    binding = RuntimeRolloutRequest.model_validate(
        {key: request[key] for key in ("operation_id", "generation", "target")}
    ).model_dump()
    if incus_installed_identity(identity_directory) != binding["target"]:
        raise ValueError("candidate build identity mismatch")
    anchor = Path(request["data_path"])
    if str(anchor) not in {"/srv/xnobrain-data", "/opt/data"}:
        raise ValueError("unsupported data anchor")
    if anchor.is_symlink() or anchor.resolve() != anchor:
        raise ValueError("unsafe data anchor")
    paths = [Path(request[key]) for key in ("data_dir", "profiles_root", "root_profile")]
    if any(not path.resolve().is_relative_to(anchor) or not path.is_dir() for path in paths):
        raise ValueError("durable path escapes the copied data anchor")
    base = RepositoryBase(paths[0], paths[1], root_profile=paths[2])
    journal = RuntimeUpdateRepository(base)
    prior = journal.maintenance()
    if prior and any(prior.get(key) != binding[key] for key in binding):
        raise ValueError("another operation owns maintenance")
    operation = journal.operation(binding["operation_id"])
    if operation and any(operation.get(key) != binding[key] for key in binding):
        raise ValueError("operation identity changed")
    completed = operation.get("steps", {}).get("checkpoint")
    if completed:
        return completed["checkpoint_id"]
    storage = RuntimeUpdateStorage(*paths)
    storage.manifest()  # Validate before opening any databases for checkpointing.
    flushed = storage.flush_sqlite()
    manifest = storage.manifest()
    checkpoint_id = "ckp_" + canonical_digest({**binding, "manifest": manifest["digest"]})[7:]
    checkpoint = {
        **binding,
        "checkpoint_id": checkpoint_id,
        "manifest": manifest,
        "sqlite_checkpoints": flushed,
        "recovery_mode": "forward_only",
    }
    existing = journal.checkpoint(checkpoint_id)
    if existing and existing != checkpoint:
        raise ValueError("checkpoint binding changed")
    if not existing:
        journal.save_checkpoint(checkpoint)
    journal.save_maintenance(
        {**binding, "dispatch_paused": True, "phase": "checkpointed", "checkpoint_id": checkpoint_id}
    )
    journal.save_operation({**binding, "steps": {"checkpoint": checkpoint}})
    return checkpoint_id


def main() -> None:
    try:
        source = Path(sys.argv[1])
        if source.is_symlink() or source.stat().st_size > 16384:
            raise ValueError("invalid bootstrap request")
        request = json.loads(source.read_text(encoding="utf-8"))
        os.environ["RUNTIME_UPDATE_DATA_PATH"] = request["data_path"]
        print(bootstrap(request))
    except (OSError, ValueError, KeyError, IndexError):
        # This CLI may be captured in gateway logs. Never print paths/content.
        raise SystemExit("Runtime maintenance bootstrap unavailable") from None


if __name__ == "__main__":
    main()
