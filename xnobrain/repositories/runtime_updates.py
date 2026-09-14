"""Atomic update-operation journals and maintenance fencing."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .base import StoreError
from .runtime_update_gate import admission_gate


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate journal field")
        result[key] = value
    return result


class RuntimeUpdateRepository:
    """Persist safe operation evidence on the durable Runtime data root."""

    def __init__(self, base):
        self.base = base
        self.root = base.data_dir / "runtime-updates"
        self.operations = self.root / "operations"
        self.checkpoints = self.root / "checkpoints"
        self.maintenance_path = self.root / "maintenance.json"
        self.operations.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.checkpoints.mkdir(parents=True, exist_ok=True, mode=0o700)

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        # An absent journal is the only empty state. Symlinked, unreadable or
        # malformed maintenance must not become an implicit admission grant.
        descriptor = None
        try:
            if path.parent.is_symlink() or path.parent.parent.is_symlink():
                raise ValueError("unsafe journal parent")
            descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_size > (16384 if path.name == "maintenance.json" else 64 * 1024 * 1024)
            ):
                raise ValueError("invalid journal file")
            with os.fdopen(descriptor, encoding="utf-8") as source:
                descriptor = None
                value = json.load(source, object_pairs_hook=_unique_fields)
            if not isinstance(value, dict):
                raise ValueError("invalid journal shape")
            if path.name == "maintenance.json":
                if not isinstance(value.get("dispatch_paused"), bool):
                    raise ValueError("missing maintenance state")
                if value["dispatch_paused"] and (
                    not value.get("operation_id")
                    or not isinstance(value.get("generation"), int)
                    or not isinstance(value.get("target"), dict)
                ):
                    raise ValueError("invalid maintenance binding")
            return value
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as error:
            raise StoreError(
                "Runtime update journal is invalid",
                status=503,
                code="runtime_update_journal_invalid",
            ) from error
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def operation(self, operation_id: str) -> dict[str, Any]:
        operation = self.base._id(operation_id, "operation id")
        return self._read(self.operations / f"{operation}.json")

    def save_operation(self, value: Mapping[str, Any]) -> dict[str, Any]:
        item = dict(value)
        operation = self.base._id(item.get("operation_id"), "operation id")
        self.base.atomic_json(self.operations / f"{operation}.json", item)
        return item

    def maintenance(self) -> dict[str, Any]:
        return self._read(self.maintenance_path)

    @contextmanager
    def _maintenance_write(self):
        if not self.base._lock.acquire(timeout=2):
            raise StoreError(
                "Runtime update storage is busy", status=503, code="runtime_update_storage_busy"
            )
        try:
            with admission_gate(self.base.data_dir):
                yield
        finally:
            self.base._lock.release()

    def save_maintenance(self, value: Mapping[str, Any]) -> dict[str, Any]:
        item = dict(value)
        with self._maintenance_write():
            self.base.atomic_write(
                self.maintenance_path,
                (json.dumps(item, ensure_ascii=False, indent=2) + "\n").encode(),
                mode=0o600,
            )
        return item

    def clear_maintenance(self) -> None:
        with self._maintenance_write():
            try:
                self.maintenance_path.unlink()
            except FileNotFoundError:
                return
            self.base._sync_dir(self.maintenance_path.parent)

    def checkpoint(self, checkpoint_id: str) -> dict[str, Any]:
        checkpoint = self.base._id(checkpoint_id, "checkpoint id")
        return self._read(self.checkpoints / f"{checkpoint}.json")

    def save_checkpoint(self, value: Mapping[str, Any]) -> dict[str, Any]:
        item = dict(value)
        checkpoint = self.base._id(item.get("checkpoint_id"), "checkpoint id")
        path = self.checkpoints / f"{checkpoint}.json"
        self.base.atomic_write(
            path,
            (json.dumps(item, ensure_ascii=False, indent=2) + "\n").encode(),
            mode=0o600,
            replace=False,
        )
        return item
