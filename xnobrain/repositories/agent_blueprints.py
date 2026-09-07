"""Atomic, creator-profile-local Agent Maker blueprint persistence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .base import StoreError


class AgentBlueprintRepositoryMixin:
    """Persist blueprint records below the owning profile without symlink escapes."""

    def _agent_blueprints_root(self, profile_root: Path) -> Path:
        profile_root = Path(profile_root)
        if profile_root.is_symlink() or not profile_root.is_dir():
            raise StoreError("creator profile not found", status=404, code="not_found")
        resolved_profile = profile_root.resolve()
        metadata_root = profile_root / ".xnobrain"
        blueprints_root = metadata_root / "agent-blueprints"
        for path in (metadata_root, blueprints_root):
            if path.is_symlink():
                raise StoreError(
                    "blueprint storage must not be a symlink",
                    code="unsafe_blueprint_store",
                )
        blueprints_root.mkdir(parents=True, exist_ok=True, mode=0o750)
        resolved_blueprints = blueprints_root.resolve()
        if resolved_profile not in resolved_blueprints.parents:
            raise StoreError(
                "blueprint storage escapes creator profile",
                code="unsafe_blueprint_store",
            )
        return resolved_blueprints

    def _agent_blueprint_path(self, profile_root: Path, blueprint_id: Any) -> Path:
        blueprint_id = self._id(blueprint_id, "blueprint id")
        root = self._agent_blueprints_root(profile_root)
        path = root / f"{blueprint_id}.json"
        if path.is_symlink() or path.resolve().parent != root:
            raise StoreError("invalid blueprint path", code="unsafe_blueprint_store")
        return path

    @staticmethod
    def _decode_agent_blueprint(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise StoreError("blueprint not found", status=404, code="not_found") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise StoreError(
                "blueprint record is unreadable",
                status=500,
                code="invalid_blueprint_store",
            ) from exc
        if not isinstance(value, dict):
            raise StoreError(
                "blueprint record is invalid",
                status=500,
                code="invalid_blueprint_store",
            )
        return value

    def create_agent_blueprint(
        self,
        profile_root: Path,
        record: Mapping[str, Any],
    ) -> dict[str, Any]:
        path = self._agent_blueprint_path(profile_root, record.get("id"))
        with self._lock:
            if path.exists():
                raise StoreError(
                    "blueprint already exists",
                    status=409,
                    code="blueprint_exists",
                )
            payload = (json.dumps(dict(record), ensure_ascii=False, indent=2) + "\n").encode()
            self.atomic_write(path, payload, mode=0o640, replace=False)
        return dict(record)

    def get_agent_blueprint(
        self,
        profile_root: Path,
        blueprint_id: Any,
    ) -> dict[str, Any]:
        path = self._agent_blueprint_path(profile_root, blueprint_id)
        with self._lock:
            return self._decode_agent_blueprint(path)

    def list_agent_blueprints(
        self,
        profile_root: Path,
    ) -> list[dict[str, Any]]:
        root = self._agent_blueprints_root(profile_root)
        with self._lock:
            records = [
                self._decode_agent_blueprint(path)
                for path in root.glob("abp_*.json")
                if path.is_file() and not path.is_symlink()
            ]
        return sorted(
            records,
            key=lambda item: (
                str(item.get("updated_at") or ""),
                str(item.get("id") or ""),
            ),
            reverse=True,
        )

    def update_agent_blueprint(
        self,
        profile_root: Path,
        blueprint_id: Any,
        expected_revision: int,
        record: Mapping[str, Any],
        *,
        expected_record: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        path = self._agent_blueprint_path(profile_root, blueprint_id)
        with self._lock:
            current = self._decode_agent_blueprint(path)
            if int(current.get("revision") or 0) != expected_revision:
                raise StoreError(
                    "blueprint revision conflict",
                    status=409,
                    code="blueprint_revision_conflict",
                )
            if expected_record is not None and current != dict(expected_record):
                raise StoreError(
                    "blueprint state conflict",
                    status=409,
                    code="blueprint_state_conflict",
                )
            next_record = dict(record)
            next_revision = int(next_record.get("revision") or 0)
            if next_revision not in {expected_revision, expected_revision + 1}:
                raise StoreError("invalid next blueprint revision")
            self.atomic_json(path, next_record)
        return next_record
