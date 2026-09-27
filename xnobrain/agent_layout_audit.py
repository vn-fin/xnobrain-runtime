"""Read-only preflight for an explicitly selected legacy agent profile.

Run with python -m xnobrain.agent_layout_audit --source ... --destination ... .
No contents, credentials, or file names are printed; no migration is performed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


def inventory(source: Path, destination: Path) -> dict:
    if not source.is_absolute() or not destination.is_absolute():
        raise ValueError("source and destination must be absolute")
    if any(node.is_symlink() for path in (source, destination) for node in (path, *path.parents)):
        raise ValueError("symlink roots are not supported")
    source = source.resolve(strict=True)
    if not source.is_dir():
        raise ValueError("source must be a directory")
    destination = destination.resolve()
    if destination.exists() and not destination.is_dir():
        raise ValueError("destination must be a directory")
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError("source and destination must be disjoint")
    files = total = conflicts = links = special = 0
    digest = hashlib.sha256()
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            links += 1
            continue
        if path.is_dir():
            target = destination / path.relative_to(source)
            if target.is_symlink() or (target.exists() and not target.is_dir()):
                conflicts += 1
            continue
        if not path.is_file():
            special += 1
            continue
        relative = path.relative_to(source)
        before = path.stat()
        with path.open("rb") as stream:
            checksum = hashlib.file_digest(stream, "sha256").digest()
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino, before.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ino,
            after.st_ctime_ns,
        ):
            raise ValueError("source changed during inventory; stop writers first")
        digest.update(str(relative).encode() + b"\0" + checksum)
        files += 1
        total += before.st_size
        target = destination / relative
        if any(node.is_symlink() for node in (target, *target.parents)):
            conflicts += 1
        elif target.exists():
            if not target.is_file():
                conflicts += 1
            else:
                with target.open("rb") as stream:
                    conflicts += hashlib.file_digest(stream, "sha256").digest() != checksum
    parent = destination
    while not parent.exists():
        parent = parent.parent
    free = shutil.disk_usage(parent).free
    return {
        "mode": "dry-run",
        "files": files,
        "bytes": total,
        "conflicts": conflicts,
        "symlinks": links,
        "special_files": special,
        "manifest_sha256": digest.hexdigest(),
        "available_bytes": free,
        "copy_space_available": free >= total,
        "requires_offline_backup_and_write_fence": True,
        "activation_performed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(inventory(args.source, args.destination), indent=2))


if __name__ == "__main__":
    main()
