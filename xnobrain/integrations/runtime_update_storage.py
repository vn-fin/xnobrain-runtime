"""Safe local storage inspection for Runtime update pre/postconditions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat as file_stat
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..repositories.base import StoreError
from ..repositories.runtime_update_gate import is_coordination_file, validate_coordination_file
from ..repositories.storage_mount import StorageMountGuard
from ..repositories.workspace_memory import DIRECTORY as MEMORY_ACTIVITY_DIRECTORY
from . import runtime_update_sqlite

# Update journals and boot-scoped memory activity state are not durable user
# data. The activity state is recreated by the next Runtime boot, so hashing it
# would make every image replacement fail post-verification.
EXCLUDED_DATA_DIRECTORIES = ("runtime-updates", MEMORY_ACTIVITY_DIRECTORY)
# Manifest v3 hashes small files exactly and fingerprints larger files by size
# and modification time, so a checkpoint never rereads every durable byte.
MANIFEST_VERSION = 3
CONTENT_DIGEST_MAX_BYTES = 1024 * 1024


class RuntimeUpdateStorageError(RuntimeError):
    def __init__(self, message: str, *, code: str, diagnostics: dict[str, str] | None = None):
        super().__init__(message)
        self.code = code
        self.diagnostics = diagnostics or {}


class RuntimeUpdateStorage:
    """Inspect durable roots directly; never invoke a host shell."""

    def __init__(self, data_dir: Path, profiles_root: Path, root_profile: Path):
        self.storage_mount = StorageMountGuard(data_dir)
        self.data_dir = data_dir.resolve()
        self.profiles_root = profiles_root.resolve()
        self.root_profile = root_profile.resolve()
        self.data_anchor = Path(os.getenv("RUNTIME_UPDATE_DATA_PATH", "/opt/data")).resolve()
        self.max_files = max(1, int(os.getenv("RUNTIME_UPDATE_MAX_MANIFEST_FILES", "200000")))
        self.max_bytes = max(1, int(os.getenv("RUNTIME_UPDATE_MAX_MANIFEST_BYTES", str(1 << 40))))

    def _unsafe_data(self, path: Path, check: str, message: str) -> RuntimeUpdateStorageError:
        # Identify the offending entry, never its resolved target or file content.
        # Use lexical paths: resolving a rejected symlink would expose outside roots.
        diagnostics = {"storage_check": check, "storage_root": "unknown"}
        roots = (
            ("data", self.data_dir),
            ("profiles", self.profiles_root),
            ("root_profile", self.root_profile),
        )
        for label, root in sorted(roots, key=lambda item: len(item[1].parts), reverse=True):
            if path.is_relative_to(root):
                relative = path.relative_to(root)
                if ".." not in relative.parts:
                    diagnostics["storage_root"] = label
                    diagnostics["relative_path"] = "".join(
                        char if char.isprintable() else "?" for char in relative.as_posix()
                    )[:512]
                break
        return RuntimeUpdateStorageError(
            message, code="runtime_update_unsafe_data", diagnostics=diagnostics
        )

    def durable_roots(self) -> list[tuple[str, Path]]:
        self.storage_mount.check()
        candidates = (
            ("data", self.data_dir),
            ("root_profile", self.root_profile),
            ("profiles", self.profiles_root),
        )
        roots: list[tuple[str, Path]] = []
        for label, path in candidates:
            if any(path == parent or path.is_relative_to(parent) for _, parent in roots):
                continue
            roots = [
                (old_label, old_path)
                for old_label, old_path in roots
                if not old_path.is_relative_to(path)
            ]
            roots.append((label, path))
        return sorted(roots)

    def layout(self, expected_data_path: str) -> dict[str, Any]:
        expected = Path(expected_data_path).resolve()
        if expected != self.data_anchor:
            raise RuntimeUpdateStorageError(
                "approved data path does not match the packaged Runtime data root",
                code="runtime_update_data_path_mismatch",
            )
        mounts = self._mounts()
        root_mount = self._covering_mount(Path("/"), mounts)
        data_mount = self._covering_mount(expected, mounts)
        separate = (
            data_mount is not None and root_mount is not None and data_mount[0] != root_mount[0]
        )
        return {
            "data_path": str(expected),
            "separate_mount": separate,
            "mount_point": str(data_mount[1]) if data_mount else "",
            "filesystem": data_mount[2] if data_mount else "unknown",
        }

    @staticmethod
    def _mounts() -> list[tuple[int, Path, str]]:
        source = Path(os.getenv("RUNTIME_UPDATE_MOUNTINFO_FILE", "/proc/self/mountinfo"))
        result: list[tuple[int, Path, str]] = []
        try:
            lines = source.read_text(encoding="utf-8").splitlines()
        except OSError:
            return result
        for line in lines:
            fields = line.split()
            try:
                separator = fields.index("-")
                result.append((int(fields[0]), Path(fields[4]), fields[separator + 1]))
            except (ValueError, IndexError):
                continue
        return result

    @staticmethod
    def _covering_mount(
        path: Path, mounts: Iterable[tuple[int, Path, str]]
    ) -> tuple[int, Path, str] | None:
        matches = [item for item in mounts if path == item[1] or path.is_relative_to(item[1])]
        return max(matches, key=lambda item: len(item[1].parts), default=None)

    def flush_sqlite(self) -> list[str]:
        flushed: list[str] = []
        for label, root in self.durable_roots():
            if not root.exists():
                continue
            # Finish validation before SQLite can remove WAL/SHM files when its
            # last connection closes. A lazy walk would inspect stale directory
            # entries after our own checkpoint and reject otherwise valid data.
            paths = list(self._walk_files(root))
            for path in paths:
                if path.is_symlink() or self._excluded(path):
                    continue
                if path.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
                    continue
                if not file_stat.S_ISREG(path.lstat().st_mode):
                    continue
                # SQLite writes through its sidecars; never let it follow a link.
                for suffix in ("-wal", "-shm"):
                    if Path(str(path) + suffix).is_symlink():
                        raise self._unsafe_data(
                            Path(str(path) + suffix),
                            "sqlite_sidecar_symlink",
                            "SQLite sidecars must not be symbolic links",
                        )
                try:
                    connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=5)
                    try:
                        result = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                        if result and result[0]:
                            raise RuntimeUpdateStorageError(
                                "a durable SQLite database is busy",
                                code="runtime_update_sqlite_checkpoint_failed",
                            )
                    finally:
                        connection.close()
                except sqlite3.DatabaseError as error:
                    raise RuntimeUpdateStorageError(
                        "a durable SQLite database could not be checkpointed",
                        code="runtime_update_sqlite_checkpoint_failed",
                    ) from error
                flushed.append(f"{label}/{path.relative_to(root).as_posix()}")
        return sorted(flushed)

    def manifest(self, *, version: int = MANIFEST_VERSION) -> dict[str, Any]:
        # A disappearing SQLite sidecar can race directory enumeration. Retry
        # the entire snapshot, never silently drop a missing durable file.
        for attempt in range(3):
            try:
                return self._manifest(version=version)
            except FileNotFoundError as error:
                if attempt == 2:
                    raise RuntimeUpdateStorageError(
                        "durable data changed during inspection",
                        code="runtime_update_manifest_unstable",
                    ) from error
        raise AssertionError("unreachable")

    def _excluded(self, path: Path) -> bool:
        return any(path.is_relative_to(self.data_dir / name) for name in EXCLUDED_DATA_DIRECTORIES)

    def _runtime_log(self, path: Path) -> bool:
        # Runtime-owned profile logs are diagnostics, not durable user data.
        if path.is_relative_to(self.root_profile / "logs"):
            return True
        if not path.is_relative_to(self.profiles_root):
            return False
        parts = path.relative_to(self.profiles_root).parts
        return len(parts) > 2 and parts[1] == "logs"

    def _manifest(self, *, version: int) -> dict[str, Any]:
        if version not in {1, 2, 3}:
            raise RuntimeUpdateStorageError(
                "unsupported checkpoint manifest", code="runtime_update_manifest_version"
            )
        entries: list[dict[str, Any]] = []
        total = 0
        for label, root in self.durable_roots():
            if not root.exists():
                continue
            paths = list(self._walk_files(root))
            databases = set()
            if version >= 2:
                for path in paths:
                    if (
                        not path.is_symlink()
                        and not self._excluded(path)
                        and file_stat.S_ISREG(path.lstat().st_mode)
                        and self._sqlite(path)
                    ):
                        databases.add(path)
            sidecars = {
                Path(str(database) + suffix)
                for database in databases
                for suffix in ("-wal", "-shm")
            }
            for path in sidecars:
                if path.is_symlink():
                    raise self._unsafe_data(
                        path,
                        "sqlite_sidecar_symlink",
                        "SQLite sidecars must not be symbolic links",
                    )
            for path in paths:
                if self._excluded(path) or (version == 3 and self._runtime_log(path)):
                    continue
                if is_coordination_file(path, self.data_dir):
                    try:
                        validate_coordination_file(path)
                    except (StoreError, OSError) as error:
                        raise self._unsafe_data(
                            path, "coordination_file", "unsafe coordination file"
                        ) from error
                    continue
                # Only recognized SQLite databases account for their WAL/SHM.
                # Orphan sidecars and symlinks remain independently verified.
                if version >= 2 and not path.is_symlink() and path in sidecars:
                    continue
                if version == 1 and path.is_symlink() and self._profile_alias(path):
                    continue
                stat = path.stat(follow_symlinks=False)
                total += stat.st_size
                if len(entries) >= self.max_files or total > self.max_bytes:
                    raise RuntimeUpdateStorageError(
                        "durable data exceeds the bounded update manifest limit",
                        code="runtime_update_manifest_limit",
                    )
                entry = {
                    "path": f"{label}/{path.relative_to(root).as_posix()}",
                    "size": stat.st_size,
                    "mode": stat.st_mode & 0o777,
                    "uid": stat.st_uid,
                    "gid": stat.st_gid,
                    "sha256": "",
                }
                if path.is_symlink():
                    # Record the link text only; its target is never opened.
                    entry["kind"] = "symlink"
                    entry["sha256"] = hashlib.sha256(os.fsencode(os.readlink(path))).hexdigest()
                elif not file_stat.S_ISREG(stat.st_mode):
                    if version < 3:
                        raise self._unsafe_data(
                            path,
                            "file_type",
                            "durable data contains an unsafe or unsupported entry",
                        )
                    # FIFOs and sockets are recorded by type, never opened.
                    entry["kind"] = "special"
                    entry["file_type"] = file_stat.S_IFMT(stat.st_mode)
                    del entry["size"], entry["sha256"]
                elif version == 3 and path in databases:
                    # Page layout churns across restarts; rows are the evidence.
                    del entry["size"], entry["sha256"]
                elif version == 3 and stat.st_size > CONTENT_DIGEST_MAX_BYTES:
                    del entry["sha256"]
                    entry["mtime_ns"] = stat.st_mtime_ns
                else:
                    entry["sha256"] = self._digest(path)
                if path in databases:
                    entry["kind"] = "sqlite"
                    try:
                        entry["sqlite"] = runtime_update_sqlite.snapshot(
                            path,
                            max_bytes=self.max_bytes,
                            hermes_state=(
                                path == self.root_profile / "state.db"
                                or (
                                    path.name == "state.db"
                                    and path.parent.parent == self.profiles_root
                                )
                            ),
                        )
                    except (sqlite3.DatabaseError, ValueError) as error:
                        raise RuntimeUpdateStorageError(
                            "durable SQLite data could not be verified",
                            code="runtime_update_sqlite_verify_failed",
                        ) from error
                entries.append(entry)
        entries.sort(key=lambda item: item["path"])
        digest = hashlib.sha256()
        for item in entries:
            if version >= 2:
                digest.update(json.dumps(item, sort_keys=True, separators=(",", ":")).encode())
                continue
            digest.update(
                (
                    f"{item['path']}\0{item['size']}\0{item['mode']}\0"
                    f"{item['uid']}\0{item['gid']}\0{item['sha256']}\n"
                ).encode()
            )
        result = {
            "algorithm": "sha256",
            "digest": digest.hexdigest(),
            "file_count": len(entries),
            "total_bytes": total,
            "entries": entries,
        }
        if version >= 2:
            result["version"] = version
        return result

    def verify(
        self, baseline: dict[str, Any], accepted_missing: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        """Verify the baseline under explicit acceptance; retain actual loss counts.

        ``preserved`` means the verification policy passes, including an operator
        exception when present. ``accepted_removed`` records that exception.
        """
        version = baseline.get("version", 1)
        accepted = self.validate_transfer_acceptance(baseline, accepted_missing)
        current = self.manifest(version=version)
        if version == 1:
            return {
                "preserved": current["digest"] == baseline["digest"],
                "manifest_digest": current["digest"],
                "manifest_version": 1,
            }
        before = {entry["path"]: entry for entry in baseline["entries"]}
        after = {entry["path"]: entry for entry in current["entries"]}
        counts = {
            "added": len(after.keys() - before.keys()),
            "removed": len(before.keys() - after.keys()),
            "changed": 0,
            "log_appends": 0,
            "sqlite_equivalent": 0,
        }
        accepted_removed = len((before.keys() - after.keys()) & accepted)
        for name in before.keys() & after.keys():
            old, new = before[name], after[name]
            if old == new:
                continue
            metadata = ("mode", "uid", "gid", "kind")
            if all(old.get(key) == new.get(key) for key in metadata):
                if old.get("kind") == "sqlite" and runtime_update_sqlite.preserved(
                    old["sqlite"], new["sqlite"]
                ):
                    counts["sqlite_equivalent"] += 1
                    continue
                path = self._log_path(name) if version == 2 else None
                if (
                    old.get("kind") is None
                    and new.get("kind") is None
                    and path
                    and new["size"] >= old["size"]
                    and self._digest(path, old["size"]) == old["sha256"]
                ):
                    counts["log_appends"] += 1
                    continue
            counts["changed"] += 1
        return {
            "preserved": (
                counts["added"] == counts["changed"] == 0 and counts["removed"] == accepted_removed
            ),
            "manifest_digest": current["digest"],
            "manifest_version": version,
            "changes": counts,
            **({"accepted_removed": accepted_removed} if accepted else {}),
        }

    @staticmethod
    def validate_transfer_acceptance(baseline, paths) -> set[str]:
        """Only complete, exact pairs of temporary upload files can be accepted."""
        accepted = set(paths)
        if not paths:
            return accepted
        entries = {entry["path"]: entry for entry in baseline["entries"]}
        valid = baseline.get("version", 1) in {2, 3} and len(accepted) == len(paths)
        for name in accepted:
            match = re.fullmatch(
                r"data/transfers/uploads/([0-9a-f]{32})/(bundle\.zip|metadata\.json)", name
            )
            entry = entries.get(name, {})
            valid = valid and bool(match) and bool(entry) and entry.get("kind") is None
            if match:
                prefix = f"data/transfers/uploads/{match[1]}/"
                valid = valid and all(
                    prefix + filename in accepted for filename in ("bundle.zip", "metadata.json")
                )
        if not valid:
            raise RuntimeUpdateStorageError(
                "Invalid expired transfer acceptance", code="runtime_update_acceptance_invalid"
            )
        return accepted

    def _log_path(self, name: str) -> Path | None:
        # Only Runtime's existing profile logs; user workspace *.log files are
        # ordinary durable data. No added/deleted/rotated log is accepted.
        for label, root in self.durable_roots():
            prefix = label + "/"
            if not name.startswith(prefix):
                continue
            path = root / name[len(prefix) :]
            if path.name != "agent.log":
                return None
            if path.parent == self.root_profile / "logs" or (
                path.parent.name == "logs" and path.parent.parent.parent == self.profiles_root
            ):
                return path
        return None

    @staticmethod
    def _sqlite(path: Path) -> bool:
        if path.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
            return False
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            return source.read(16) == b"SQLite format 3\x00"

    def _digest(self, path: Path, limit: int | None = None) -> str:
        digest = hashlib.sha256()
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            if not file_stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise self._unsafe_data(path, "regular_file", "unsafe file")
            remaining = limit
            while remaining is None or remaining > 0:
                chunk = source.read(
                    min(1024 * 1024, remaining) if remaining is not None else 1024 * 1024
                )
                if not chunk:
                    break
                digest.update(chunk)
                if remaining is not None:
                    remaining -= len(chunk)
        return digest.hexdigest()

    def _profile_alias(self, path: Path) -> bool:
        return path == self.root_profile / "profiles" and path.resolve() == self.profiles_root

    def _walk_files(self, root: Path) -> Iterable[Path]:
        def fail_walk(error):
            raise error

        for current, directories, files in os.walk(root, followlinks=False, onerror=fail_walk):
            current_path = Path(current)
            safe_directories = []
            for name in sorted(directories):
                path = current_path / name
                if self._excluded(path):
                    continue
                # Symlinked directories are recorded as links, never descended.
                if path.is_symlink():
                    yield path
                    continue
                safe_directories.append(name)
            directories[:] = safe_directories
            for name in sorted(files):
                yield current_path / name
