"""Atomic profile-local persistence for Skill Doctor workflows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .base import StoreError


class SkillDoctorRepositoryMixin:
    """Persist reports, plans, launch receipts, and immutable checkpoints."""

    def _skill_doctor_root(self, agent_id: Any) -> Path:
        profile = self.profile_path(agent_id)
        if profile.is_symlink() or not profile.is_dir():
            raise StoreError("agent profile not found", status=404, code="not_found")
        root = profile / ".xnobrain" / "skill-doctor"
        for candidate in (root.parent, root):
            if candidate.is_symlink():
                raise StoreError("skill doctor storage must not be a symlink")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        resolved = root.resolve()
        if profile.resolve() not in resolved.parents:
            raise StoreError("skill doctor storage escapes profile")
        return resolved

    def _skill_doctor_record_path(self, agent_id: Any, kind: str, record_id: Any) -> Path:
        identifier = self._id(record_id, f"skill doctor {kind} id")
        prefixes = {"reports": "sdr_", "plans": "sdp_"}
        if kind not in prefixes or not identifier.startswith(prefixes[kind]):
            raise StoreError(f"invalid skill doctor {kind} id")
        root = self._skill_doctor_root(agent_id) / kind
        path = root / f"{identifier}.json"
        if path.is_symlink() or path.resolve(strict=False).parent != root.resolve(strict=False):
            raise StoreError(f"invalid skill doctor {kind} path")
        return path

    @staticmethod
    def _read_skill_doctor_record(path: Path, label: str) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise StoreError(
                f"skill doctor {label} not found",
                status=404,
                code=f"skill_doctor_{label}_not_found",
            ) from error
        except (OSError, json.JSONDecodeError) as error:
            raise StoreError(
                f"skill doctor {label} is unreadable",
                status=500,
                code="invalid_skill_doctor_store",
            ) from error
        if not isinstance(value, dict):
            raise StoreError("skill doctor record is invalid", status=500)
        return value

    def create_skill_doctor_record(
        self,
        agent_id: Any,
        kind: str,
        record: Mapping[str, Any],
    ) -> dict[str, Any]:
        path = self._skill_doctor_record_path(agent_id, kind, record.get("id"))
        with self._lock:
            if path.exists():
                raise StoreError(
                    "skill doctor record already exists",
                    status=409,
                    code="skill_doctor_record_exists",
                )
            self.atomic_json(path, dict(record))
        return dict(record)

    def get_skill_doctor_record(
        self,
        agent_id: Any,
        kind: str,
        record_id: Any,
    ) -> dict[str, Any]:
        path = self._skill_doctor_record_path(agent_id, kind, record_id)
        return self._read_skill_doctor_record(path, kind[:-1])

    def update_skill_doctor_record(
        self,
        agent_id: Any,
        kind: str,
        record_id: Any,
        expected_revision: int,
        record: Mapping[str, Any],
        *,
        expected_record: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        path = self._skill_doctor_record_path(agent_id, kind, record_id)
        with self._lock:
            current = self._read_skill_doctor_record(path, kind[:-1])
            if int(current.get("revision") or 0) != expected_revision:
                raise StoreError(
                    "skill doctor revision conflict",
                    status=409,
                    code="skill_doctor_revision_conflict",
                )
            if expected_record is not None and current != dict(expected_record):
                raise StoreError(
                    "skill doctor state conflict",
                    status=409,
                    code="skill_doctor_state_conflict",
                )
            next_record = dict(record)
            if int(next_record.get("revision") or 0) not in {
                expected_revision,
                expected_revision + 1,
            }:
                raise StoreError("invalid skill doctor revision")
            self.atomic_json(path, next_record)
        return next_record

    def find_skill_doctor_report_by_idempotency(
        self,
        agent_id: Any,
        key_hash: str,
    ) -> dict[str, Any] | None:
        root = self._skill_doctor_root(agent_id) / "reports"
        if not root.is_dir():
            return None
        for path in sorted(root.glob("sdr_*.json")):
            record = self._read_skill_doctor_record(path, "report")
            if record.get("idempotency_key_hash") == key_hash:
                return record
        return None

    def find_skill_doctor_launch_by_session(
        self,
        agent_id: Any,
        session_id: str,
    ) -> tuple[str, dict[str, Any]] | None:
        root = self._skill_doctor_root(agent_id) / "launches"
        if not root.is_dir():
            return None
        for path in sorted(root.glob("*.json")):
            record = self._read_skill_doctor_record(path, "launch")
            if record.get("session_id") == session_id:
                return path.stem, record
        return None

    def update_skill_doctor_launch(
        self,
        agent_id: Any,
        key_hash: str,
        record: Mapping[str, Any],
        *,
        expected_record: Mapping[str, Any],
    ) -> dict[str, Any]:
        path = self._skill_doctor_launch_path(agent_id, key_hash)
        with self._lock:
            current = self._read_skill_doctor_record(path, "launch")
            if current != dict(expected_record):
                raise StoreError(
                    "skill doctor launch state conflict",
                    status=409,
                    code="skill_doctor_launch_conflict",
                )
            self.atomic_json(path, dict(record))
        return dict(record)

    def _skill_doctor_launch_path(self, agent_id: Any, key_hash: str) -> Path:
        if len(key_hash) != 64 or any(char not in "0123456789abcdef" for char in key_hash):
            raise StoreError("invalid skill doctor launch key")
        root = self._skill_doctor_root(agent_id) / "launches"
        path = root / f"{key_hash}.json"
        if path.is_symlink() or path.resolve(strict=False).parent != root.resolve(strict=False):
            raise StoreError("invalid skill doctor launch path")
        return path

    def get_skill_doctor_launch(self, agent_id: Any, key_hash: str) -> dict[str, Any] | None:
        path = self._skill_doctor_launch_path(agent_id, key_hash)
        if not path.is_file():
            return None
        return self._read_skill_doctor_record(path, "launch")

    def create_skill_doctor_launch(
        self,
        agent_id: Any,
        key_hash: str,
        record: Mapping[str, Any],
    ) -> dict[str, Any]:
        path = self._skill_doctor_launch_path(agent_id, key_hash)
        with self._lock:
            if path.exists():
                existing = self._read_skill_doctor_record(path, "launch")
                if existing != dict(record):
                    raise StoreError(
                        "skill doctor launch key conflict",
                        status=409,
                        code="skill_doctor_launch_conflict",
                    )
                return existing
            self.atomic_json(path, dict(record))
        return dict(record)

    def write_skill_doctor_checkpoint(
        self,
        agent_id: Any,
        plan_id: Any,
        config: bytes,
    ) -> None:
        plan = self._id(plan_id, "skill doctor plan id")
        root = self._skill_doctor_root(agent_id) / "checkpoints" / plan
        path = root / "config.yaml"
        with self._lock:
            if path.is_file():
                if path.is_symlink() or path.read_bytes() != config:
                    raise StoreError(
                        "skill doctor checkpoint conflict",
                        status=409,
                        code="skill_doctor_checkpoint_conflict",
                    )
                return
            self.atomic_write(path, config, mode=0o440, replace=False)

    def read_skill_doctor_checkpoint(self, agent_id: Any, plan_id: Any) -> bytes:
        plan = self._id(plan_id, "skill doctor plan id")
        root = self._skill_doctor_root(agent_id) / "checkpoints" / plan
        path = root / "config.yaml"
        if path.is_symlink() or not path.is_file() or root.resolve() not in path.resolve().parents:
            raise StoreError(
                "skill doctor checkpoint is unavailable",
                status=409,
                code="skill_doctor_checkpoint_missing",
            )
        return path.read_bytes()

    def write_skill_doctor_checkpoint(
        self,
        agent_id: Any,
        plan_id: Any,
        config_content: bytes,
    ) -> None:
        plan = self._id(plan_id, "skill doctor plan id")
        if not plan.startswith("sdp_"):
            raise StoreError("invalid skill doctor plan id")
        root = self._skill_doctor_root(agent_id) / "checkpoints" / plan
        path = root / "config.yaml"
        with self._lock:
            if path.exists():
                if path.is_symlink() or path.read_bytes() != config_content:
                    raise StoreError(
                        "skill doctor checkpoint conflict",
                        status=409,
                        code="skill_doctor_checkpoint_conflict",
                    )
                return
            self.atomic_write(path, config_content, mode=0o440, replace=False)

    def read_skill_doctor_checkpoint(self, agent_id: Any, plan_id: Any) -> bytes:
        plan = self._id(plan_id, "skill doctor plan id")
        if not plan.startswith("sdp_"):
            raise StoreError("invalid skill doctor plan id")
        root = self._skill_doctor_root(agent_id) / "checkpoints" / plan
        path = root / "config.yaml"
        if path.is_symlink() or not path.is_file() or root.resolve() not in path.resolve().parents:
            raise StoreError(
                "skill doctor checkpoint is unavailable",
                status=409,
                code="skill_doctor_checkpoint_missing",
            )
        return path.read_bytes()
