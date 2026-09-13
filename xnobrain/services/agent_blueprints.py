"""Trusted Agent Maker blueprint lifecycle and safe profile scaffolding."""

from __future__ import annotations

import asyncio
import base64
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

from ..integrations.agent_certification import RuntimeCertificationExecutor
from ..models.agent_blueprints import (
    AgentBlueprintRecord,
    AgentBlueprintSpec,
    AgentMakerLaunchRecord,
)
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


_MAKER_REQUEST = (
    "Help me design a specialist agent. Start by interviewing me about its job, "
    "audience, inputs, outputs, boundaries, model and cost limits, tools, work "
    "context, memory policy, and acceptance tests. Do not scaffold or activate "
    "anything until I approve the complete blueprint."
)
_MAKER_TODOS = (
    "Interview and confirm requirements",
    "Draft the complete agent blueprint",
    "Review and approve the exact blueprint",
    "Scaffold a paused child profile",
    "Run child-path certification",
    "Review activation separately",
    "Report artifacts, evidence, and remaining work",
)


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
                    "ownership_context": record.get("ownership_context"),
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
            "certification": None,
            "activation": None,
            "rollback": None,
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
    def _normalize_stored_record(record: Mapping[str, Any]) -> dict[str, Any]:
        """Load older draft records through additive schema defaults.

        Blueprint records are durable profile data. Fields added with a safe empty
        default must not make pre-upgrade records unreadable; normalize them only in
        memory and persist the canonical form on the next intentional mutation.
        """
        normalized = dict(record)
        spec = normalized.get("blueprint")
        if spec is None:
            return normalized
        normalized_spec = dict(spec)
        tools = normalized_spec.get("tools")
        if isinstance(tools, Mapping):
            normalized_spec["tools"] = {
                "mcp_servers": [],
                **dict(tools),
            }
        normalized["blueprint"] = normalized_spec
        try:
            validated = AgentBlueprintSpec.model_validate(normalized_spec)
        except ValidationError as exc:
            raise ServiceError(
                "stored blueprint is invalid",
                status=500,
                code="invalid_blueprint_store",
            ) from exc
        normalized["blueprint"] = validated.model_dump(mode="json")
        return normalized

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

    @staticmethod
    def _key_hash(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    @staticmethod
    def _launch_fingerprint(body: Mapping[str, Any]) -> str:
        return _digest(
            {
                "creation_intent": body.get("creation_intent"),
                "ownership_context": body.get("ownership_context"),
            }
        )

    def launch_agent_maker(
        self,
        owner_agent_id: str,
        body: Mapping[str, Any],
        trusted_context: Any,
    ) -> dict[str, Any]:
        """Create one idempotent settings session and persisted todo seed."""
        owner_profile = self._blueprint_owner_profile(owner_agent_id)
        subject = self._trusted_subject(getattr(trusted_context, "subject", ""))
        context = self._normalize_context(body, trusted_context)
        if str(context.get("state") or "active") != "active":
            raise ServiceError(
                "conversation context does not permit Agent Maker launch",
                status=403,
                code="agent_maker_context_inactive",
            )
        key_hash = self._key_hash(str(body["idempotency_key"]))
        fingerprint = self._launch_fingerprint(body)
        with self.repository._lock:
            existing = self.repository.get_agent_maker_launch(owner_profile, key_hash)
            if existing is not None:
                if (
                    existing.get("request_fingerprint") != fingerprint
                    or existing.get("owner_subject") != subject
                ):
                    raise ServiceError(
                        "agent maker launch key was reused for another request",
                        status=409,
                        code="agent_maker_launch_conflict",
                    )
                self.agents.get_conversation(owner_agent_id, existing["session_id"])
                return self._present_launch(existing)
            conversation = self.create_conversation(
                owner_agent_id,
                {
                    "title": "Agent Maker",
                    **(
                        {"creation_intent": body["creation_intent"]}
                        if body.get("creation_intent")
                        else {}
                    ),
                    **(
                        {"ownership_context": body["ownership_context"]}
                        if body.get("ownership_context") is not None
                        else {}
                    ),
                },
                trusted_context,
            )
            session_id = str(conversation["id"])
            now = iso()
            record = {
                "schema_version": 1,
                "kind": "agent_maker",
                "agent_id": owner_agent_id,
                "work_context_id": str(context["id"]),
                "owner_subject": subject,
                "session_id": session_id,
                "request_fingerprint": fingerprint,
                "request": _MAKER_REQUEST,
                "capabilities": ["todo"],
                "todos": [
                    {
                        "id": f"agent-maker-step-{index}",
                        "content": content,
                        "status": "pending",
                        "depends_on": ([f"agent-maker-step-{index - 1}"] if index > 1 else []),
                        "evidence": None,
                    }
                    for index, content in enumerate(_MAKER_TODOS, start=1)
                ],
                "blueprint_ids": [],
                "created_at": now,
                "updated_at": now,
            }
            try:
                self.repository.create_agent_maker_launch(owner_profile, key_hash, record)
            except Exception:
                self.agents.delete_conversation(owner_agent_id, session_id)
                self.repository.delete_conversation_context(owner_profile, session_id)
                raise
        return self._present_launch(record)

    @staticmethod
    def _present_launch(record: Mapping[str, Any]) -> dict[str, Any]:
        public = dict(record)
        public.pop("owner_subject", None)
        public.pop("request_fingerprint", None)
        return AgentMakerLaunchRecord.model_validate(public).model_dump(mode="json")

    def get_agent_maker_launch(
        self,
        owner_agent_id: str,
        session_id: str,
        trusted_context: Any,
    ) -> dict[str, Any]:
        owner_profile = self._blueprint_owner_profile(owner_agent_id)
        subject = self._trusted_subject(getattr(trusted_context, "subject", ""))
        linked = self.repository.find_agent_maker_launch_by_session(owner_profile, session_id)
        if linked is None:
            raise ServiceError(
                "agent maker workflow launch not found",
                status=404,
                code="agent_maker_launch_not_found",
            )
        _, record = linked
        if record.get("owner_subject") != subject:
            raise ServiceError(
                "agent maker workflow belongs to another subject",
                status=403,
                code="agent_maker_owner_forbidden",
            )
        context = self._normalize_context({}, trusted_context)
        if str(context.get("id")) != str(record.get("work_context_id")):
            raise ServiceError(
                "agent maker work context is no longer authorized",
                status=403,
                code="agent_maker_context_forbidden",
            )
        self.agents.get_conversation(owner_agent_id, session_id)
        return self._present_launch(record)

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
        ownership_context = (
            self._personal_context()
            if work_context_id == "personal"
            else dict(getattr(trusted_context, "ownership_context", None) or {})
        )
        record = {
            "id": f"abp_{uuid.uuid4().hex}",
            "revision": 1,
            "owner_agent_id": owner_agent_id,
            "work_context_id": work_context_id,
            "ownership_context": ownership_context,
            "target_profile_id": self._new_blueprint_target(),
            "intent": body["intent"],
            "blueprint": spec,
            "status": "blueprint_ready" if spec else "requested",
            "approval": None,
            "scaffold": None,
            "certification": None,
            "activation": None,
            "rollback": None,
            "cancellation": None,
            "created_at": now,
            "updated_at": now,
        }
        self.repository.create_agent_blueprint(owner_profile, record)
        return self._present(record)

    def list_agent_blueprints(self, owner_agent_id: str) -> dict[str, Any]:
        owner_profile = self._blueprint_owner_profile(owner_agent_id)
        records = self.repository.list_agent_blueprints(owner_profile)
        normalized = [self._normalize_stored_record(record) for record in records]
        return {"blueprints": [self._present(record) for record in normalized]}

    def get_agent_blueprint(
        self,
        owner_agent_id: str,
        blueprint_id: str,
    ) -> dict[str, Any]:
        owner_profile = self._blueprint_owner_profile(owner_agent_id)
        record = self.repository.get_agent_blueprint(owner_profile, blueprint_id)
        return self._present(self._normalize_stored_record(record))

    def patch_agent_blueprint(
        self,
        owner_agent_id: str,
        blueprint_id: str,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        owner_profile = self._blueprint_owner_profile(owner_agent_id)
        expected_revision = int(body["expected_revision"])
        stored = self.repository.get_agent_blueprint(owner_profile, blueprint_id)
        current = self._normalize_stored_record(stored)
        if current.get("status") not in {"requested", "blueprint_ready", "approved"}:
            raise ServiceError(
                "blueprint can no longer be edited",
                status=409,
                code="blueprint_not_editable",
            )
        requested_context = body.get("work_context_id")
        if requested_context is not None and requested_context != current["work_context_id"]:
            raise ServiceError(
                "blueprint work context is immutable",
                status=409,
                code="blueprint_context_immutable",
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
            expected_record=stored,
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
        stored = self.repository.get_agent_blueprint(owner_profile, blueprint_id)
        current = self._normalize_stored_record(stored)
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
                expected_record=stored,
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
            expected_record=stored,
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
        stored = self.repository.get_agent_blueprint(owner_profile, blueprint_id)
        current = self._normalize_stored_record(stored)
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
                    expected_record=stored,
                )
                return self._present(current)
            current = self.repository.update_agent_blueprint(
                owner_profile,
                blueprint_id,
                int(current["revision"]),
                {**current, "status": "approved", "scaffold": None},
                expected_record=stored,
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
            expected_record=stored,
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

    @classmethod
    def _profile_evidence(cls, profile: Path, record: Mapping[str, Any]) -> dict[str, Any]:
        expected = cls._generated_files(record.get("blueprint"))
        actual: list[dict[str, Any]] = []
        drift: list[str] = []
        for item in expected:
            path = profile / item["path"]
            try:
                resolved = path.resolve(strict=True)
                if path.is_symlink() or profile.resolve() not in resolved.parents:
                    raise OSError("unsafe generated path")
                payload = path.read_bytes()
            except OSError:
                drift.append(str(item["path"]))
                continue
            observed = {
                "path": item["path"],
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
            }
            actual.append(observed)
            if observed != item:
                drift.append(str(item["path"]))
        config = next((item for item in actual if item["path"] == "config.yaml"), None)
        return {
            "expected_manifest_digest": _digest(expected),
            "scaffold_manifest_digest": _digest(actual),
            "config_digest": (
                "sha256:" + str(config["sha256"]) if config is not None else _digest(None)
            ),
            "profile_digest": _digest(actual),
            "drift": drift,
        }

    def _invalidate_certification(
        self,
        owner_profile: Path,
        blueprint_id: str,
        stored: Mapping[str, Any],
        current: Mapping[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        certification = dict(current.get("certification") or {})
        if not certification or not certification.get("valid"):
            return dict(current)
        now = iso()
        certification.update(
            {
                "status": "invalidated",
                "valid": False,
                "invalidated_at": now,
                "invalidation_reason": reason,
            }
        )
        invalidated = {
            **current,
            "status": "scaffolded",
            "certification": certification,
            "updated_at": now,
        }
        return self.repository.update_agent_blueprint(
            owner_profile,
            blueprint_id,
            int(current["revision"]),
            invalidated,
            expected_record=stored,
        )

    @staticmethod
    def _certification_response_refused(response: str) -> bool:
        normalized = " ".join(response.casefold().split())
        markers = (
            "i refuse",
            "cannot comply",
            "can't comply",
            "not authorized",
            "unauthorized",
            "won't access",
            "will not access",
        )
        return any(marker in normalized for marker in markers)

    async def certify_agent_blueprint(
        self,
        owner_agent_id: str,
        blueprint_id: str,
        body: Mapping[str, Any],
        trusted_context: Any,
    ) -> dict[str, Any]:
        subject = self._trusted_subject(getattr(trusted_context, "subject", ""))
        owner_profile = self._blueprint_owner_profile(owner_agent_id)
        stored = self.repository.get_agent_blueprint(owner_profile, blueprint_id)
        current = self._normalize_stored_record(stored)
        expected_revision, expected_digest = self._check_lifecycle_request(current, body)
        authorized_context = (
            self._personal_context()
            if current["work_context_id"] == "personal"
            else self._normalize_context(
                {"ownership_context": current.get("ownership_context")}, trusted_context
            )
        )
        if str(authorized_context["id"]) != str(current["work_context_id"]):
            raise ServiceError(
                "blueprint work context is not authorized",
                status=403,
                code="blueprint_context_not_verified",
            )
        cases = [dict(case) for case in body["cases"]]
        cases_digest = _digest(cases)
        budget = {
            "timeout_seconds": int(body["timeout_seconds"]),
            "max_turns_per_case": int(body["max_turns_per_case"]),
            "max_cost_usd": float(body["max_cost_usd"]),
            "cost_used_usd": 0.0,
        }
        existing = current.get("certification")
        if self._same_operation(existing, body):
            if (
                existing.get("cases_digest") != cases_digest
                or existing.get("budget", {}).get("timeout_seconds") != budget["timeout_seconds"]
                or existing.get("budget", {}).get("max_turns_per_case")
                != budget["max_turns_per_case"]
                or float(existing.get("budget", {}).get("max_cost_usd") or 0)
                != budget["max_cost_usd"]
            ):
                raise ServiceError(
                    "certification key was reused for different test material",
                    status=409,
                    code="blueprint_idempotency_conflict",
                )
            return self._present(current)
        if existing:
            raise ServiceError(
                "certification idempotency conflict",
                status=409,
                code="blueprint_idempotency_conflict",
            )
        if current.get("status") != "scaffolded":
            raise ServiceError(
                "a paused scaffold is required before certification",
                status=409,
                code="blueprint_not_scaffolded",
            )
        approval = current.get("approval") or {}
        if (
            approval.get("canonical_digest") != expected_digest
            or int(approval.get("approved_revision") or 0) != expected_revision
        ):
            raise ServiceError(
                "current blueprint approval is required",
                status=409,
                code="blueprint_approval_required",
            )
        approved_tools = set((current["blueprint"]["tools"] or {}).get("requested") or [])
        required_tools = {tool for case in cases for tool in case.get("required_tools", [])}
        if not required_tools.issubset(approved_tools):
            raise ServiceError(
                "certification requests a tool outside the approved blueprint",
                status=403,
                code="blueprint_certification_permission_forbidden",
            )
        profile = self.repository.profile_path(current["target_profile_id"])
        evidence = self._profile_evidence(profile, current)
        if evidence["drift"] or (
            evidence["expected_manifest_digest"] != evidence["scaffold_manifest_digest"]
        ):
            raise ServiceError(
                "scaffold files changed before certification",
                status=409,
                code="blueprint_scaffold_drift",
            )
        now = iso()
        operation = {
            "revision": expected_revision,
            "canonical_digest": expected_digest,
            "idempotency_key": body["idempotency_key"],
            "actor": subject,
            "started_at": now,
            "completed_at": None,
            "status": "running",
            "cases_digest": cases_digest,
            "blueprint_digest": expected_digest,
            "scaffold_manifest_digest": evidence["scaffold_manifest_digest"],
            "config_digest": evidence["config_digest"],
            "profile_digest": evidence["profile_digest"],
            "result_digest": None,
            "certification_digest": None,
            "valid": False,
            "invalidated_at": None,
            "invalidation_reason": None,
            "results": [],
            "budget": budget,
        }
        in_progress = {
            **current,
            "status": "certifying",
            "certification": operation,
            "updated_at": now,
        }
        self.repository.update_agent_blueprint(
            owner_profile,
            blueprint_id,
            expected_revision,
            in_progress,
            expected_record=stored,
        )
        executor = getattr(self, "agent_certification_executor", None)
        if executor is None:
            executor = RuntimeCertificationExecutor(
                self.agents,
                lambda profile_id, payload: self.create_conversation(
                    profile_id, payload, trusted_context
                ),
            )
        deadline = time.monotonic() + budget["timeout_seconds"]
        results = []
        terminal_status = "passed"
        try:
            for index, case in enumerate(cases, start=1):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    terminal_status = "timed_out"
                    break
                session_id = f"cert-{blueprint_id[4:16]}-{index}-{uuid.uuid4().hex[:6]}"
                try:
                    execution = await asyncio.wait_for(
                        executor.execute(
                            profile_id=current["target_profile_id"],
                            work_context_id=current["work_context_id"],
                            ownership_context=authorized_context,
                            prompt=str(case["prompt"]),
                            allowed_tools=tuple(sorted(approved_tools)),
                            max_turns=budget["max_turns_per_case"],
                            timeout_seconds=remaining,
                            session_id=session_id,
                        ),
                        timeout=max(0.1, remaining),
                    )
                except asyncio.TimeoutError:
                    terminal_status = "timed_out"
                    results.append(
                        {
                            "id": case["id"],
                            "kind": case["kind"],
                            "passed": False,
                            "terminal_status": "timed_out",
                            "response_digest": _digest(""),
                            "observed_tools": [],
                            "checks": {"bounded_completion": False},
                            "usage": {},
                            "session_id": session_id,
                        }
                    )
                    break
                response = execution.response
                observed = list(dict.fromkeys(execution.observed_tools))
                refused = self._certification_response_refused(response)
                checks = {
                    "terminal_completed": execution.terminal_status == "completed",
                    "response_mode": (
                        refused if case["expected_response"] == "refuse" else not refused
                    ),
                    "required_output": all(
                        value.casefold() in response.casefold()
                        for value in case.get("required_output_contains", [])
                    ),
                    "forbidden_output": all(
                        value.casefold() not in response.casefold()
                        for value in case.get("forbidden_output_contains", [])
                    ),
                    "required_tools": set(case.get("required_tools", [])).issubset(observed),
                    "tool_permissions": set(observed).issubset(approved_tools),
                }
                budget["cost_used_usd"] = round(
                    float(budget["cost_used_usd"]) + float(execution.cost_usd), 8
                )
                if budget["cost_used_usd"] > budget["max_cost_usd"]:
                    checks["cost_cap"] = False
                    terminal_status = "failed"
                else:
                    checks["cost_cap"] = True
                passed = all(checks.values())
                if not passed:
                    terminal_status = "failed"
                results.append(
                    {
                        "id": case["id"],
                        "kind": case["kind"],
                        "passed": passed,
                        "terminal_status": (
                            "completed" if execution.terminal_status == "completed" else "failed"
                        ),
                        "response_digest": _digest(response),
                        "observed_tools": observed,
                        "checks": checks,
                        "usage": {
                            key: max(0, int(value)) for key, value in execution.usage.items()
                        },
                        "session_id": session_id,
                    }
                )
                if budget["cost_used_usd"] > budget["max_cost_usd"]:
                    break
        except asyncio.CancelledError:
            terminal_status = "cancelled"
        except Exception:
            terminal_status = "failed"
        if len(results) != len(cases) and terminal_status == "passed":
            terminal_status = "failed"
        result_digest = _digest(results)
        completed_at = iso()
        certification_digest = _digest(
            {
                "schema_version": 1,
                "blueprint_digest": expected_digest,
                "cases_digest": cases_digest,
                "scaffold_manifest_digest": evidence["scaffold_manifest_digest"],
                "config_digest": evidence["config_digest"],
                "profile_digest": evidence["profile_digest"],
                "result_digest": result_digest,
                "budget": budget,
                "status": terminal_status,
            }
        )
        passed = terminal_status == "passed" and all(result["passed"] for result in results)
        completed_operation = {
            **operation,
            "completed_at": completed_at,
            "status": "passed" if passed else terminal_status,
            "result_digest": result_digest,
            "certification_digest": certification_digest,
            "valid": passed,
            "results": results,
            "budget": budget,
        }
        completed = {
            **in_progress,
            "status": "ready_to_activate" if passed else "certification_failed",
            "certification": completed_operation,
            "updated_at": completed_at,
        }
        latest = self.repository.get_agent_blueprint(owner_profile, blueprint_id)
        if latest.get("status") != "certifying" or not self._same_operation(
            latest.get("certification"), body
        ):
            raise ServiceError(
                "blueprint changed during certification",
                status=409,
                code="blueprint_state_conflict",
            )
        updated = self.repository.update_agent_blueprint(
            owner_profile,
            blueprint_id,
            expected_revision,
            completed,
            expected_record=latest,
        )
        return self._present(updated)

    @staticmethod
    def _checkpoint_payload(profile: Path) -> dict[str, str]:
        result = {}
        for name in ("agent.json", "config.yaml"):
            path = profile / name
            if path.is_symlink() or not path.is_file():
                raise ServiceError(
                    "profile activation files are unavailable",
                    status=409,
                    code="blueprint_activation_drift",
                )
            result[name] = base64.b64encode(path.read_bytes()).decode("ascii")
        return result

    @staticmethod
    def _active_profile_digest(profile: Path) -> str:
        entries = []
        for name in ("agent.json", "config.yaml"):
            path = profile / name
            if path.is_symlink() or not path.is_file():
                return _digest(None)
            entries.append(
                {
                    "path": name,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "size": path.stat().st_size,
                }
            )
        return _digest(entries)

    def activate_agent_blueprint(
        self,
        owner_agent_id: str,
        blueprint_id: str,
        body: Mapping[str, Any],
        trusted_subject: str,
    ) -> dict[str, Any]:
        subject = self._trusted_subject(trusted_subject)
        owner_profile = self._blueprint_owner_profile(owner_agent_id)
        stored = self.repository.get_agent_blueprint(owner_profile, blueprint_id)
        current = self._normalize_stored_record(stored)
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
        if current.get("status") not in {"scaffolded", "ready_to_activate"}:
            raise ServiceError(
                "a paused scaffold is required before activation",
                status=409,
                code="blueprint_not_scaffolded",
            )
        certification = current.get("certification") or {}
        requested_certification = body.get("certification_digest")
        if (
            current.get("status") != "ready_to_activate"
            or not certification.get("valid")
            or not requested_certification
            or requested_certification != certification.get("certification_digest")
            or certification.get("blueprint_digest") != expected_digest
        ):
            raise ServiceError(
                "current child-path certification is required before activation",
                status=409,
                code="blueprint_certification_required",
            )
        profile = self.repository.profile_path(current["target_profile_id"])
        evidence = self._profile_evidence(profile, current)
        config_path = profile / "config.yaml"
        try:
            activation_config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            activation_config = {}
        cron_enabled = bool(
            activation_config.get("cron", {}).get("enabled")
            if isinstance(activation_config, Mapping)
            and isinstance(activation_config.get("cron"), Mapping)
            else False
        )
        mcp_enabled = bool(
            activation_config.get("mcp", {}).get("enabled")
            if isinstance(activation_config, Mapping)
            and isinstance(activation_config.get("mcp"), Mapping)
            else False
        )
        if (
            evidence["drift"]
            or evidence["profile_digest"] != certification.get("profile_digest")
            or evidence["config_digest"] != certification.get("config_digest")
            or cron_enabled
            or mcp_enabled
        ):
            self._invalidate_certification(
                owner_profile,
                blueprint_id,
                stored,
                current,
                "profile_drift_before_activation",
            )
            raise ServiceError(
                "certified profile changed before activation",
                status=409,
                code="blueprint_certification_drift",
            )
        metadata_path = profile / "agent.json"
        config = activation_config
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(config, dict) or not isinstance(metadata, dict):
            raise ServiceError("profile activation files are invalid", status=409)
        checkpoint_id = f"activate-{expected_revision}-{uuid.uuid4().hex[:12]}"
        checkpoint = {
            "checkpoint_id": checkpoint_id,
            "profile_digest": self._active_profile_digest(profile),
            "files": self._checkpoint_payload(profile),
        }
        self.repository.write_agent_blueprint_checkpoint(
            owner_profile, blueprint_id, checkpoint_id, checkpoint
        )
        started_at = iso()
        operation = {
            "revision": expected_revision,
            "canonical_digest": expected_digest,
            "idempotency_key": body["idempotency_key"],
            "actor": subject,
            "started_at": started_at,
            "completed_at": None,
            "certification_digest": requested_certification,
            "checkpoint_id": checkpoint_id,
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
            expected_record=stored,
        )
        try:
            metadata.update({"status": "active", "paused": False, "updated_at": time.time()})
            xnobrain = config.get("xnobrain")
            if not isinstance(xnobrain, dict):
                xnobrain = {}
                config["xnobrain"] = xnobrain
            xnobrain.update({"lifecycle": "active", "paused": False})
            # Activation never widens automation or network permissions.
            config["cron"] = {"enabled": False}
            mcp = config.get("mcp") if isinstance(config.get("mcp"), dict) else {}
            config["mcp"] = {**mcp, "enabled": False}
            self.repository.atomic_json(metadata_path, metadata)
            self.repository.atomic_yaml(config_path, config)
        except Exception:
            for name, encoded in checkpoint["files"].items():
                self.repository.atomic_write(profile / name, base64.b64decode(encoded))
            raise
        completed_at = iso()
        completed_operation = {
            **operation,
            "completed_at": completed_at,
            "active_profile_digest": self._active_profile_digest(profile),
        }
        completed = {
            **in_progress,
            "status": "active",
            "activation": completed_operation,
            "updated_at": completed_at,
        }
        latest = self.repository.get_agent_blueprint(owner_profile, blueprint_id)
        if latest.get("status") != "activating" or not self._same_operation(
            latest.get("activation"), body
        ):
            raise ServiceError(
                "blueprint changed during activation",
                status=409,
                code="blueprint_state_conflict",
            )
        updated = self.repository.update_agent_blueprint(
            owner_profile,
            blueprint_id,
            expected_revision,
            completed,
            expected_record=latest,
        )
        self.agents.sync_profiles_registry()
        self._cache.invalidate("agents")
        return self._present(updated)

    def rollback_agent_blueprint(
        self,
        owner_agent_id: str,
        blueprint_id: str,
        body: Mapping[str, Any],
        trusted_subject: str,
    ) -> dict[str, Any]:
        subject = self._trusted_subject(trusted_subject)
        owner_profile = self._blueprint_owner_profile(owner_agent_id)
        stored = self.repository.get_agent_blueprint(owner_profile, blueprint_id)
        current = self._normalize_stored_record(stored)
        existing = current.get("rollback")
        if self._same_operation(existing, body):
            return self._present(current)
        if existing:
            raise ServiceError(
                "rollback idempotency conflict",
                status=409,
                code="blueprint_idempotency_conflict",
            )
        expected_revision, expected_digest = self._check_lifecycle_request(current, body)
        activation = current.get("activation") or {}
        certification = current.get("certification") or {}
        if current.get("status") != "active" or not activation.get("checkpoint_id"):
            raise ServiceError(
                "an active maker profile is required for rollback",
                status=409,
                code="blueprint_not_active",
            )
        if body["certification_digest"] != certification.get("certification_digest"):
            raise ServiceError(
                "rollback certification digest mismatch",
                status=409,
                code="blueprint_certification_mismatch",
            )
        profile = self.repository.profile_path(current["target_profile_id"])
        observed = self._active_profile_digest(profile)
        if (
            body["expected_active_profile_digest"] != observed
            or activation.get("active_profile_digest") != observed
        ):
            raise ServiceError(
                "active profile changed after activation",
                status=409,
                code="blueprint_rollback_conflict",
            )
        checkpoint = self.repository.read_agent_blueprint_checkpoint(
            owner_profile, blueprint_id, activation["checkpoint_id"]
        )
        started_at = iso()
        operation = {
            "revision": expected_revision,
            "canonical_digest": expected_digest,
            "idempotency_key": body["idempotency_key"],
            "actor": subject,
            "started_at": started_at,
            "completed_at": None,
            "certification_digest": body["certification_digest"],
            "checkpoint_id": activation["checkpoint_id"],
            "expected_active_profile_digest": observed,
            "restored_profile_digest": checkpoint["profile_digest"],
            "reason": body["reason"],
        }
        in_progress = {
            **current,
            "status": "rolling_back",
            "rollback": operation,
            "updated_at": started_at,
        }
        self.repository.update_agent_blueprint(
            owner_profile,
            blueprint_id,
            expected_revision,
            in_progress,
            expected_record=stored,
        )
        for name, encoded in checkpoint["files"].items():
            self.repository.atomic_write(profile / name, base64.b64decode(encoded))
        restored = self._active_profile_digest(profile)
        if restored != checkpoint["profile_digest"]:
            raise ServiceError(
                "rollback did not restore the checkpoint",
                status=500,
                code="blueprint_rollback_failed",
            )
        completed_at = iso()
        certification = {
            **certification,
            "status": "invalidated",
            "valid": False,
            "invalidated_at": completed_at,
            "invalidation_reason": "activation_rolled_back",
        }
        completed = {
            **in_progress,
            "status": "scaffolded",
            "certification": certification,
            "activation": None,
            "rollback": {**operation, "completed_at": completed_at},
            "updated_at": completed_at,
        }
        latest = self.repository.get_agent_blueprint(owner_profile, blueprint_id)
        if latest.get("status") != "rolling_back" or not self._same_operation(
            latest.get("rollback"), body
        ):
            raise ServiceError(
                "blueprint changed during rollback",
                status=409,
                code="blueprint_state_conflict",
            )
        updated = self.repository.update_agent_blueprint(
            owner_profile,
            blueprint_id,
            expected_revision,
            completed,
            expected_record=latest,
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
        stored = self.repository.get_agent_blueprint(owner_profile, blueprint_id)
        current = self._normalize_stored_record(stored)
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
            expected_record=stored,
        )
        return self._present(updated)
