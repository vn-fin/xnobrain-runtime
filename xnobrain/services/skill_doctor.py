"""Bounded, authorized, and reversible Skill Doctor workflow."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

import yaml

from ..defaults import BIG_BROTHER_AGENT_ID, BIG_BROTHER_SKILL_ID
from .base import ServiceError, iso

_MAX_SKILL_FILE_BYTES = 200_000
_MAX_SUPPORT_FILES = 100
_MAX_SUPPORT_BYTES = 1_048_576
_MAX_DESCRIPTION_CHARS = 1_000
_MAX_INVENTORY_CANDIDATES = 500
_MAX_INVENTORY_BYTES = 8 * 1_048_576
_PROTECTED_SKILLS = frozenset({BIG_BROTHER_SKILL_ID, "skill-optimizer"})
_TOKENIZER = "unicode-codepoint-estimate-v1"
_WORKFLOW_REQUEST = (
    "Audit this agent’s effective skills in the locked work context. Inventory skills "
    "first, report honest usage coverage and context cost, diagnose evidence-backed "
    "issues, and prepare recommendations. Do not read conversation content, run paid "
    "evaluation, disable, edit, or publish skills without my separate consent and approval."
)
_WORKFLOW_TODOS = (
    "Inventory effective skills and discovery state",
    "Summarize usage, coverage, and context cost",
    "Analyze issues and evidence",
    "Review recommendations",
    "Optionally evaluate approved candidates",
    "Approve and apply selected changes",
    "Validate results and prepare the final report",
)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _key_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class SkillDoctorService:
    """Own static diagnosis, selected history consent, plans, and recovery."""

    def __init__(self, platform: Any):
        self.platform = platform
        self.repository = platform.repository
        self.agents = platform.agents
        self.analytics = platform.analytics
        self.optimizations = platform.skill_optimizations

    def _profile(self, agent_id: str) -> Path:
        if agent_id == BIG_BROTHER_AGENT_ID:
            raise ServiceError(
                "protected system profile cannot be diagnosed here",
                status=403,
                code="protected_skill",
            )
        self.agents.describe_agent(agent_id, include_memory=False)
        profile = self.repository.profile_path(agent_id)
        if profile.is_symlink() or not profile.is_dir():
            raise ServiceError("agent profile not found", status=404, code="agent_not_found")
        return profile

    @staticmethod
    def _authorize_context(context_id: str, trusted: Any) -> str:
        from .analytics import authorize_work_context

        return authorize_work_context(context_id, trusted)

    @staticmethod
    def _require_human(trusted: Any, purpose: str) -> str:
        subject = str(getattr(trusted, "subject", "") or "").strip()
        if not subject:
            raise ServiceError(
                f"verified human identity is required to {purpose}",
                status=401,
                code="trusted_subject_required",
            )
        return subject

    def _authorize_record(self, record: Mapping[str, Any], trusted: Any) -> None:
        context_id = str(record["work_context_id"])
        self._authorize_context(context_id, trusted)
        if context_id == "personal":
            subject = self._require_human(trusted, "access Personal Skill Doctor data")
            if subject != str(record.get("owner_subject") or ""):
                raise ServiceError(
                    "Personal Skill Doctor data belongs to another verified subject",
                    status=403,
                    code="skill_doctor_owner_forbidden",
                )

    @staticmethod
    def _fingerprint(body: Mapping[str, Any], *fields: str) -> str:
        return _digest({field: body.get(field) for field in fields})

    def launch(
        self,
        agent_id: str,
        body: Mapping[str, Any],
        trusted: Any,
    ) -> dict[str, Any]:
        """Create one persisted workflow session without starting a paid run."""
        self._profile(agent_id)
        subject = self._require_human(trusted, "launch Skill Doctor")
        context = self.platform._normalize_context(body, trusted)
        context_id = self._authorize_context(str(context["id"]), trusted)
        key_hash = _key_hash(str(body["idempotency_key"]))
        fingerprint = self._fingerprint(
            body,
            "creation_intent",
            "ownership_context",
        )
        with self.repository._lock:
            existing = self.repository.get_skill_doctor_launch(agent_id, key_hash)
            if existing is not None:
                if existing.get("request_fingerprint") != fingerprint:
                    raise ServiceError(
                        "skill doctor launch key was reused for a different request",
                        status=409,
                        code="skill_doctor_launch_conflict",
                    )
                self._authorize_record(existing, trusted)
                self.agents.get_conversation(agent_id, existing["session_id"])
                return self._present_launch(existing)
            conversation = self.platform.create_conversation(
                agent_id,
                {
                    "title": f"Skill Doctor {key_hash[:12]}",
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
                trusted,
            )
            session_id = str(conversation["id"])
            now = iso()
            record = {
                "schema_version": 1,
                "kind": "skill_doctor",
                "agent_id": agent_id,
                "work_context_id": context_id,
                "owner_subject": subject,
                "session_id": session_id,
                "request_fingerprint": fingerprint,
                "request": _WORKFLOW_REQUEST,
                "capabilities": ["todo"],
                "todos": [
                    {
                        "id": f"skill-doctor-step-{index}",
                        "content": content,
                        "status": "pending",
                        "finding_ids": [],
                        "skill_ids": [],
                        "depends_on": ([f"skill-doctor-step-{index - 1}"] if index > 1 else []),
                    }
                    for index, content in enumerate(_WORKFLOW_TODOS, start=1)
                ],
                "report_ids": [],
                "plan_ids": [],
                "operation_ids": [],
                "created_at": now,
                "updated_at": now,
            }
            try:
                self.repository.create_skill_doctor_launch(agent_id, key_hash, record)
            except Exception:
                self.agents.delete_conversation(agent_id, session_id)
                self.repository.delete_conversation_context(
                    self.repository.profile_path(agent_id), session_id
                )
                raise
        return self._present_launch(record)

    def _present_launch(self, record: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(record)
        result.pop("request_fingerprint", None)
        result.pop("owner_subject", None)
        return result

    def get_launch(
        self,
        agent_id: str,
        session_id: str,
        trusted: Any,
    ) -> dict[str, Any]:
        self._profile(agent_id)
        self._require_human(trusted, "read a Skill Doctor workflow")
        linked = self.repository.find_skill_doctor_launch_by_session(agent_id, session_id)
        if linked is None:
            raise ServiceError(
                "skill doctor workflow launch not found",
                status=404,
                code="skill_doctor_launch_not_found",
            )
        _, record = linked
        self._authorize_record(record, trusted)
        return self._present_launch(record)

    async def create_report(
        self,
        agent_id: str,
        body: Mapping[str, Any],
        trusted: Any,
    ) -> dict[str, Any]:
        profile = self._profile(agent_id)
        subject = self._require_human(trusted, "create a Skill Doctor report")
        context_id = self._authorize_context(
            str(body.get("work_context_id") or "personal"), trusted
        )
        fingerprint = _digest(
            {
                "work_context_id": context_id,
                "range_from": float(body["range_from"]),
                "range_to": float(body["range_to"]),
                "mode": body.get("mode") or "static",
                "session_ids": list(body.get("session_ids") or []),
                "consent_to_read_session_content": bool(
                    body.get("consent_to_read_session_content")
                ),
                "max_session_bytes": int(body.get("max_session_bytes", 262_144)),
                "inventory_limit": int(body.get("inventory_limit", 100)),
                "finding_limit": int(body.get("finding_limit", 100)),
                "workflow_session_id": body.get("workflow_session_id"),
            }
        )
        key_hash = _key_hash(str(body["idempotency_key"]))
        with self.repository._lock:
            existing = self.repository.find_skill_doctor_report_by_idempotency(agent_id, key_hash)
            if existing is not None:
                if existing.get("request_fingerprint") != fingerprint:
                    raise ServiceError(
                        "skill doctor report key was reused for a different request",
                        status=409,
                        code="skill_doctor_report_conflict",
                    )
                self._authorize_record(existing, trusted)
                return self._present(existing)

            launch_link = self._workflow_launch(
                agent_id, context_id, body.get("workflow_session_id"), trusted
            )
            session_analysis = self._selected_session_analysis(
                agent_id,
                context_id,
                body,
                trusted,
            )
            complete_inventory, inventory_meta = self._inventory(profile, _MAX_INVENTORY_CANDIDATES)
            inventory_limit = int(body.get("inventory_limit", 100))
            inventory = complete_inventory[:inventory_limit]
            usage = await self.analytics.skill_usage(
                agent_id,
                start_epoch=float(body["range_from"]),
                end_epoch=float(body["range_to"]),
                work_context_id=context_id,
                limit=100,
                trusted_context=trusted,
            )
            findings = self._findings(complete_inventory, usage)
            finding_limit = int(body.get("finding_limit", 100))
            finding_page = findings[:finding_limit]
            totals = self._totals(
                agent_id,
                context_id,
                float(body["range_from"]),
                float(body["range_to"]),
                complete_inventory,
                usage,
                findings,
            )
            inventory_digest = _digest(
                [
                    {
                        "skill_id": item["skill_id"],
                        "digest": item["digest"],
                        "enabled": item["enabled"],
                        "source_scope": item["source_scope"],
                        "discovered": item["discovered"],
                    }
                    for item in complete_inventory
                ]
            )
            now = iso()
            report_id = "sdr_" + uuid.uuid4().hex
            report = {
                "schema_version": 1,
                "id": report_id,
                "revision": 1,
                "status": "completed",
                "agent_id": agent_id,
                "work_context_id": context_id,
                "owner_subject": subject,
                "workflow_session_id": body.get("workflow_session_id"),
                "mode": body.get("mode") or "static",
                "range_from": float(body["range_from"]),
                "range_to": float(body["range_to"]),
                "selected_session_ids": list(body.get("session_ids") or []),
                "session_analysis": session_analysis,
                "inventory_digest": inventory_digest,
                "inventory": inventory,
                "inventory_page": {
                    "returned": len(inventory),
                    "total": inventory_meta["total"],
                    "truncated": inventory_meta["truncated"],
                    "limit": int(body.get("inventory_limit", 100)),
                    "next_cursor": None,
                },
                "findings": finding_page,
                "finding_page": {
                    "returned": len(finding_page),
                    "total": len(findings),
                    "truncated": len(findings) > finding_limit,
                    "limit": finding_limit,
                    "next_cursor": None,
                },
                "usage": usage,
                "totals": totals,
                "context_cost": self._context_cost(complete_inventory),
                "todos": self._finding_todos(finding_page),
                "plan_ids": [],
                "request_fingerprint": fingerprint,
                "idempotency_key_hash": key_hash,
                "created_at": now,
                "updated_at": now,
            }
            self.repository.create_skill_doctor_record(agent_id, "reports", report)
            if launch_link is not None:
                launch_hash, launch = launch_link
                updated_launch = {
                    **launch,
                    "report_ids": [*launch.get("report_ids", []), report_id],
                    "todos": [*launch.get("todos", []), *report["todos"]],
                    "updated_at": now,
                }
                self.repository.update_skill_doctor_launch(
                    agent_id,
                    launch_hash,
                    updated_launch,
                    expected_record=launch,
                )
        return self._present(report)

    def get_report(
        self,
        agent_id: str,
        report_id: str,
        trusted: Any,
    ) -> dict[str, Any]:
        self._profile(agent_id)
        self._require_human(trusted, "read a Skill Doctor report")
        report = self.repository.get_skill_doctor_record(agent_id, "reports", report_id)
        self._authorize_record(report, trusted)
        return self._present(report)

    def create_plan(
        self,
        agent_id: str,
        report_id: str,
        body: Mapping[str, Any],
        trusted: Any,
    ) -> dict[str, Any]:
        profile = self._profile(agent_id)
        self._require_human(trusted, "create a Skill Doctor plan")
        with self.repository._lock:
            report = self.repository.get_skill_doctor_record(agent_id, "reports", report_id)
            self._authorize_record(report, trusted)
            if int(report["revision"]) != int(body["expected_report_revision"]):
                raise ServiceError(
                    "skill doctor report revision conflict",
                    status=409,
                    code="skill_doctor_revision_conflict",
                )
            current, _ = self._inventory(profile, _MAX_INVENTORY_CANDIDATES)
            current_digest = _digest(
                [
                    {
                        "skill_id": item["skill_id"],
                        "digest": item["digest"],
                        "enabled": item["enabled"],
                        "source_scope": item["source_scope"],
                        "discovered": item["discovered"],
                    }
                    for item in current
                ]
            )
            if (
                body["expected_inventory_digest"] != report["inventory_digest"]
                or current_digest != report["inventory_digest"]
            ):
                raise ServiceError(
                    "skill inventory changed; run the report again",
                    status=409,
                    code="skill_doctor_inventory_conflict",
                )
            inventory = {item["skill_id"]: item for item in report["inventory"]}
            findings = {item["id"]: item for item in report["findings"]}
            actions = []
            for requested in body["actions"]:
                action = dict(requested)
                skill = inventory.get(str(action["skill_id"]))
                if skill is None:
                    raise ServiceError(
                        "plan skill is not in the report inventory",
                        status=409,
                        code="skill_doctor_skill_conflict",
                    )
                linked = list(action["finding_ids"])
                if any(
                    finding_id not in findings
                    or skill["skill_id"] not in findings[finding_id]["skill_ids"]
                    for finding_id in linked
                ):
                    raise ServiceError(
                        "plan action must link matching report findings",
                        status=409,
                        code="skill_doctor_finding_conflict",
                    )
                if action["action"] in {"disable", "re_enable"}:
                    self._validate_enablement_action(skill, action["action"])
                if action["action"] == "improve":
                    optimization = self.optimizations.get(
                        agent_id,
                        str(action["optimization_id"]),
                        trusted,
                    )
                    if optimization["work_context_id"] != report["work_context_id"]:
                        raise ServiceError(
                            "optimization context does not match the report",
                            status=403,
                            code="skill_doctor_optimization_forbidden",
                        )
                    if skill["skill_id"] not in {
                        patch["skill_id"] for patch in optimization["patches"]
                    }:
                        raise ServiceError(
                            "optimization does not target the selected skill",
                            status=409,
                            code="skill_doctor_optimization_conflict",
                        )
                action["dependencies"] = self._dependencies(agent_id, skill["skill_id"], skill)
                action["blocked"] = bool(action["dependencies"])
                actions.append(action)
            if any(action["action"] == "improve" for action in actions) and len(actions) != 1:
                raise ServiceError(
                    "improvement must use a dedicated plan for atomic FT0004 apply/rollback",
                    status=409,
                    code="skill_doctor_mixed_plan_conflict",
                )
            now = iso()
            plan_id = "sdp_" + uuid.uuid4().hex
            plan = {
                "schema_version": 1,
                "id": plan_id,
                "revision": 1,
                "status": "blocked" if any(action["blocked"] for action in actions) else "ready",
                "agent_id": agent_id,
                "work_context_id": report["work_context_id"],
                "owner_subject": report["owner_subject"],
                "report_id": report_id,
                "report_revision": report["revision"],
                "inventory_digest": report["inventory_digest"],
                "actions": actions,
                "digest": "",
                "apply": None,
                "rollback": None,
                "created_at": now,
                "updated_at": now,
            }
            plan["digest"] = _digest(
                {
                    "agent_id": agent_id,
                    "work_context_id": plan["work_context_id"],
                    "report_id": report_id,
                    "inventory_digest": plan["inventory_digest"],
                    "actions": actions,
                }
            )
            self.repository.create_skill_doctor_record(agent_id, "plans", plan)
            report["plan_ids"] = [*report.get("plan_ids", []), plan_id]
            report["revision"] = int(report["revision"]) + 1
            report["updated_at"] = now
            self.repository.update_skill_doctor_record(
                agent_id,
                "reports",
                report_id,
                int(body["expected_report_revision"]),
                report,
            )
            self._append_launch_links(
                agent_id,
                report.get("workflow_session_id"),
                plan_ids=[plan_id],
            )
        return self._present(plan)

    def apply(
        self,
        agent_id: str,
        plan_id: str,
        body: Mapping[str, Any],
        trusted: Any,
    ) -> dict[str, Any]:
        subject = self._require_human(trusted, "apply a Skill Doctor plan")
        profile = self._profile(agent_id)
        with self.optimizations._lock(agent_id), self.agents._skill_sync_lock:
            plan = self.repository.get_skill_doctor_record(agent_id, "plans", plan_id)
            self._authorize_record(plan, trusted)
            if (
                plan.get("status") == "applied"
                and (plan.get("apply") or {}).get("idempotency_key") == body["idempotency_key"]
            ):
                return self._present(plan)
            self._check_plan(plan, body)
            if plan["status"] == "blocked":
                raise ServiceError(
                    "plan has protected, active, or configured dependencies",
                    status=409,
                    code="skill_doctor_dependency_conflict",
                )
            if plan["status"] != "ready":
                raise ServiceError(
                    "skill doctor plan is not ready",
                    status=409,
                    code="skill_doctor_plan_not_ready",
                )
            self._check_current_inventory(agent_id, plan)
            for action in plan["actions"]:
                skill = self._inventory_item(profile, action["skill_id"])
                if action["action"] in {"disable", "re_enable"}:
                    self._validate_enablement_action(skill, action["action"])
                    dependencies = self._dependencies(agent_id, action["skill_id"], skill)
                    if dependencies:
                        raise ServiceError(
                            "skill dependency changed after plan review",
                            status=409,
                            code="skill_doctor_dependency_conflict",
                        )
            config_path = profile / "config.yaml"
            config_before = config_path.read_bytes() if config_path.is_file() else b"{}\n"
            self.repository.write_skill_doctor_checkpoint(agent_id, plan_id, config_before)
            for action in plan["actions"]:
                if action["action"] != "improve":
                    continue
                optimization = self.optimizations.get(agent_id, action["optimization_id"], trusted)
                if optimization.get("status") != "approved":
                    raise ServiceError(
                        "all improvements require current FT0004 approval before apply",
                        status=409,
                        code="skill_doctor_optimization_approval_required",
                    )
            optimization_requests = {
                str(item["optimization_id"]): item["request"]
                for item in body.get("optimization_applies") or []
            }
            expected_optimizations = {
                str(action["optimization_id"])
                for action in plan["actions"]
                if action["action"] == "improve"
            }
            if set(optimization_requests) != expected_optimizations:
                raise ServiceError(
                    "improve actions require exact FT0004 apply approval fields",
                    status=409,
                    code="skill_doctor_optimization_approval_required",
                )
            optimization_results = []
            for action in plan["actions"]:
                if action["action"] == "improve":
                    optimization_results.append(
                        self.optimizations.apply(
                            agent_id,
                            action["optimization_id"],
                            optimization_requests[action["optimization_id"]],
                            trusted,
                        )
                    )
            config = self.agents._read_config(profile)
            disabled = self.agents._disabled_skills(config)
            for action in plan["actions"]:
                if action["action"] == "disable":
                    disabled.add(action["skill_id"])
                elif action["action"] == "re_enable":
                    disabled.discard(action["skill_id"])
            if any(action["action"] in {"disable", "re_enable"} for action in plan["actions"]):
                self.agents._normalize_agent_skill_config(config)
                config["skills"]["disabled"] = sorted(disabled)
                self.repository.atomic_yaml(profile / "config.yaml", config)
            config_digest = (
                "sha256:" + hashlib.sha256((profile / "config.yaml").read_bytes()).hexdigest()
            )
            applied_digest = _digest(
                {
                    "config": config_digest,
                    "optimizations": [item["id"] for item in optimization_results],
                }
            )
            now = iso()
            updated = {
                **plan,
                "revision": int(plan["revision"]) + 1,
                "status": "applied",
                "apply": {
                    "idempotency_key": body["idempotency_key"],
                    "applied_by": subject,
                    "applied_at": now,
                    "applied_digest": applied_digest,
                    "config_digest": config_digest,
                    "optimization_ids": [item["id"] for item in optimization_results],
                    "checkpoint_retained": True,
                },
                "updated_at": now,
            }
            self.repository.update_skill_doctor_record(
                agent_id,
                "plans",
                plan_id,
                int(plan["revision"]),
                updated,
                expected_record=plan,
            )
            report = self.repository.get_skill_doctor_record(agent_id, "reports", plan["report_id"])
            self._append_launch_links(
                agent_id,
                report.get("workflow_session_id"),
                operation_ids=[plan_id, *updated["apply"]["optimization_ids"]],
            )
        return self._present(updated)

    def rollback(
        self,
        agent_id: str,
        plan_id: str,
        body: Mapping[str, Any],
        trusted: Any,
    ) -> dict[str, Any]:
        subject = self._require_human(trusted, "rollback a Skill Doctor plan")
        profile = self._profile(agent_id)
        with self.optimizations._lock(agent_id), self.agents._skill_sync_lock:
            plan = self.repository.get_skill_doctor_record(agent_id, "plans", plan_id)
            self._authorize_record(plan, trusted)
            if (
                plan.get("status") == "rolled_back"
                and (plan.get("rollback") or {}).get("idempotency_key") == body["idempotency_key"]
            ):
                return self._present(plan)
            if int(plan["revision"]) != int(body["expected_revision"]):
                raise ServiceError(
                    "skill doctor revision conflict",
                    status=409,
                    code="skill_doctor_revision_conflict",
                )
            apply = plan.get("apply") or {}
            if plan["status"] != "applied" or body["expected_applied_digest"] != apply.get(
                "applied_digest"
            ):
                raise ServiceError(
                    "applied plan digest conflict",
                    status=409,
                    code="skill_doctor_rollback_conflict",
                )
            expected_disabled = {
                action["skill_id"] for action in plan["actions"] if action["action"] == "disable"
            }
            expected_enabled = {
                action["skill_id"] for action in plan["actions"] if action["action"] == "re_enable"
            }
            current_disabled = self.agents._disabled_skills(self.agents._read_config(profile))
            if not expected_disabled.issubset(current_disabled) or (
                expected_enabled & current_disabled
            ):
                raise ServiceError(
                    "skill enablement changed before rollback",
                    status=409,
                    code="skill_doctor_rollback_conflict",
                )
            current_config_digest = (
                "sha256:" + hashlib.sha256((profile / "config.yaml").read_bytes()).hexdigest()
            )
            if current_config_digest != apply.get("config_digest"):
                raise ServiceError(
                    "profile config changed before rollback",
                    status=409,
                    code="skill_doctor_rollback_conflict",
                )
            rollback_requests = {
                str(item["optimization_id"]): item["request"]
                for item in body.get("optimization_rollbacks") or []
            }
            expected_optimizations = {
                str(action["optimization_id"])
                for action in plan["actions"]
                if action["action"] == "improve"
            }
            if set(rollback_requests) != expected_optimizations:
                raise ServiceError(
                    "improvement rollback requires exact FT0004 rollback fields",
                    status=409,
                    code="skill_doctor_optimization_rollback_required",
                )
            optimization_results = []
            for action in reversed(plan["actions"]):
                if action["action"] == "improve":
                    optimization_results.append(
                        self.optimizations.rollback(
                            agent_id,
                            action["optimization_id"],
                            rollback_requests[action["optimization_id"]],
                            trusted,
                        )
                    )
            checkpoint = self.repository.read_skill_doctor_checkpoint(agent_id, plan_id)
            self.repository.atomic_write(profile / "config.yaml", checkpoint)
            now = iso()
            updated = {
                **plan,
                "revision": int(plan["revision"]) + 1,
                "status": "rolled_back",
                "rollback": {
                    "idempotency_key": body["idempotency_key"],
                    "rolled_back_by": subject,
                    "rolled_back_at": now,
                    "reason": body["reason"],
                    "optimization_ids": [item["id"] for item in optimization_results],
                },
                "updated_at": now,
            }
            self.repository.update_skill_doctor_record(
                agent_id,
                "plans",
                plan_id,
                int(plan["revision"]),
                updated,
                expected_record=plan,
            )
        return self._present(updated)

    def _append_launch_links(
        self,
        agent_id: str,
        session_id: Any,
        *,
        plan_ids: list[str] | None = None,
        operation_ids: list[str] | None = None,
    ) -> None:
        if not session_id:
            return
        linked = self.repository.find_skill_doctor_launch_by_session(agent_id, str(session_id))
        if linked is None:
            raise ServiceError(
                "linked Skill Doctor workflow is unavailable",
                status=409,
                code="skill_doctor_launch_not_found",
            )
        key_hash, launch = linked
        updated = {
            **launch,
            "plan_ids": list(dict.fromkeys([*launch.get("plan_ids", []), *(plan_ids or [])])),
            "operation_ids": list(
                dict.fromkeys([*launch.get("operation_ids", []), *(operation_ids or [])])
            ),
            "updated_at": iso(),
        }
        self.repository.update_skill_doctor_launch(
            agent_id, key_hash, updated, expected_record=launch
        )

    @staticmethod
    def _check_plan(plan: Mapping[str, Any], body: Mapping[str, Any]) -> None:
        if int(plan.get("revision") or 0) != int(body["expected_revision"]):
            raise ServiceError(
                "skill doctor revision conflict",
                status=409,
                code="skill_doctor_revision_conflict",
            )
        if plan.get("digest") != body["plan_digest"]:
            raise ServiceError(
                "skill doctor plan digest conflict",
                status=409,
                code="skill_doctor_plan_conflict",
            )

    def _check_current_inventory(self, agent_id: str, plan: Mapping[str, Any]) -> None:
        current, _ = self._inventory(self._profile(agent_id), _MAX_INVENTORY_CANDIDATES)
        digest = _digest(
            [
                {
                    "skill_id": item["skill_id"],
                    "digest": item["digest"],
                    "enabled": item["enabled"],
                    "source_scope": item["source_scope"],
                    "discovered": item["discovered"],
                }
                for item in current
            ]
        )
        if digest != plan["inventory_digest"]:
            raise ServiceError(
                "skill inventory changed after plan review",
                status=409,
                code="skill_doctor_inventory_conflict",
            )

    def _inventory_item(self, profile: Path, skill_id: str) -> dict[str, Any]:
        inventory, _ = self._inventory(profile, 200)
        item = next((value for value in inventory if value["skill_id"] == skill_id), None)
        if item is None:
            raise ServiceError(
                "skill is no longer in effective inventory",
                status=409,
                code="skill_doctor_skill_conflict",
            )
        return item

    def _inventory(self, profile: Path, limit: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        config = self.agents._read_config(profile)
        disabled = self.agents._disabled_skills(config)
        roots: list[tuple[str, Path]] = [("agent", profile / "skills")]
        raw_external = (
            ((config.get("skills") or {}).get("external_dirs") or [])
            if isinstance(config.get("skills"), Mapping)
            else []
        )
        if isinstance(raw_external, str):
            raw_external = [raw_external]
        for value in raw_external if isinstance(raw_external, list) else []:
            expanded = Path(os.path.expandvars(os.path.expanduser(str(value))))
            if not expanded.is_absolute():
                expanded = profile / expanded
            roots.append(("inherited", expanded))
        install = str(os.environ.get("HERMES_INSTALL_DIR") or "").strip()
        if (
            str(os.environ.get("RUNTIME_INCLUDE_PACKAGED_SKILLS") or "").lower()
            in {
                "1",
                "true",
                "yes",
                "on",
            }
            and install
        ):
            roots.append(("packaged", Path(install) / "skills"))

        inventory: list[dict[str, Any]] = []
        seen: set[str] = set()
        total = 0
        inspected_bytes = 0
        scan_truncated = False
        for source_scope, root in roots:
            if not root.is_dir() or root.is_symlink():
                continue
            try:
                resolved_root = root.resolve(strict=True)
            except OSError:
                continue
            for skill_file in sorted(root.rglob("SKILL.md")):
                if total >= _MAX_INVENTORY_CANDIDATES:
                    scan_truncated = True
                    break
                if any(
                    part in {".archive", ".git", "node_modules", "__pycache__"}
                    for part in skill_file.parts
                ):
                    continue
                try:
                    candidate_size = skill_file.lstat().st_size
                except OSError:
                    candidate_size = 0
                if inspected_bytes + candidate_size > _MAX_INVENTORY_BYTES:
                    scan_truncated = True
                    break
                inspected_bytes += candidate_size
                total += 1
                item = self._inspect_skill(
                    resolved_root,
                    skill_file,
                    source_scope,
                    disabled,
                    seen,
                )
                if len(inventory) < limit:
                    inventory.append(item)
                seen.add(item["skill_id"])
        inventory.sort(key=lambda item: (item["skill_id"], item["source_scope"]))
        return inventory, {
            "total": total,
            "truncated": total > limit or scan_truncated,
            "scan_truncated": scan_truncated,
            "inspected_bytes": inspected_bytes,
        }

    def _inspect_skill(
        self,
        root: Path,
        skill_file: Path,
        source_scope: str,
        disabled: set[str],
        seen: set[str],
    ) -> dict[str, Any]:
        issues = []
        try:
            resolved = skill_file.resolve(strict=True)
            if root not in resolved.parents or skill_file.is_symlink():
                raise OSError("unsafe skill path")
            size = resolved.stat().st_size
            if size > _MAX_SKILL_FILE_BYTES:
                raise OSError("SKILL.md exceeds static inspection byte limit")
            content = resolved.read_bytes()
            text = content.decode("utf-8")
        except (OSError, UnicodeDecodeError) as error:
            fallback = skill_file.parent.name[:128] or "invalid-skill"
            return {
                "skill_id": fallback,
                "version": None,
                "digest": None,
                "source_scope": source_scope,
                "relative_path": self._safe_relative(skill_file.parent, root),
                "enabled": fallback not in disabled,
                "protected": source_scope != "agent" or fallback in _PROTECTED_SKILLS,
                "discovered": False,
                "shadowed": fallback in seen,
                "description": "",
                "description_chars": 0,
                "body_bytes": 0,
                "support_file_count": 0,
                "support_bytes": 0,
                "related_skills": [],
                "issues": [{"code": "unreadable", "message": str(error)[:200]}],
            }
        frontmatter: Mapping[str, Any] = {}
        if not text.startswith("---") or len(text.split("---", 2)) != 3:
            issues.append({"code": "invalid_frontmatter", "message": "frontmatter missing"})
        else:
            try:
                parsed = yaml.safe_load(text.split("---", 2)[1]) or {}
                if isinstance(parsed, Mapping):
                    frontmatter = parsed
                else:
                    issues.append(
                        {"code": "invalid_frontmatter", "message": "frontmatter is not an object"}
                    )
            except yaml.YAMLError:
                issues.append(
                    {"code": "invalid_frontmatter", "message": "frontmatter YAML is invalid"}
                )
        skill_id = str(frontmatter.get("name") or skill_file.parent.name).strip()[:128]
        if not skill_id:
            skill_id = skill_file.parent.name[:128] or "invalid-skill"
            issues.append({"code": "missing_name", "message": "skill name is missing"})
        description = str(frontmatter.get("description") or "").strip()
        if not description:
            issues.append({"code": "missing_description", "message": "description is missing"})
        metadata = frontmatter.get("metadata")
        hermes = metadata.get("hermes") if isinstance(metadata, Mapping) else None
        related = (
            hermes.get("related_skills")
            if isinstance(hermes, Mapping)
            else frontmatter.get("related_skills")
        )
        if isinstance(related, str):
            related = [related]
        related_ids = [
            str(value).strip()[:128]
            for value in (related if isinstance(related, list) else [])[:20]
            if str(value).strip()
        ]
        support_count = 0
        support_bytes = 0
        missing_refs = []
        references = set(
            match.group(1)
            for match in re.finditer(
                r"(?:references|scripts|assets|templates)/([A-Za-z0-9._/-]+)",
                text,
            )
        )
        for relative in sorted(references)[:_MAX_SUPPORT_FILES]:
            target = skill_file.parent / relative.split("#", 1)[0]
            try:
                resolved_target = target.resolve(strict=True)
                if skill_file.parent.resolve() not in resolved_target.parents:
                    raise OSError
                if not resolved_target.is_file():
                    raise OSError
                support_count += 1
                support_bytes += min(resolved_target.stat().st_size, _MAX_SUPPORT_BYTES)
            except OSError:
                missing_refs.append(relative[:200])
        if missing_refs:
            issues.append(
                {
                    "code": "missing_reference",
                    "message": "referenced support files are missing",
                    "references": missing_refs[:20],
                }
            )
        discovered = skill_id not in seen and skill_id not in disabled
        return {
            "skill_id": skill_id,
            "version": str(frontmatter.get("version") or "")[:64] or None,
            "digest": "sha256:" + hashlib.sha256(content).hexdigest(),
            "source_scope": source_scope,
            "relative_path": self._safe_relative(skill_file.parent, root),
            "enabled": skill_id not in disabled,
            "protected": source_scope != "agent" or skill_id in _PROTECTED_SKILLS,
            "discovered": discovered and skill_id not in seen and skill_id not in disabled,
            "shadowed": skill_id in seen,
            "description": description[:_MAX_DESCRIPTION_CHARS],
            "description_chars": len(description),
            "body_bytes": len(content),
            "support_file_count": support_count,
            "support_bytes": min(support_bytes, _MAX_SUPPORT_BYTES),
            "related_skills": related_ids,
            "issues": issues,
        }

    @staticmethod
    def _safe_relative(path: Path, root: Path) -> str:
        try:
            return path.resolve().relative_to(root).as_posix()
        except (OSError, ValueError):
            return "unavailable"

    def _findings(
        self,
        inventory: list[Mapping[str, Any]],
        usage: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        findings = []
        by_description: dict[str, list[str]] = defaultdict(list)
        words: dict[str, set[str]] = {}
        for item in inventory:
            skill_id = str(item["skill_id"])
            description = str(item.get("description") or "")
            normalized = " ".join(re.findall(r"[a-z0-9]+", description.lower()))
            if normalized:
                by_description[normalized].append(skill_id)
                words[skill_id] = set(normalized.split())
            for issue in item.get("issues") or []:
                findings.append(
                    self._finding(
                        str(issue["code"]),
                        "high"
                        if issue["code"] in {"unreadable", "invalid_frontmatter"}
                        else "medium",
                        [skill_id],
                        str(issue["message"]),
                        "static metadata and package references",
                        "review",
                    )
                )
            if int(item.get("description_chars") or 0) > 500:
                findings.append(
                    self._finding(
                        "large_description",
                        "low",
                        [skill_id],
                        "discovery description exceeds 500 characters",
                        f"description_chars={item['description_chars']}",
                        "improve",
                    )
                )
        usage_by_skill: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in usage.get("items") or []:
            usage_by_skill[str(row.get("skill_id") or "")].append(row)
        coverage = dict(usage.get("coverage") or {})
        for item in inventory:
            if not item.get("enabled") or not coverage.get("instrumented"):
                continue
            rows = usage_by_skill.get(str(item["skill_id"]), [])
            observed_loads = sum(int(row.get("loaded_count") or 0) for row in rows)
            if observed_loads == 0:
                findings.append(
                    self._finding(
                        "no_observed_use",
                        "low",
                        [str(item["skill_id"])],
                        "no successful load was observed in the disclosed covered interval",
                        f"coverage={coverage.get('coverage_start')}..{coverage.get('coverage_end')}",
                        "review",
                    )
                )
        for identifiers in by_description.values():
            if len(identifiers) > 1:
                findings.append(
                    self._finding(
                        "duplicate_description",
                        "high",
                        identifiers,
                        "multiple skills have identical normalized discovery descriptions",
                        "normalized descriptions match exactly",
                        "improve",
                    )
                )
        identifiers = sorted(words)
        for index, left in enumerate(identifiers):
            for right in identifiers[index + 1 :]:
                union = words[left] | words[right]
                if len(union) < 3:
                    continue
                score = len(words[left] & words[right]) / len(union)
                if score >= 0.75:
                    findings.append(
                        self._finding(
                            "trigger_overlap",
                            "medium",
                            [left, right],
                            "discovery descriptions have strongly overlapping trigger terms",
                            f"jaccard={score:.2f}",
                            "review",
                        )
                    )
        unique = {item["id"]: item for item in findings}
        return sorted(unique.values(), key=lambda item: (item["severity"], item["id"]))

    @staticmethod
    def _finding(
        code: str,
        confidence: str,
        skill_ids: list[str],
        summary: str,
        evidence: str,
        suggested_action: str,
    ) -> dict[str, Any]:
        material = {"code": code, "skill_ids": sorted(skill_ids), "evidence": evidence}
        return {
            "id": "sdf_" + hashlib.sha256(_canonical(material)).hexdigest()[:24],
            "code": code,
            "severity": "error" if code in {"unreadable", "invalid_frontmatter"} else "warning",
            "confidence": confidence,
            "scope": "selected_agent",
            "skill_ids": sorted(skill_ids),
            "summary": summary,
            "evidence": evidence,
            "suggested_action": suggested_action,
            "automatic_disable": False,
        }

    @staticmethod
    def _context_cost(inventory: list[Mapping[str, Any]]) -> dict[str, Any]:
        discovery_bytes = sum(
            len(str(item["skill_id"]).encode()) + len(str(item.get("description") or "").encode())
            for item in inventory
            if item.get("discovered")
        )
        on_demand_bytes = sum(
            int(item.get("body_bytes") or 0) + int(item.get("support_bytes") or 0)
            for item in inventory
        )
        return {
            "tokenizer": _TOKENIZER,
            "model": None,
            "discovery_payload_bytes": discovery_bytes,
            "discovery_estimated_tokens": (discovery_bytes + 3) // 4,
            "on_demand_available_bytes": on_demand_bytes,
            "on_demand_estimated_tokens": (on_demand_bytes + 3) // 4,
            "spend_usd": None,
            "savings_claim": None,
        }

    def _totals(
        self,
        agent_id: str,
        context_id: str,
        start_epoch: float,
        end_epoch: float,
        inventory: list[Mapping[str, Any]],
        usage: Mapping[str, Any],
        findings: list[Mapping[str, Any]],
    ) -> dict[str, Any]:
        items = list(usage.get("items") or [])
        sessions: set[str] = set()
        runs: set[str] = set()
        identities_available = self.repository.has_skill_usage_events(agent_id)
        if identities_available:
            contexts = {context_id}
            if context_id == "personal":
                contexts.add(f"personal:{agent_id}")
            window = self.repository.read_skill_usage_window(
                agent_id,
                start_epoch=start_epoch,
                end_epoch=end_epoch,
                work_context_ids=contexts,
            )
            for event in window.get("events") or []:
                session_id = str(event.get("session_id") or "")
                if session_id:
                    sessions.add(session_id)
                if event.get("event_type") in {"skill.loaded", "skill.run_associated"}:
                    run_id = str(event.get("run_id") or "")
                    if run_id:
                        runs.add(run_id)
            for rollup in window.get("rollups") or []:
                for row in (rollup.get("items") or {}).values():
                    if not isinstance(row, Mapping):
                        continue
                    sessions.update(str(value) for value in row.get("session_ids") or [])
                    runs.update(str(value) for value in row.get("run_ids") or [])
            sessions.discard("")
            runs.discard("")
        return {
            "skills": len(inventory),
            "enabled": sum(bool(item.get("enabled")) for item in inventory),
            "disabled": sum(not bool(item.get("enabled")) for item in inventory),
            "discovered": sum(bool(item.get("discovered")) for item in inventory),
            "shadowed": sum(bool(item.get("shadowed")) for item in inventory),
            "unreadable_or_invalid": sum(bool(item.get("issues")) for item in inventory),
            "findings": len(findings),
            "requested": (
                sum(int(item.get("requested_count") or 0) for item in items)
                if all(item.get("requested_count") is not None for item in items)
                else None
            ),
            "observed_loads": (
                sum(int(item.get("loaded_count") or 0) for item in items)
                if all(item.get("loaded_count") is not None for item in items)
                else None
            ),
            "distinct_sessions": len(sessions) if identities_available else None,
            "distinct_runs": len(runs) if identities_available else None,
            "dedupe_rule": "set-union across retained event and rollup identities",
        }

    def _workflow_launch(
        self,
        agent_id: str,
        context_id: str,
        session_id: Any,
        trusted: Any,
    ) -> tuple[str, dict[str, Any]] | None:
        if session_id is None:
            return None
        linked = self.repository.find_skill_doctor_launch_by_session(agent_id, str(session_id))
        if linked is None:
            raise ServiceError(
                "workflow session is not a persisted Skill Doctor launch",
                status=409,
                code="skill_doctor_launch_not_found",
            )
        _, launch = linked
        if launch.get("work_context_id") != context_id:
            raise ServiceError(
                "workflow session context does not match report context",
                status=403,
                code="skill_doctor_session_forbidden",
            )
        self._authorize_record(launch, trusted)
        return linked

    def _selected_session_analysis(
        self,
        agent_id: str,
        context_id: str,
        body: Mapping[str, Any],
        trusted: Any,
    ) -> dict[str, Any]:
        session_ids = list(body.get("session_ids") or [])
        if not session_ids:
            return {
                "consented": False,
                "selected": 0,
                "analyzed": 0,
                "bytes": 0,
                "content_retained": False,
                "model_used": False,
            }
        subject = self._require_human(trusted, "analyze selected sessions")
        total = 0
        analyzed = 0
        profile = self._profile(agent_id)
        for session_id in session_ids:
            stored_context = self.repository.get_conversation_context(profile, session_id)
            if stored_context is None:
                raise ServiceError(
                    "selected session has no verifiable ownership context",
                    status=403,
                    code="skill_doctor_session_forbidden",
                )
            context = self.platform._public_context(stored_context)
            if (
                str(context.get("id") or "") != context_id
                or context.get("state") != "active"
                or (
                    context_id == "personal"
                    and str(stored_context.get("actor_user_id") or "") != subject
                )
            ):
                raise ServiceError(
                    "selected session is outside the active authorized context",
                    status=403,
                    code="skill_doctor_session_forbidden",
                )
            payload = self.agents.get_conversation(agent_id, session_id)
            for message in payload.get("messages") or []:
                timestamp = float(message.get("timestamp") or 0)
                if not float(body["range_from"]) < timestamp <= float(body["range_to"]):
                    continue
                if str(message.get("role") or "") not in {"user", "assistant"}:
                    continue
                total += len(str(message.get("content") or "").encode())
                if total > int(body.get("max_session_bytes", 262_144)):
                    raise ServiceError(
                        "selected session content exceeds max_session_bytes",
                        status=413,
                        code="skill_doctor_sessions_too_large",
                    )
            analyzed += 1
        return {
            "consented": True,
            "selected": len(session_ids),
            "analyzed": analyzed,
            "bytes": total,
            "content_retained": False,
            "untrusted_evidence": True,
            "model_used": False,
        }

    @staticmethod
    def _finding_todos(findings: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "id": "sdt_" + finding["id"][4:],
                "content": f"Review {finding['code']} for {', '.join(finding['skill_ids'])}",
                "status": "pending",
                "finding_ids": [finding["id"]],
                "skill_ids": list(finding["skill_ids"]),
                "priority": "high" if finding["severity"] == "error" else "medium",
                "depends_on": [],
                "evidence": finding["evidence"],
            }
            for finding in findings
        ]

    @staticmethod
    def _validate_enablement_action(skill: Mapping[str, Any], action: str) -> None:
        if skill.get("protected") or skill.get("source_scope") != "agent":
            raise ServiceError(
                "protected, inherited, and packaged skills cannot be changed here",
                status=403,
                code="protected_skill",
            )
        if action == "disable" and not skill.get("enabled"):
            raise ServiceError(
                "skill is already disabled",
                status=409,
                code="skill_doctor_enablement_conflict",
            )
        if action == "re_enable" and skill.get("enabled"):
            raise ServiceError(
                "skill is already enabled",
                status=409,
                code="skill_doctor_enablement_conflict",
            )

    def _dependencies(
        self,
        agent_id: str,
        skill_id: str,
        skill: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        dependencies = []
        active_count = int(self.agents._active_agent_counts.get(agent_id, 0))
        if active_count:
            dependencies.append(
                {"kind": "active_run", "id": agent_id, "detail": "active agent execution"}
            )
        for job in self.repository.list_crons():
            configured = job.get("skills") or ([job.get("skill")] if job.get("skill") else [])
            if job.get("agent_id") == agent_id and skill_id in configured:
                dependencies.append(
                    {
                        "kind": "schedule",
                        "id": str(job.get("id") or ""),
                        "detail": "configured skill",
                    }
                )
        for team in self.repository.list_teams():
            linked = []
            for step in team.get("workflow") or []:
                if isinstance(step, Mapping) and step.get("agent_id") == agent_id:
                    linked.extend(step.get("skills") or [])
            if team.get("orchestrator_id") == agent_id:
                linked.extend(team.get("coordinator_skills") or [])
                linked.extend(team.get("synthesis_skills") or [])
            if skill_id in linked:
                dependencies.append(
                    {"kind": "team", "id": str(team.get("id") or ""), "detail": "workflow skill"}
                )
        inventory, _ = self._inventory(self._profile(agent_id), 200)
        for candidate in inventory:
            if skill_id in (candidate.get("related_skills") or []):
                dependencies.append(
                    {
                        "kind": "skill",
                        "id": str(candidate["skill_id"]),
                        "detail": "related skill dependency",
                    }
                )
        for blueprint in self.repository.list_agent_blueprints(self._profile(agent_id)):
            configured = {
                str(item.get("id") or "")
                for item in (blueprint.get("blueprint") or {}).get("skills") or []
                if isinstance(item, Mapping)
            }
            if skill_id in configured:
                dependencies.append(
                    {
                        "kind": "template",
                        "id": str(blueprint.get("id") or ""),
                        "detail": "agent blueprint skill",
                    }
                )
        return dependencies[:100]

    @staticmethod
    def _present(record: Mapping[str, Any]) -> dict[str, Any]:
        result = json.loads(json.dumps(record))
        result.pop("request_fingerprint", None)
        result.pop("idempotency_key_hash", None)
        result.pop("owner_subject", None)
        return result
