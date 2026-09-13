"""Atomic profile-local persistence for skill optimization operations."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Mapping

from .base import StoreError


class SkillOptimizationRepositoryMixin:
    """Persist records, private examples, candidates, and rollback checkpoints."""

    def skill_optimization_root(self, agent_id: Any) -> Path:
        profile = self.profile_path(agent_id)
        if profile.is_symlink() or not profile.is_dir():
            raise StoreError("agent profile not found", status=404, code="not_found")
        root = profile / "skill-optimization"
        if root.is_symlink():
            raise StoreError("optimization storage must not be a symlink")
        resolved = root.resolve(strict=False)
        if profile not in resolved.parents:
            raise StoreError("optimization storage escapes profile")
        resolved.mkdir(parents=True, exist_ok=True, mode=0o700)
        return resolved

    def skill_optimization_path(self, agent_id: Any, operation_id: Any) -> Path:
        operation = self._id(operation_id, "optimization id")
        if not operation.startswith("sop_"):
            raise StoreError("invalid optimization id")
        root = self.skill_optimization_root(agent_id)
        path = root / operation
        if path.is_symlink() or path.resolve(strict=False).parent != root:
            raise StoreError("invalid optimization path")
        return path

    def create_skill_optimization(
        self,
        agent_id: Any,
        record: Mapping[str, Any],
        *,
        candidates: Mapping[str, bytes],
        examples: Mapping[str, Any],
    ) -> dict[str, Any]:
        directory = self.skill_optimization_path(agent_id, record.get("id"))
        staging = directory.parent / f".{directory.name}.staging"
        with self._lock:
            if directory.exists() or staging.exists():
                raise StoreError(
                    "skill optimization already exists",
                    status=409,
                    code="skill_optimization_exists",
                )
            staging.mkdir(mode=0o700)
            try:
                self.atomic_json(staging / "record.json", dict(record))
                self.atomic_json(staging / "examples.json", dict(examples))
                os.chmod(staging / "examples.json", 0o600)
                for skill_id, content in candidates.items():
                    self.atomic_write(
                        staging / "candidates" / skill_id / "SKILL.md",
                        content,
                        mode=0o600,
                    )
                os.replace(staging, directory)
                self._sync_dir(directory.parent)
            except Exception:
                if staging.exists():
                    shutil.rmtree(staging)
                raise
        return dict(record)

    def get_skill_optimization(self, agent_id: Any, operation_id: Any) -> dict[str, Any]:
        path = self.skill_optimization_path(agent_id, operation_id) / "record.json"
        if not path.is_file() or path.is_symlink():
            raise StoreError(
                "skill optimization not found",
                status=404,
                code="skill_optimization_not_found",
            )
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise StoreError(
                "skill optimization record is unreadable",
                status=500,
                code="invalid_skill_optimization_store",
            ) from error
        if not isinstance(value, dict):
            raise StoreError("skill optimization record is invalid", status=500)
        return value

    def update_skill_optimization(
        self,
        agent_id: Any,
        operation_id: Any,
        expected_revision: int,
        record: Mapping[str, Any],
        *,
        expected_record: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        path = self.skill_optimization_path(agent_id, operation_id) / "record.json"
        with self._lock:
            current = self.get_skill_optimization(agent_id, operation_id)
            if int(current.get("revision") or 0) != expected_revision:
                raise StoreError(
                    "skill optimization revision conflict",
                    status=409,
                    code="skill_optimization_revision_conflict",
                )
            if expected_record is not None and current != dict(expected_record):
                raise StoreError(
                    "skill optimization state conflict",
                    status=409,
                    code="skill_optimization_state_conflict",
                )
            next_record = dict(record)
            if int(next_record.get("revision") or 0) not in {
                expected_revision,
                expected_revision + 1,
            }:
                raise StoreError("invalid skill optimization revision")
            self.atomic_json(path, next_record)
        return next_record

    def read_skill_optimization_candidate(
        self,
        agent_id: Any,
        operation_id: Any,
        skill_id: Any,
    ) -> bytes:
        skill = self._id(skill_id, "skill id")
        directory = self.skill_optimization_path(agent_id, operation_id)
        path = directory / "candidates" / skill / "SKILL.md"
        if path.is_symlink() or not path.is_file():
            raise StoreError("skill candidate not found", status=404)
        if directory not in path.resolve().parents:
            raise StoreError("skill candidate escapes operation")
        return path.read_bytes()

    def write_skill_optimization_checkpoint(
        self,
        agent_id: Any,
        operation_id: Any,
        skill_id: Any,
        content: bytes | None,
        relative_path: str | None,
        enabled: bool | None,
    ) -> None:
        skill = self._id(skill_id, "skill id")
        root = self.skill_optimization_path(agent_id, operation_id) / "checkpoint" / skill
        metadata = {
            "existed": content is not None,
            "relative_path": relative_path,
            "enabled": enabled,
        }
        with self._lock:
            metadata_path = root / "metadata.json"
            content_path = root / "SKILL.md"
            if metadata_path.is_file():
                existing = self.read_skill_optimization_checkpoint(agent_id, operation_id, skill_id)
                if (
                    existing.get("existed") != metadata["existed"]
                    or existing.get("relative_path") != relative_path
                    or existing.get("enabled") != enabled
                    or existing.get("content") != content
                ):
                    raise StoreError(
                        "optimization checkpoint conflict",
                        status=409,
                        code="skill_optimization_checkpoint_conflict",
                    )
                return
            if content is not None:
                if content_path.is_file():
                    if content_path.is_symlink() or content_path.read_bytes() != content:
                        raise StoreError(
                            "optimization checkpoint conflict",
                            status=409,
                            code="skill_optimization_checkpoint_conflict",
                        )
                else:
                    self.atomic_write(content_path, content, mode=0o440, replace=False)
            self.atomic_json(metadata_path, metadata)

    def read_skill_optimization_checkpoint(
        self,
        agent_id: Any,
        operation_id: Any,
        skill_id: Any,
    ) -> dict[str, Any]:
        skill = self._id(skill_id, "skill id")
        root = self.skill_optimization_path(agent_id, operation_id) / "checkpoint" / skill
        try:
            metadata = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise StoreError(
                "optimization checkpoint is unavailable",
                status=409,
                code="skill_optimization_checkpoint_missing",
            ) from error
        content_path = root / "SKILL.md"
        return {
            **metadata,
            "content": content_path.read_bytes() if content_path.is_file() else None,
        }
