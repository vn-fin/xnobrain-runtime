"""Atomic update-operation journals and maintenance fencing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .base import StoreError


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
        if not path.is_file() or path.is_symlink():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise StoreError(
                "Runtime update journal is invalid",
                status=500,
                code="runtime_update_journal_invalid",
            ) from error
        if not isinstance(value, dict):
            raise StoreError(
                "Runtime update journal is invalid",
                status=500,
                code="runtime_update_journal_invalid",
            )
        return value

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

    def save_maintenance(self, value: Mapping[str, Any]) -> dict[str, Any]:
        item = dict(value)
        self.base.atomic_write(
            self.maintenance_path,
            (json.dumps(item, ensure_ascii=False, indent=2) + "\n").encode(),
            mode=0o600,
        )
        return item

    def clear_maintenance(self) -> None:
        with self.base._lock:
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
