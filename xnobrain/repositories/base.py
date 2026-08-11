"""Shared filesystem repository primitives."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any, Mapping

import yaml


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
TEAM_RUN_RETENTION = 100


class StoreError(ValueError):
    """An expected filesystem contract error."""

    def __init__(self, message: str, *, status: int = 400, code: str = "invalid_store_request"):
        super().__init__(message)
        self.status = status
        self.code = code


class RepositoryBase:
    """Lock-protected atomic filesystem primitives shared by repository groups."""

    def __init__(self, data_dir: str | Path, profiles_root: str | Path):
        self.data_dir = Path(data_dir).resolve()
        self.profiles_root = Path(profiles_root).resolve()
        self.teams_root = self.data_dir / "teams"
        self.team_runs_root = self.teams_root / "runs"
        self.notifications_root = self.data_dir / "notifications"
        self.trash_root = self.data_dir / "trash" / "profiles"
        self._lock = threading.RLock()
        for path in (self.data_dir, self.profiles_root, self.teams_root, self.team_runs_root, self.notifications_root, self.trash_root):
            path.mkdir(parents=True, exist_ok=True, mode=0o750)

    @staticmethod
    def _id(value: Any, field: str = "id") -> str:
        result = str(value or "").strip()
        if not _SAFE_ID.fullmatch(result):
            raise StoreError(f"invalid {field}")
        return result

    def atomic_json(self, path: Path, value: Any) -> None:
        self.atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())

    def atomic_yaml(self, path: Path, value: Any) -> None:
        self.atomic_write(path, yaml.safe_dump(value, sort_keys=False, allow_unicode=True).encode())

    def atomic_write(self, path: Path, payload: bytes, *, mode: int = 0o640, replace: bool = True) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        with self._lock:
            if not replace and path.exists():
                raise StoreError("immutable snapshot already exists", status=409, code="snapshot_exists")
            fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
            try:
                os.fchmod(fd, mode)
                with os.fdopen(fd, "wb") as file:
                    file.write(payload)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temporary, path)
                self._sync_dir(path.parent)
            except Exception:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
                raise

    def _profile_dirs(self) -> list[Path]:
        return [path for path in self.profiles_root.iterdir() if path.is_dir() and not path.is_symlink()]

    @staticmethod
    def _read_yaml(path: Path) -> Any:
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    @staticmethod
    def _sync_dir(path: Path) -> None:
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            pass

