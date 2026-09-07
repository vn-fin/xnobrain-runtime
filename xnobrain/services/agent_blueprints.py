"""Agent Maker blueprint draft, revision, and approval behavior."""

from __future__ import annotations

import hashlib
import json
import uuid
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
    """Own the reviewable lifecycle before any child profile is created."""

    def _blueprint_owner_profile(self, owner_agent_id: str):
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
    def _generated_files(spec: Mapping[str, Any] | None) -> list[dict[str, Any]]:
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
                        "model": {"default": model_slot["alias"]},
                        "agent": {"reasoning_effort": model_slot["reasoning_effort"]},
                        "approvals": {"mode": "manual"},
                        "skills": {"write_approval": True},
                        "memory": {
                            "policy": memory["policy"],
                            "write_approval": True,
                        },
                        "checkpoints": {"enabled": True},
                        "mcp": {"enabled": False, "requested": tools["mcp_servers"]},
                        "cron": {"enabled": False},
                        "xnobrain": {"lifecycle": "draft", "paused": True},
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
        return [
            {
                "path": path,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
            for path, content in files
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
        result = dict(record)
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

    def create_agent_blueprint(
        self,
        owner_agent_id: str,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        owner_profile = self._blueprint_owner_profile(owner_agent_id)
        now = iso()
        spec = body.get("blueprint")
        record = {
            "id": f"abp_{uuid.uuid4().hex}",
            "revision": 1,
            "owner_agent_id": owner_agent_id,
            "work_context_id": body["work_context_id"],
            "target_profile_id": self._new_blueprint_target(),
            "intent": body["intent"],
            "blueprint": spec,
            "status": "blueprint_ready" if spec else "requested",
            "approval": None,
            "created_at": now,
            "updated_at": now,
        }
        self.repository.create_agent_blueprint(owner_profile, record)
        return self._present(record)

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
        if current.get("status") in {"scaffolding", "certifying", "active", "cancelled"}:
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
        )
        return self._present(updated)

    def approve_agent_blueprint(
        self,
        owner_agent_id: str,
        blueprint_id: str,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        owner_profile = self._blueprint_owner_profile(owner_agent_id)
        expected_revision = int(body["expected_revision"])
        current = self.repository.get_agent_blueprint(owner_profile, blueprint_id)
        if int(current.get("revision") or 0) != expected_revision:
            raise ServiceError(
                "blueprint revision conflict",
                status=409,
                code="blueprint_revision_conflict",
            )
        if current.get("status") != "blueprint_ready" or not current.get("blueprint"):
            raise ServiceError(
                "a complete blueprint is required before approval",
                status=409,
                code="blueprint_not_ready",
            )
        expected_digest = _digest(self._approval_material(current))
        if body["canonical_digest"] != expected_digest:
            raise ServiceError(
                "blueprint approval digest mismatch",
                status=409,
                code="blueprint_digest_mismatch",
            )
        approved_at = iso()
        record = dict(current)
        record["status"] = "approved"
        record["approval"] = {
            "approved_revision": expected_revision,
            "canonical_digest": expected_digest,
            "binding": self._approval_binding(current),
            "approved_by": body["approved_by"],
            "approved_at": approved_at,
        }
        record["updated_at"] = approved_at
        updated = self.repository.update_agent_blueprint(
            owner_profile,
            blueprint_id,
            expected_revision,
            record,
        )
        return self._present(updated)
