"""Atomic durable settings, fences, and operation journals for FT0013."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .base import StoreError


class TimeControlRepository:
    """Persist adapter-owned state beneath the Runtime data directory."""

    def __init__(self, base):
        self.base = base
        self.root = base.data_dir / "time-control"
        self.operations = self.root / "operations"
        self.settings_path = self.root / "timezone.json"
        self.fence_path = self.root / "fence.json"
        self.operations.mkdir(parents=True, exist_ok=True, mode=0o700)

    @staticmethod
    def _read(path: Path, label: str) -> dict[str, Any]:
        if not path.exists():
            return {}
        if not path.is_file() or path.is_symlink():
            raise StoreError(
                f"{label} is invalid",
                status=500,
                code="time_control_state_invalid",
            )
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise StoreError(
                f"{label} is invalid",
                status=500,
                code="time_control_state_invalid",
            ) from error
        if not isinstance(value, dict):
            raise StoreError(
                f"{label} is invalid",
                status=500,
                code="time_control_state_invalid",
            )
        return value

    def settings(self) -> dict[str, Any]:
        value = self._read(self.settings_path, "Time Control configuration")
        if not value:
            return {
                "schema_version": 1,
                "timezone": "Etc/UTC",
                "revision": 0,
                "operation_id": "bootstrap",
            }
        return value

    def save_settings(self, value: Mapping[str, Any]) -> dict[str, Any]:
        item = dict(value)
        self.base.atomic_write(
            self.settings_path,
            (json.dumps(item, ensure_ascii=False, indent=2) + "\n").encode(),
            mode=0o600,
        )
        return item

    def fence(self) -> dict[str, Any]:
        return self._read(self.fence_path, "Time Control fence")

    def save_fence(self, value: Mapping[str, Any]) -> dict[str, Any]:
        item = dict(value)
        self.base.atomic_write(
            self.fence_path,
            (json.dumps(item, ensure_ascii=False, indent=2) + "\n").encode(),
            mode=0o600,
        )
        return item

    def operation(self, operation_id: str) -> dict[str, Any]:
        identifier = self.base._id(operation_id, "Time Control operation id")
        return self._read(
            self.operations / f"{identifier}.json",
            "Time Control operation journal",
        )

    def save_operation(self, value: Mapping[str, Any]) -> dict[str, Any]:
        item = dict(value)
        identifier = self.base._id(
            item.get("operation_id"),
            "Time Control operation id",
        )
        self.base.atomic_write(
            self.operations / f"{identifier}.json",
            (json.dumps(item, ensure_ascii=False, indent=2) + "\n").encode(),
            mode=0o600,
        )
        return item

    def incomplete_operations(self) -> list[dict[str, Any]]:
        result = []
        for path in sorted(self.operations.glob("*.json")):
            value = self._read(path, "Time Control operation journal")
            if value and value.get("state") not in {"succeeded", "partial", "failed"}:
                result.append(value)
        return result
