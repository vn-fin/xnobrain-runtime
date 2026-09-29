"""Private boot guard for Control-owned Incus filesystem volumes.

The guest marker checks consistency only. Control and the gateway authorize the
resource. This module deliberately imports no application or database modules.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from uuid import UUID

MARKER = ".xnobrain-volume.json"
INITIALIZED = ".xnobrain-volume-initialized"


def fresh_filesystem(anchor: Path) -> bool:
    """Allow only the empty recovery directory created by mkfs.ext4."""
    for entry in anchor.iterdir():
        info = entry.lstat()
        if (
            entry.name != "lost+found"
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != 0
            or any(entry.iterdir())
        ):
            return False
    return True


def verify_mount(anchor: Path, mountinfo: Path = Path("/proc/self/mountinfo")) -> None:
    """Require the exact anchor as a mount, including same-device bind mounts."""
    if anchor.is_symlink() or anchor.resolve() != anchor or not anchor.is_dir():
        raise ValueError("storage_mount_unavailable")
    encoded = str(anchor).replace("\\", "\\134").replace(" ", "\\040")
    if not any(
        len(row.split()) > 5 and row.split()[4] == encoded and "rw" in row.split()[5].split(",")
        for row in mountinfo.read_text().splitlines()
    ):
        raise ValueError("storage_mount_unavailable")


def verify_volume(
    anchor: Path, volume_id: str, *, initialize: bool = False, adopt: bool = False
) -> None:
    if str(UUID(volume_id)) != volume_id:
        raise ValueError("storage_binding_mismatch")
    verify_mount(anchor)
    marker = anchor / MARKER
    expected = {"schema_version": 1, "volume_id": volume_id}
    if not marker.exists() and (initialize or adopt):
        # Only trusted fresh provisioning may seed an empty mounted filesystem.
        # An initialized or partially populated volume is never inferred fresh.
        if not adopt and not fresh_filesystem(anchor):
            raise ValueError("storage_binding_mismatch")
        # Publish the complete marker atomically. The temporary lives outside
        # user profile trees and never authorizes a partially copied volume.
        with tempfile.NamedTemporaryFile(
            mode="w", dir=anchor, prefix=".volume-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            try:
                json.dump(expected, stream, sort_keys=True)
                stream.flush()
                os.fchmod(stream.fileno(), 0o644)
                os.fsync(stream.fileno())
                os.link(temporary, marker, follow_symlinks=False)
            finally:
                temporary.unlink(missing_ok=True)
        directory = os.open(anchor, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    fd = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 512:
            raise ValueError("storage_binding_mismatch")
        if json.load(stream) != expected:
            raise ValueError("storage_binding_mismatch")


def validate_archive(archive: Path) -> None:
    """Validate the complete path graph before GNU tar restores ACLs/xattrs.

    Leaf symlinks (including links to image tools) are preserved, but archive
    entries may never traverse one. Hardlinks must target archive regular files.
    No device, FIFO, reserved volume marker, or duplicate member is accepted.
    """
    if archive.is_symlink() or not archive.is_file():
        raise ValueError("storage_verification_failed")
    members: dict[PurePosixPath, tarfile.TarInfo] = {}
    with tarfile.open(archive, "r:") as stream:
        for member in stream:
            path = PurePosixPath(member.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or path.name in {MARKER, INITIALIZED}
                or path in members
                or not (member.isfile() or member.isdir() or member.issym() or member.islnk())
            ):
                raise ValueError("storage_verification_failed")
            if len(members) >= 1_000_000:
                raise ValueError("storage_verification_failed")
            members[path] = member
    for path, member in members.items():
        for parent in path.parents:
            if parent in members and not members[parent].isdir():
                raise ValueError("storage_verification_failed")
        if member.islnk():
            target = PurePosixPath(member.linkname)
            if target.is_absolute() or ".." in target.parts or not members.get(target):
                raise ValueError("storage_verification_failed")
            if not members[target].isfile():
                raise ValueError("storage_verification_failed")


def guard_environment() -> None:
    required = os.environ.get("RUNTIME_DATA_MOUNT_REQUIRED", "false").lower()
    if required in {"false", "0", ""}:
        return
    if required not in {"true", "1"}:
        raise ValueError("storage_policy_unavailable")
    anchor = Path(os.environ.get("RUNTIME_DATA_VOLUME_PATH", ""))
    if str(anchor) not in {"/opt/data", "/srv/xnobrain-data"}:
        raise ValueError("storage_mount_unavailable")
    verify_volume(
        anchor,
        os.environ.get("RUNTIME_DATA_VOLUME_ID", ""),
        initialize=os.environ.get("RUNTIME_DATA_VOLUME_INITIALIZE") == "true",
    )


def main() -> None:
    try:
        if len(sys.argv) == 4 and sys.argv[1] in {"verify", "adopt"}:
            verify_volume(Path(sys.argv[2]), sys.argv[3], adopt=sys.argv[1] == "adopt")
        elif len(sys.argv) == 3 and sys.argv[1] == "validate-archive":
            validate_archive(Path(sys.argv[2]))
        elif len(sys.argv) == 3 and sys.argv[1] == "mount":
            verify_mount(Path(sys.argv[2]))
        else:
            guard_environment()
    except (OSError, ValueError, TypeError, tarfile.TarError):
        raise SystemExit("Runtime persistent storage unavailable") from None


if __name__ == "__main__":
    main()
