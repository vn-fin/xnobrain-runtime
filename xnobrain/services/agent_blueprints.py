"""Trusted Agent Maker blueprint lifecycle and safe profile scaffolding."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

import yaml
from pydantic import ValidationError

from ..models.agent_blueprints import AgentBlueprintRecord, AgentBlueprintSpec
from .base import ServiceError, iso


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


class AgentBlueprintsServiceMixin:
    """Own review, scaffold, and explicit activation of specialist profiles."""

    def _blueprint_owner_profile(self, owner_agent_id: str) -> Path:
        profile = self._agent_profile_path(owner_agent_id)
        if profile.is_symlink() or not profile.is_dir():
            raise ServiceError("creator profile not found", status=404, code="not_found")
        return profile

    def _new_blueprint_target(self) -> str:
        existing = set(self.agents.list_agent_names())
        for _ in range(64):
            target = f"agent-{uuid.uuid4().hex[:12]}"
            if target not in existing and not self.repository.profile_path(target).exists():
                return target
        raise ServiceError(
            "failed to allocate target profile",
            status=409,
            code="target_profile_unavailable",
        )

    @staticmethod
    def _generated_file_contents(
        spec: Mapping[str, Any] | None,
    ) -> list[tuple[str, bytes]]:
        if not spec:
            return []
        persona = dict(spec["persona"])
        memory = dict(spec["memory"])
        model_slot = dict(spec["model_slot"])
        tools = dict(spec["tools"])
        files: list[tuple[str, bytes]] = [
            ("SOUL.md", persona["soul"].encode()),
            ("workspace/AGENTS.md", persona["agents_instructions"].encode()),
            (
                "config.yaml",
                yaml.safe_dump(
                    {
                        "model": {
                            "provider": "custom:xnobrain",
                            "default": model_slot["alias"],
                        },
                        "providers": {},
                        "agent": {"reasoning_effort": model_slot["reasoning_effort"]},
                        "terminal": {"backend": "local", "home_mode": "profile"},
                        "approvals": {"mode": "manual"},
                        "skills": {
                            "write_approval": True,
                            "disabled": [],
                        },
                        "memory": {
                            "policy": memory["policy"],
                            "write_approval": True,
                        },
                        "checkpoints": {"enabled": True},
                        "mcp": {"enabled": False, "requested": tools["mcp_servers"]},
                        "cron": {"enabled": False},
                        "xnobrain": {
                            "lifecycle": "draft",
                            "paused": True,
                            "global_skills_inherited": False,
                        },
                    },
                    sort_keys=False,
                    allow_unicode=True,
                ).encode(),
            ),
        ]
        for seed in memory["seed_sources"]:
            content = f"Provenance: {seed['provenance']}\n\n{seed['content']}\n"
            files.append((f"memories/seeds/{seed['id']}.md", content.encode()))
        for skill in spec["skills"]:
            files.append((f"skills/custom/{skill['id']}/SKILL.md", skill["content"].encode()))
        for directory in spec["workspace"]["directories"]:
            files.append((f"workspace/{directory}/.gitkeep", b""))
        certification = yaml.safe_dump(
            {"schema_version": 1, "acceptance": spec["acceptance"]},
            sort_keys=False,
            allow_unicode=True,
        ).encode()
        files.append((".xnobrain/certification.yaml", certification))
        return files

    @classmethod
    def _generated_files(cls, spec: Mapping[str, Any] | None) -> list[dict[str, Any]]:
        return [
            {
                "path": path,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
            for path, content in cls._generated_file_contents(spec)
        ]

    @classmethod
    def _approval_binding(cls, record: Mapping[str, Any]) -> dict[str, str]:
        spec = record.get("blueprint") or {}
        return {
            "content_digest": _digest(
                {
                    "intent": record["intent"],
                    "blueprint": spec,
                    "file_manifest": cls._generated_files(spec),
                }
            ),
            "permissions_digest": _digest(
                {
                    "tools": spec.get("tools"),
                    "skills": spec.get("skills"),
                    "automation": spec.get("automation"),
                }
            ),
            "model_digest": _digest(spec.get("model_slot")),
            "context_digest": _digest(
                {
                    "owner_agent_id": record["owner_agent_id"],
                    "work_context_id": record["work_context_id"],
                    "ownership": spec.get("ownership"),
                    "target_profile_id": record["target_profile_id"],
                }
            ),
        }

    @classmethod
    def _approval_material(cls, record: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "blueprint_id": record["id"],
            "revision": record["revision"],
            "binding": cls._approval_binding(record),
        }

    @classmethod
    def _present(cls, record: Mapping[str, Any]) -> dict[str, Any]:
        result = {
            "scaffold": None,
            "activation": None,
            "cancellation": None,
            **dict(record),
        }
        result["file_manifest"] = cls._generated_files(record.get("blueprint"))
        result["approval_binding"] = cls._approval_binding(record)
        result["canonical_digest"] = _digest(cls._approval_material(record))
        try:
            return AgentBlueprintRecord.model_validate(result).model_dump(mode="json")
        except ValidationError as exc:
            raise ServiceError(
                "blueprint record is invalid",
                status=500,
                code="invalid_blueprint_store",
            ) from exc

    @staticmethod
    def _validate_stored_spec(record: Mapping[str, Any]) -> None:
        spec = record.get("blueprint")
        if spec is None:
            return
        try:
            AgentBlueprintSpec.model_validate(spec)
        except ValidationError as exc:
            raise ServiceError(
                "stored blueprint is invalid",
                status=500,
                code="invalid_blueprint_store",
            ) from exc

    @staticmethod
    def _trusted_subject(trusted_subject: str) -> str:
        subject = str(trusted_subject or "").strip()
        if not subject:
            raise ServiceError(
                "trusted lifecycle subject is required",
                status=401,
                code="trusted_subject_required",
            )
        return subject

    @classmethod
    def _check_lifecycle_request(
        cls,
        current: Mapping[str, Any],
        body: Mapping[str, Any],
    ) -> tuple[int, str]:
        expected_revision = int(body["expected_revision"])
        if int(current.get("revision") or 0) != expected_revision:
            raise ServiceError(
                "blueprint revision conflict",
                status=409,
                code="blueprint_revision_conflict",
            )
        expected_digest = _digest(cls._approval_material(current))
        if body["canonical_digest"] != expected_digest:
            raise ServiceError(
                "blueprint lifecycle digest mismatch",
                status=409,
                code="blueprint_digest_mismatch",
            )
        return expected_revision, expected_digest

    def create_agent_blueprint(
        self,
        owner_agent_id: str,
        body: Mapping[str, Any],
        trusted_context: Any = None,
    ) -> dict[str, Any]:
        owner_profile = self._blueprint_owner_profile(owner_agent_id)
        work_context_id = str(body["work_context_id"])
        if work_context_id != "personal":
            subject = str(getattr(trusted_context, "subject", "") or "").strip()
            verified = getattr(trusted_context, "ownership_context", None)
            if not subject:
                raise ServiceError(
                    "trusted conversation context is required",
                    status=401,
                    code="trusted_context_required",
                )
            if (
                not isinstance(verified, Mapping)
                or str(verified.get("id") or "") != work_context_id
            ):
                raise ServiceError(
                    "blueprint work context was not verified by Control",
                    status=403,
                    code="blueprint_context_not_verified",
                )
        now = iso()
        spec = body.get("blueprint")
        record = {
            "id": f"abp_{uuid.uuid4().hex}",
            "revision": 1,
            "owner_agent_id": owner_agent_id,
            "work_context_id": work_context_id,
            "target_profile_id": self._new_blueprint_target(),
            "intent": body["intent"],
            "blueprint": spec,
            "status": "blueprint_ready" if spec else "requested",
            "approval": None,
            "scaffold": None,
            "activation": None,
            "cancellation": None,
            "created_at": now,
            "updated_at": now,
        }
        self.repository.create_agent_blueprint(owner_profile, record)
        return self._present(record)

    def list_agent_blueprints(self, owner_agent_id: str) -> dict[str, Any]:
        owner_profile = self._blueprint_owner_profile(owner_agent_id)
        records = self.repository.list_agent_blueprints(owner_profile)
        for record in records:
            self._validate_stored_spec(record)
        return {"blueprints": [self._present(record) for record in records]}

    def get_agent_blueprint(
        self,
        owner_agent_id: str,
        blueprint_id: str,
    ) -> dict[str, Any]:
        owner_profile = self._blueprint_owner_profile(owner_agent_id)
        record = self.repository.get_agent_blueprint(owner_profile, blueprint_id)
        self._validate_stored_spec(record)
        return self._present(record)

    def patch_agent_blueprint(
        self,
        owner_agent_id: str,
        blueprint_id: str,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        owner_profile = self._blueprint_owner_profile(owner_agent_id)
        expected_revision = int(body["expected_revision"])
        current = self.repository.get_agent_blueprint(owner_profile, blueprint_id)
        if current.get("status") not in {"requested", "blueprint_ready", "approved"}:
            raise ServiceError(
                "blueprint can no longer be edited",
                status=409,
                code="blueprint_not_editable",
            )
        record = dict(current)
        for field in ("intent", "work_context_id", "blueprint"):
            if field in body:
                record[field] = body[field]
        record["revision"] = expected_revision + 1
        record["status"] = "blueprint_ready" if record.get("blueprint") else "requested"
        record["approval"] = None
        record["updated_at"] = iso()
        updated = self.repository.update_agent_blueprint(
            owner_profile,
            blueprint_id,
            expected_revision,
            record,
            expected_record=current,
        )
        return self._present(updated)

    def approve_agent_blueprint(
        self,
        owner_agent_id: str,
        blueprint_id: str,
        body: Mapping[str, Any],
        trusted_subject: str,
    ) -> dict[str, Any]:
        subject = self._trusted_subject(trusted_subject)
        owner_profile = self._blueprint_owner_profile(owner_agent_id)
        current = self.repository.get_agent_blueprint(owner_profile, blueprint_id)
        expected_revision, expected_digest = self._check_lifecycle_request(current, body)
        if current.get("status") != "blueprint_ready" or not current.get("blueprint"):
            raise ServiceError(
                "a complete blueprint is required before approval",
                status=409,
                code="blueprint_not_ready",
            )
        if body["decision"] == "deny":
            now = iso()
            record = {
                **current,
                "status": "cancelled",
                "approval": None,
                "cancellation": {
                    "revision": expected_revision,
                    "canonical_digest": expected_digest,
                    "idempotency_key": f"denial:{blueprint_id}:{expected_revision}",
                    "actor": subject,
                    "started_at": now,
                    "completed_at": now,
                    "decision": "deny",
                    "reason": body.get("reason"),
                },
                "updated_at": now,
            }
            updated = self.repository.update_agent_blueprint(
                owner_profile,
                blueprint_id,
                expected_revision,
                record,
                expected_record=current,
            )
            return self._present(updated)
        approved_at = iso()
        record = {
            **current,
            "status": "approved",
            "approval": {
                "approved_revision": expected_revision,
                "canonical_digest": expected_digest,
                "binding": self._approval_binding(current),
                "approved_by": subject,
                "approved_at": approved_at,
            },
            "updated_at": approved_at,
        }
        updated = self.repository.update_agent_blueprint(
            owner_profile,
            blueprint_id,
            expected_revision,
            record,
            expected_record=current,
        )
        return self._present(updated)

    @staticmethod
    def _same_operation(
        operation: Mapping[str, Any] | None,
        body: Mapping[str, Any],
    ) -> bool:
        return bool(
            operation
            and operation.get("idempotency_key") == body.get("idempotency_key")
            and int(operation.get("revision") or 0) == int(body.get("expected_revision") or 0)
            and operation.get("canonical_digest") == body.get("canonical_digest")
        )

    def _write_staged_files(
        self,
        staging: Path,
        spec: Mapping[str, Any],
        record: Mapping[str, Any],
    ) -> None:
        for relative, content in self._generated_file_contents(spec):
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
            if target.is_symlink() or staging.resolve() not in target.resolve().parents:
                raise ServiceError("unsafe scaffold path", code="unsafe_scaffold_path")
            self.repository.atomic_write(target, content, mode=0o640, replace=False)
        now = time.time()
        metadata = {
            "name": record["target_profile_id"],
            "profile_name": record["target_profile_id"],
            "display_name": spec["name"],
            "title": spec["name"],
            "description": spec["purpose"],
            "status": "draft",
            "paused": True,
            "blueprint_id": record["id"],
            "blueprint_revision": record["revision"],
            "created_at": now,
            "updated_at": now,
        }
        self.repository.atomic_json(staging / "agent.json", metadata)
        self.repository.atomic_yaml(
            staging / "profile.yaml",
            {"name": record["target_profile_id"], "description": spec["purpose"]},
        )

    def _profile_matches_scaffold(
        self,
        final: Path,
        record: Mapping[str, Any],
    ) -> bool:
        metadata_path = final / "agent.json"
        if not final.is_dir() or final.is_symlink() or not metadata_path.is_file():
            return False
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(metadata, Mapping):
            return False
        if metadata.get("blueprint_id") != record["id"] or int(
            metadata.get("blueprint_revision") or 0
        ) != int(record["revision"]):
            return False
        for item in self._generated_files(record.get("blueprint")):
            path = final / item["path"]
            if not path.is_file() or path.is_symlink():
                return False
            if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                return False
        return True

    def scaffold_agent_blueprint(
        self,
        owner_agent_id: str,
        blueprint_id: str,
        body: Mapping[str, Any],
        trusted_subject: str,
    ) -> dict[str, Any]:
        subject = self._trusted_subject(trusted_subject)
        owner_profile = self._blueprint_owner_profile(owner_agent_id)
        current = self.repository.get_agent_blueprint(owner_profile, blueprint_id)
        existing_scaffold = current.get("scaffold")
        if self._same_operation(existing_scaffold, body):
            if current.get("status") == "scaffolded":
                return self._present(current)
            final = self.repository.profile_path(current["target_profile_id"])
            if self._profile_matches_scaffold(final, current):
                completed_at = iso()
                recovered = {
                    **current,
                    "status": "scaffolded",
                    "scaffold": {**existing_scaffold, "completed_at": completed_at},
                    "updated_at": completed_at,
                }
                current = self.repository.update_agent_blueprint(
                    owner_profile,
                    blueprint_id,
                    int(current["revision"]),
                    recovered,
                    expected_record=current,
                )
                return self._present(current)
            current = self.repository.update_agent_blueprint(
                owner_profile,
                blueprint_id,
                int(current["revision"]),
                {**current, "status": "approved", "scaffold": None},
                expected_record=current,
            )
            existing_scaffold = None
        if existing_scaffold:
            raise ServiceError(
                "scaffold idempotency conflict",
                status=409,
                code="blueprint_idempotency_conflict",
            )
        expected_revision, expected_digest = self._check_lifecycle_request(current, body)
        approval = current.get("approval") or {}
        if (
            current.get("status") != "approved"
            or int(approval.get("approved_revision") or 0) != expected_revision
            or approval.get("canonical_digest") != expected_digest
        ):
            raise ServiceError(
                "current blueprint approval is required",
                status=409,
                code="blueprint_approval_required",
            )
        final = self.repository.profile_path(current["target_profile_id"])
        if final.exists() and not self._profile_matches_scaffold(final, current):
            raise ServiceError(
                "target profile already exists",
                status=409,
                code="target_profile_conflict",
            )
        started_at = iso()
        operation = {
            "revision": expected_revision,
            "canonical_digest": expected_digest,
            "idempotency_key": body["idempotency_key"],
            "actor": subject,
            "started_at": started_at,
            "completed_at": None,
        }
        in_progress = {
            **current,
            "status": "scaffolding",
            "scaffold": operation,
            "updated_at": started_at,
        }
        self.repository.update_agent_blueprint(
            owner_profile,
            blueprint_id,
            expected_revision,
            in_progress,
            expected_record=current,
        )
        staging = Path(
            tempfile.mkdtemp(
                prefix=f".{current['target_profile_id']}.scaffold-",
                dir=self.repository.profiles_root,
            )
        )
        try:
            if final.exists():
                if not self._profile_matches_scaffold(final, current):
                    raise ServiceError(
                        "target profile already exists",
                        status=409,
                        code="target_profile_conflict",
                    )
            else:
                self._write_staged_files(staging, current["blueprint"], current)
                os.replace(staging, final)
                self.repository._sync_dir(final.parent)
            completed_at = iso()
            completed = {
                **in_progress,
                "status": "scaffolded",
                "scaffold": {**operation, "completed_at": completed_at},
                "updated_at": completed_at,
            }
            updated = self.repository.update_agent_blueprint(
                owner_profile,
                blueprint_id,
                expected_revision,
                completed,
                expected_record=in_progress,
            )
            self.agents.sync_profiles_registry()
            self._cache.invalidate("agents")
            return self._present(updated)
        except Exception:
            if self._profile_matches_scaffold(final, current):
                completed_at = iso()
                recovered = {
                    **in_progress,
                    "status": "scaffolded",
                    "scaffold": {**operation, "completed_at": completed_at},
                    "updated_at": completed_at,
                }
                updated = self.repository.update_agent_blueprint(
                    owner_profile,
                    blueprint_id,
                    expected_revision,
                    recovered,
                    expected_record=in_progress,
                )
                return self._present(updated)
            self.repository.update_agent_blueprint(
                owner_profile,
                blueprint_id,
                expected_revision,
                {**current, "status": "approved", "scaffold": None},
                expected_record=in_progress,
            )
            raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def activate_agent_blueprint(
        self,
        owner_agent_id: str,
        blueprint_id: str,
        body: Mapping[str, Any],
        trusted_subject: str,
    ) -> dict[str, Any]:
        subject = self._trusted_subject(trusted_subject)
        owner_profile = self._blueprint_owner_profile(owner_agent_id)
        current = self.repository.get_agent_blueprint(owner_profile, blueprint_id)
        existing_activation = current.get("activation")
        if self._same_operation(existing_activation, body):
            return self._present(current)
        if existing_activation:
            raise ServiceError(
                "activation idempotency conflict",
                status=409,
                code="blueprint_idempotency_conflict",
            )
        expected_revision, expected_digest = self._check_lifecycle_request(current, body)
        if current.get("status") != "scaffolded":
            raise ServiceError(
                "a paused scaffold is required before activation",
                status=409,
                code="blueprint_not_scaffolded",
            )
        # This trusted slice intentionally keeps certification out of scope.
        # Activation is therefore a separate authenticated decision, not a
        # certification claim and never an implicit scaffold side effect.
        final = self.repository.profile_path(current["target_profile_id"])
        if not self._profile_matches_scaffold(final, current):
            raise ServiceError(
                "scaffolded profile has drifted",
                status=409,
                code="blueprint_scaffold_conflict",
            )
        started_at = iso()
        operation = {
            "revision": expected_revision,
            "canonical_digest": expected_digest,
            "idempotency_key": body["idempotency_key"],
            "actor": subject,
            "started_at": started_at,
            "completed_at": None,
        }
        in_progress = {
            **current,
            "status": "activating",
            "activation": operation,
            "updated_at": started_at,
        }
        self.repository.update_agent_blueprint(
            owner_profile,
            blueprint_id,
            expected_revision,
            in_progress,
            expected_record=current,
        )
        metadata_path = final / "agent.json"
        config_path = final / "config.yaml"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        metadata["status"] = "active"
        metadata["paused"] = False
        metadata["updated_at"] = time.time()
        managed = config.setdefault("xnobrain", {})
        managed["lifecycle"] = "active"
        managed["paused"] = False
        self.repository.atomic_json(metadata_path, metadata)
        self.repository.atomic_yaml(config_path, config)
        completed_at = iso()
        completed = {
            **in_progress,
            "status": "active",
            "activation": {**operation, "completed_at": completed_at},
            "updated_at": completed_at,
        }
        updated = self.repository.update_agent_blueprint(
            owner_profile,
            blueprint_id,
            expected_revision,
            completed,
            expected_record=in_progress,
        )
        self.agents.sync_profiles_registry()
        self._cache.invalidate("agents")
        return self._present(updated)

    def cancel_agent_blueprint(
        self,
        owner_agent_id: str,
        blueprint_id: str,
        body: Mapping[str, Any],
        trusted_subject: str,
    ) -> dict[str, Any]:
        subject = self._trusted_subject(trusted_subject)
        owner_profile = self._blueprint_owner_profile(owner_agent_id)
        current = self.repository.get_agent_blueprint(owner_profile, blueprint_id)
        existing_cancellation = current.get("cancellation")
        if self._same_operation(existing_cancellation, body):
            return self._present(current)
        if existing_cancellation:
            raise ServiceError(
                "cancellation idempotency conflict",
                status=409,
                code="blueprint_idempotency_conflict",
            )
        expected_revision, expected_digest = self._check_lifecycle_request(current, body)
        if current.get("status") == "active":
            raise ServiceError(
                "active profiles require the separate profile lifecycle",
                status=409,
                code="blueprint_already_active",
            )
        if current.get("status") == "cancelled":
            raise ServiceError(
                "blueprint is already cancelled",
                status=409,
                code="blueprint_cancel_conflict",
            )
        now = iso()
        cancellation = {
            "revision": expected_revision,
            "canonical_digest": expected_digest,
            "idempotency_key": body["idempotency_key"],
            "actor": subject,
            "started_at": now,
            "completed_at": now,
            "decision": "cancel",
            "reason": body.get("reason"),
        }
        record = {
            **current,
            "status": "cancelled",
            "cancellation": cancellation,
            "updated_at": now,
        }
        updated = self.repository.update_agent_blueprint(
            owner_profile,
            blueprint_id,
            expected_revision,
            record,
            expected_record=current,
        )
        return self._present(updated)
