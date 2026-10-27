"""Stopped-service operator CLI; no HTTP route or workload-token override."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from ..repositories.base import RepositoryBase
from ..repositories.runtime_updates import RuntimeUpdateRepository
from ..services.runtime_update_recovery import accept_expired_transfers
from .runtime_source_checkpoint import require_stopped
from .runtime_update_storage import RuntimeUpdateStorage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("data-dir", "profiles-root", "root-profile", "paths-file"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("operation-id", "checkpoint-id", "manifest-digest", "reason"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--generation", type=int, required=True)
    parser.add_argument("--attest-expired-unused", action="store_true")
    args = parser.parse_args()
    require_stopped()
    roots = (args.data_dir, args.profiles_root, args.root_profile)
    if any(not path.is_absolute() or path.resolve() != path or not path.is_dir() for path in roots):
        parser.error("Durable roots must be existing canonical absolute directories")
    if os.geteuid() != args.data_dir.stat().st_uid:
        parser.error("Run as the OS account owning the Runtime data directory")
    paths = json.loads(args.paths_file.read_text())
    if not isinstance(paths, list):
        parser.error("paths-file must contain a JSON array of exact checkpoint paths")
    base = RepositoryBase(args.data_dir, args.profiles_root, root_profile=args.root_profile)
    receipt = accept_expired_transfers(
        RuntimeUpdateRepository(base),
        RuntimeUpdateStorage(*roots),
        operation_id=args.operation_id,
        generation=args.generation,
        checkpoint_id=args.checkpoint_id,
        manifest_digest=args.manifest_digest,
        paths=paths,
        reason=args.reason,
        attest_expired_unused=args.attest_expired_unused,
    )
    print(
        json.dumps(
            {
                "accepted": len(receipt["paths"]),
                "operation_id": receipt["operation_id"],
                "maintenance_released": False,
            }
        )
    )


if __name__ == "__main__":
    main()
