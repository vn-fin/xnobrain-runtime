"""Safe local storage inspection for Runtime update pre/postconditions."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable


class RuntimeUpdateStorageError(RuntimeError):
    def __init__(self, message: str, *, code: str):
        super().__init__(message)
        self.code = code


class RuntimeUpdateStorage:
    """Inspect durable roots directly; never invoke a host shell."""

    def __init__(self, data_dir: Path, profiles_root: Path, root_profile: Path):
        self.data_dir = data_dir.resolve()
        self.profiles_root = profiles_root.resolve()
        self.root_profile = root_profile.resolve()
        self.data_anchor = Path(os.getenv("RUNTIME_UPDATE_DATA_PATH", "/opt/data")).resolve()
        self.max_files = max(1, int(os.getenv("RUNTIME_UPDATE_MAX_MANIFEST_FILES", "200000")))
        self.max_bytes = max(1, int(os.getenv("RUNTIME_UPDATE_MAX_MANIFEST_BYTES", str(1 << 40))))

    def durable_roots(self) -> list[tuple[str, Path]]:
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
            for path in self._walk_files(root):
                if path.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
                    continue
                try:
                    connection = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=5)
                    try:
                        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
                    finally:
                        connection.close()
                except sqlite3.DatabaseError as error:
                    raise RuntimeUpdateStorageError(
                        "a durable SQLite database could not be checkpointed",
                        code="runtime_update_sqlite_checkpoint_failed",
                    ) from error
                flushed.append(f"{label}/{path.relative_to(root).as_posix()}")
        return sorted(flushed)

    def manifest(self) -> dict[str, Any]:
        entries: list[dict[str, Any]] = []
        total = 0
        for label, root in self.durable_roots():
            if not root.exists():
                continue
            for path in self._walk_files(root):
                if self.data_dir == root and path.is_relative_to(self.data_dir / "runtime-updates"):
                    continue
                stat = path.stat(follow_symlinks=False)
                total += stat.st_size
                if len(entries) >= self.max_files or total > self.max_bytes:
                    raise RuntimeUpdateStorageError(
                        "durable data exceeds the bounded update manifest limit",
                        code="runtime_update_manifest_limit",
                    )
                entries.append(
                    {
                        "path": f"{label}/{path.relative_to(root).as_posix()}",
                        "size": stat.st_size,
                        "mode": stat.st_mode & 0o777,
                        "uid": stat.st_uid,
                        "gid": stat.st_gid,
                        "sha256": self._digest(path),
                    }
                )
        entries.sort(key=lambda item: item["path"])
        digest = hashlib.sha256()
        for item in entries:
            digest.update(
                (
                    f"{item['path']}\0{item['size']}\0{item['mode']}\0"
                    f"{item['uid']}\0{item['gid']}\0{item['sha256']}\n"
                ).encode()
            )
        return {
            "algorithm": "sha256",
            "digest": digest.hexdigest(),
            "file_count": len(entries),
            "total_bytes": total,
            "entries": entries,
        }

    @staticmethod
    def _digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _walk_files(root: Path) -> Iterable[Path]:
        for current, directories, files in os.walk(root, followlinks=False):
            current_path = Path(current)
            safe_directories = []
            for name in sorted(directories):
                path = current_path / name
                if path.is_symlink():
                    raise RuntimeUpdateStorageError(
                        "durable data contains an unsafe symbolic link",
                        code="runtime_update_unsafe_data",
                    )
                if not path.is_dir():
                    raise RuntimeUpdateStorageError(
                        "durable data contains an unsupported entry",
                        code="runtime_update_unsafe_data",
                    )
                safe_directories.append(name)
            directories[:] = safe_directories
            for name in sorted(files):
                path = current_path / name
                if path.is_symlink() or not path.is_file():
                    raise RuntimeUpdateStorageError(
                        "durable data contains an unsafe or unsupported entry",
                        code="runtime_update_unsafe_data",
                    )
                yield path
