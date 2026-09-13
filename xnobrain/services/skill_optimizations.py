"""Bounded evaluate/review/apply/rollback lifecycle for profile-local skills."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, Mapping

import yaml

from ..defaults import BIG_BROTHER_AGENT_ID, BIG_BROTHER_SKILL_ID
from .base import ServiceError, iso

_MAX_DIFF_BYTES = 256 * 1024
_TOKEN = re.compile(r"[a-z0-9][a-z0-9_-]{2,}")


class SkillOptimizationService:
    """Own consent, deterministic evaluation, approval, and safe mutation."""

    def __init__(self, platform: Any):
        self.platform = platform
        self.repository = platform.repository
        self.agents = platform.agents
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    def _lock(self, agent_id: str) -> threading.RLock:
        with self._locks_guard:
            return self._locks.setdefault(agent_id, threading.RLock())

    def _profile(self, agent_id: str) -> Path:
        if agent_id == BIG_BROTHER_AGENT_ID:
            raise ServiceError(
                "protected system skills cannot be optimized",
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

    def _skill(self, profile: Path, skill_id: str) -> tuple[Path | None, bytes | None, bool]:
        if skill_id == BIG_BROTHER_SKILL_ID:
            raise ServiceError(
                "protected skill cannot be optimized", status=403, code="protected_skill"
            )
        path = self.agents._find_agent_skill(profile, skill_id)
        if path is None:
            return None, None, False
        root = (profile / "skills").resolve()
        resolved = path.resolve(strict=True)
        if root not in resolved.parents or path.is_symlink():
            raise ServiceError("unsafe skill path", status=409, code="unsafe_skill_path")
        relative = resolved.relative_to(root)
        if relative.parts[:1] != ("custom",):
            raise ServiceError(
                "packaged and shared skills require a separate reviewed release flow",
                status=403,
                code="protected_skill",
            )
        content = (resolved / "SKILL.md").read_bytes()
        disabled = self.agents._disabled_skills(self.agents._read_config(profile))
        return resolved, content, skill_id not in disabled

    @staticmethod
    def _content_digest(content: bytes | None) -> str | None:
        return "sha256:" + hashlib.sha256(content).hexdigest() if content is not None else None

    @staticmethod
    def _candidate_digest(patches: list[Mapping[str, Any]]) -> str:
        material = [
            {
                "skill_id": item["skill_id"],
                "baseline_digest": item.get("baseline_digest"),
                "candidate_digest": item["candidate_digest"],
                "enable": bool(item.get("enable")),
            }
            for item in patches
        ]
        return _digest(material)

    def _validate_candidate(self, skill_id: str, content: bytes) -> None:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ServiceError("candidate must be UTF-8", code="invalid_skill_candidate") from error
        if not text.startswith("---"):
            raise ServiceError("candidate frontmatter is required", code="invalid_skill_candidate")
        parts = text.split("---", 2)
        try:
            frontmatter = yaml.safe_load(parts[1]) if len(parts) == 3 else None
        except yaml.YAMLError as error:
            raise ServiceError(
                "candidate frontmatter is invalid", code="invalid_skill_candidate"
            ) from error
        if not isinstance(frontmatter, Mapping) or str(frontmatter.get("name") or "") != skill_id:
            raise ServiceError(
                "candidate frontmatter name must match skill_id",
                code="invalid_skill_candidate",
            )
        description = str(frontmatter.get("description") or "").strip()
        if not description or len(description) > 1_000:
            raise ServiceError(
                "candidate needs a bounded description",
                code="invalid_skill_candidate",
            )

    def _session_examples(
        self,
        agent_id: str,
        context_id: str,
        session_ids: list[str],
        start: float,
        end: float,
        maximum: int,
    ) -> dict[str, Any]:
        examples = []
        total = 0
        for session_id in session_ids:
            context = self.platform._stored_context(agent_id, session_id, backfill=False)
            if str(context.get("id") or "") != context_id:
                raise ServiceError(
                    "session is outside the authorized work context",
                    status=403,
                    code="skill_optimization_session_forbidden",
                )
            payload = self.agents.get_conversation(agent_id, session_id)
            messages = []
            for message in payload.get("messages") or []:
                timestamp = float(message.get("timestamp") or 0)
                if not start < timestamp <= end:
                    continue
                role = str(message.get("role") or "")
                if role not in {"user", "assistant"}:
                    continue
                content = str(message.get("content") or "")
                encoded = content.encode("utf-8")
                if total + len(encoded) > maximum:
                    raise ServiceError(
                        "selected session examples exceed max_example_bytes",
                        status=413,
                        code="skill_optimization_examples_too_large",
                    )
                total += len(encoded)
                messages.append({"role": role, "content": content, "timestamp": timestamp})
            examples.append({"session_id": session_id, "messages": messages})
        return {
            "schema_version": 1,
            "untrusted_evidence": True,
            "expires_after_days": 30,
            "total_bytes": total,
            "sessions": examples,
        }

    def create(
        self,
        agent_id: str,
        body: Mapping[str, Any],
        trusted: Any,
    ) -> dict[str, Any]:
        profile = self._profile(agent_id)
        context_id = self._authorize_context(
            str(body.get("work_context_id") or "personal"), trusted
        )
        patches = []
        candidates = {}
        with self._lock(agent_id):
            for requested in body["patches"]:
                skill_id = str(requested["skill_id"])
                current_path, current, enabled = self._skill(profile, skill_id)
                baseline_digest = self._content_digest(current)
                expected = requested.get("expected_baseline_digest")
                if expected != baseline_digest:
                    raise ServiceError(
                        "skill changed or baseline digest was not supplied",
                        status=409,
                        code="skill_optimization_baseline_conflict",
                    )
                candidate = str(requested["candidate_content"]).encode("utf-8")
                self._validate_candidate(skill_id, candidate)
                candidate_digest = self._content_digest(candidate)
                baseline_text = current.decode("utf-8") if current else ""
                diff = "".join(
                    difflib.unified_diff(
                        baseline_text.splitlines(keepends=True),
                        candidate.decode("utf-8").splitlines(keepends=True),
                        fromfile=f"{skill_id}/baseline",
                        tofile=f"{skill_id}/candidate",
                    )
                )
                if len(diff.encode("utf-8")) > _MAX_DIFF_BYTES:
                    raise ServiceError(
                        "candidate diff exceeds limit",
                        status=413,
                        code="skill_optimization_diff_too_large",
                    )
                patches.append(
                    {
                        "skill_id": skill_id,
                        "baseline_digest": baseline_digest,
                        "candidate_digest": candidate_digest,
                        "baseline_exists": current is not None,
                        "baseline_enabled": enabled if current is not None else None,
                        "relative_path": (
                            current_path.relative_to(profile / "skills").as_posix()
                            if current_path
                            else f"custom/{skill_id}"
                        ),
                        "enable": bool(requested.get("enable")),
                        "diff": diff,
                    }
                )
                candidates[skill_id] = candidate
            examples = self._session_examples(
                agent_id,
                context_id,
                list(body.get("session_ids") or []),
                float(body["range_from"]),
                float(body["range_to"]),
                int(body.get("max_example_bytes") or 0),
            )
            now = iso()
            operation_id = "sop_" + uuid.uuid4().hex
            record = {
                "schema_version": 1,
                "id": operation_id,
                "revision": 1,
                "agent_id": agent_id,
                "work_context_id": context_id,
                "range_from": float(body["range_from"]),
                "range_to": float(body["range_to"]),
                "session_ids": list(body.get("session_ids") or []),
                "example_count": len(examples["sessions"]),
                "example_bytes": examples["total_bytes"],
                "max_evaluation_cost_usd": float(body.get("max_evaluation_cost_usd") or 0),
                "status": "draft",
                "patches": patches,
                "candidate_digest": self._candidate_digest(patches),
                "evaluation": None,
                "approval": None,
                "apply": None,
                "rollback": None,
                "cancellation": None,
                "created_at": now,
                "updated_at": now,
            }
            self.repository.create_skill_optimization(
                agent_id,
                record,
                candidates=candidates,
                examples=examples,
            )
        return self._present(record)

    def get(self, agent_id: str, operation_id: str, trusted: Any) -> dict[str, Any]:
        self._profile(agent_id)
        record = self.repository.get_skill_optimization(agent_id, operation_id)
        record = self._recover_interrupted_apply(agent_id, operation_id, record)
        self._authorize_context(str(record["work_context_id"]), trusted)
        return self._present(record)

    @staticmethod
    def _check(record: Mapping[str, Any], body: Mapping[str, Any]) -> None:
        if int(record.get("revision") or 0) != int(body["expected_revision"]):
            raise ServiceError(
                "skill optimization revision conflict",
                status=409,
                code="skill_optimization_revision_conflict",
            )
        if body.get("candidate_digest") and body["candidate_digest"] != record.get(
            "candidate_digest"
        ):
            raise ServiceError(
                "skill optimization candidate digest mismatch",
                status=409,
                code="skill_optimization_candidate_conflict",
            )

    def evaluate(
        self,
        agent_id: str,
        operation_id: str,
        body: Mapping[str, Any],
        trusted: Any,
    ) -> dict[str, Any]:
        self._profile(agent_id)
        with self._lock(agent_id):
            stored = self.repository.get_skill_optimization(agent_id, operation_id)
            self._authorize_context(str(stored["work_context_id"]), trusted)
            self._check(stored, body)
            if stored["status"] in {"cancelled", "applied", "rolled_back"}:
                raise ServiceError(
                    "optimization cannot be evaluated in its current state",
                    status=409,
                    code="skill_optimization_not_evaluable",
                )
            if float(body.get("max_cost_usd") or 0) > float(
                stored.get("max_evaluation_cost_usd") or 0
            ):
                raise ServiceError(
                    "evaluation cost cap exceeds the optimization budget",
                    status=409,
                    code="skill_optimization_budget_conflict",
                )
            cases = list(body["cases"])
            trials = int(body.get("trial_count") or 1)
            patch_reports = []
            for patch in stored["patches"]:
                baseline = self._checkpointless_baseline(agent_id, patch)
                candidate = self.repository.read_skill_optimization_candidate(
                    agent_id, operation_id, patch["skill_id"]
                ).decode("utf-8")
                case_reports = []
                for case in cases:
                    expected = bool(case["expected_trigger"])
                    baseline_result = _triggered(baseline, str(case["prompt"]))
                    candidate_result = _triggered(candidate, str(case["prompt"]))
                    case_reports.append(
                        {
                            "case_id": case["case_id"],
                            "partition": case["partition"],
                            "expected_trigger": expected,
                            "baseline_passed": baseline_result == expected,
                            "candidate_passed": candidate_result == expected,
                            "trials": trials,
                        }
                    )
                patch_reports.append(
                    {
                        "skill_id": patch["skill_id"],
                        "baseline_kind": "skill" if patch["baseline_exists"] else "no_skill",
                        "cases": case_reports,
                        "baseline_passed": sum(item["baseline_passed"] for item in case_reports),
                        "candidate_passed": sum(item["candidate_passed"] for item in case_reports),
                        "held_out_candidate_passed": sum(
                            item["candidate_passed"]
                            for item in case_reports
                            if item["partition"] == "held_out"
                        ),
                    }
                )
            evaluation = {
                "evaluator": "deterministic-trigger-v1",
                "matched_settings": True,
                "trial_count": trials,
                "case_count": len(cases),
                "actual_cost_usd": 0.0,
                "cost_kind": "measured",
                "quality_claim": False,
                "patches": patch_reports,
                "completed_at": iso(),
            }
            evaluation["digest"] = _digest(evaluation)
            record = {
                **stored,
                "revision": int(stored["revision"]) + 1,
                "status": "evaluated",
                "evaluation": evaluation,
                "approval": None,
                "updated_at": iso(),
            }
            updated = self.repository.update_skill_optimization(
                agent_id,
                operation_id,
                int(stored["revision"]),
                record,
                expected_record=stored,
            )
        return self._present(updated)

    def _checkpointless_baseline(self, agent_id: str, patch: Mapping[str, Any]) -> str:
        if not patch["baseline_exists"]:
            return ""
        profile = self._profile(agent_id)
        path = profile / "skills" / str(patch["relative_path"]) / "SKILL.md"
        try:
            content = path.read_bytes()
        except OSError as error:
            raise ServiceError(
                "skill changed since optimization draft",
                status=409,
                code="skill_optimization_baseline_conflict",
            ) from error
        if self._content_digest(content) != patch["baseline_digest"]:
            raise ServiceError(
                "skill changed since optimization draft",
                status=409,
                code="skill_optimization_baseline_conflict",
            )
        return content.decode("utf-8")

    def approve(
        self,
        agent_id: str,
        operation_id: str,
        body: Mapping[str, Any],
        trusted: Any,
    ) -> dict[str, Any]:
        subject = str(getattr(trusted, "subject", "") or "").strip()
        if not subject:
            raise ServiceError(
                "verified human identity is required for optimization review",
                status=401,
                code="trusted_subject_required",
            )
        self._profile(agent_id)
        with self._lock(agent_id):
            stored = self.repository.get_skill_optimization(agent_id, operation_id)
            self._authorize_context(str(stored["work_context_id"]), trusted)
            self._check(stored, body)
            evaluation = stored.get("evaluation") or {}
            if stored["status"] != "evaluated" or body["evaluation_digest"] != evaluation.get(
                "digest"
            ):
                raise ServiceError(
                    "current evaluation must be reviewed",
                    status=409,
                    code="skill_optimization_evaluation_conflict",
                )
            approval = {
                "decision": body["decision"],
                "candidate_digest": stored["candidate_digest"],
                "evaluation_digest": evaluation["digest"],
                "reviewed_by": subject,
                "reviewed_at": iso(),
                "reason": body.get("reason"),
            }
            approval["digest"] = _digest(approval)
            record = {
                **stored,
                "revision": int(stored["revision"]) + 1,
                "status": "approved" if body["decision"] == "approve" else "rejected",
                "approval": approval,
                "updated_at": iso(),
            }
            updated = self.repository.update_skill_optimization(
                agent_id,
                operation_id,
                int(stored["revision"]),
                record,
                expected_record=stored,
            )
        return self._present(updated)

    def apply(
        self,
        agent_id: str,
        operation_id: str,
        body: Mapping[str, Any],
        trusted: Any,
    ) -> dict[str, Any]:
        subject = str(getattr(trusted, "subject", "") or "").strip()
        if not subject:
            raise ServiceError(
                "verified human identity is required to apply skills",
                status=401,
                code="trusted_subject_required",
            )
        profile = self._profile(agent_id)
        with self._lock(agent_id), self.agents._skill_sync_lock, self.repository._lock:
            stored = self.repository.get_skill_optimization(agent_id, operation_id)
            stored = self._recover_interrupted_apply(agent_id, operation_id, stored)
            self._authorize_context(str(stored["work_context_id"]), trusted)
            if (
                stored.get("status") == "applied"
                and (stored.get("apply") or {}).get("idempotency_key") == body["idempotency_key"]
            ):
                return self._present(stored)
            self._check(stored, body)
            approval = stored.get("approval") or {}
            if (
                stored["status"] != "approved"
                or approval.get("decision") != "approve"
                or body["approval_digest"] != approval.get("digest")
            ):
                raise ServiceError(
                    "current trusted approval is required",
                    status=409,
                    code="skill_optimization_approval_required",
                )
            for patch in stored["patches"]:
                current_path, content, enabled = self._skill(profile, patch["skill_id"])
                if self._content_digest(content) != patch["baseline_digest"]:
                    raise ServiceError(
                        "skill changed since optimization draft",
                        status=409,
                        code="skill_optimization_baseline_conflict",
                    )
                self.repository.write_skill_optimization_checkpoint(
                    agent_id,
                    operation_id,
                    patch["skill_id"],
                    content,
                    (
                        current_path.relative_to(profile / "skills").as_posix()
                        if current_path
                        else None
                    ),
                    enabled if content is not None else None,
                )
            applying = {
                **stored,
                "status": "applying",
                "apply": {
                    "idempotency_key": body["idempotency_key"],
                    "applied_by": subject,
                    "started_at": iso(),
                    "interrupted_recovery": False,
                },
                "updated_at": iso(),
            }
            self.repository.update_skill_optimization(
                agent_id,
                operation_id,
                int(stored["revision"]),
                applying,
                expected_record=stored,
            )
            try:
                self._atomic_apply_tree(agent_id, operation_id, applying)
                applied = [
                    {
                        "skill_id": patch["skill_id"],
                        "digest": patch["candidate_digest"],
                        "enabled": patch["enable"],
                    }
                    for patch in stored["patches"]
                ]
                record = {
                    **stored,
                    "revision": int(stored["revision"]) + 1,
                    "status": "applied",
                    "apply": {
                        "idempotency_key": body["idempotency_key"],
                        "applied_by": subject,
                        "applied_at": iso(),
                        "skills": applied,
                        "checkpoint_retained": True,
                    },
                    "updated_at": iso(),
                }
                updated = self.repository.update_skill_optimization(
                    agent_id,
                    operation_id,
                    int(stored["revision"]),
                    record,
                    expected_record=applying,
                )
            except Exception:
                self._restore_checkpoint_tree(agent_id, operation_id, applying)
                recovered = {
                    **stored,
                    "status": "approved",
                    "apply": None,
                    "updated_at": iso(),
                    "recovery": {
                        "recovered_at": iso(),
                        "reason": "failed_apply_restored_checkpoint",
                    },
                }
                self.repository.update_skill_optimization(
                    agent_id,
                    operation_id,
                    int(stored["revision"]),
                    recovered,
                    expected_record=applying,
                )
                raise
        return self._present(updated)

    def _atomic_apply_tree(
        self,
        agent_id: str,
        operation_id: str,
        record: Mapping[str, Any],
    ) -> None:
        profile = self._profile(agent_id)
        skills = profile / "skills"
        config_path = profile / "config.yaml"
        config = self.agents._read_config(profile)
        old_config = config_path.read_bytes() if config_path.is_file() else None
        staging = Path(tempfile.mkdtemp(prefix=".skills-optimization-", dir=profile))
        backup = profile / f".skills-before-{operation_id}"
        committed = False
        try:
            staged_skills = staging / "skills"
            if skills.is_dir():
                shutil.copytree(skills, staged_skills, symlinks=True)
            else:
                staged_skills.mkdir()
            disabled = self.agents._disabled_skills(config)
            for patch in record["patches"]:
                target = staged_skills / str(patch["relative_path"])
                if target.is_symlink() or staged_skills.resolve() not in target.resolve().parents:
                    raise ServiceError("unsafe candidate target", code="unsafe_skill_path")
                target.mkdir(parents=True, exist_ok=True)
                candidate = self.repository.read_skill_optimization_candidate(
                    agent_id, operation_id, patch["skill_id"]
                )
                self.repository.atomic_write(target / "SKILL.md", candidate)
                if patch["enable"]:
                    disabled.discard(patch["skill_id"])
                else:
                    disabled.add(patch["skill_id"])
            had_skills = skills.exists()
            if had_skills:
                os.replace(skills, backup)
            os.replace(staged_skills, skills)
            self.agents._write_disabled_skills(profile, config, disabled)
            committed = True
            if backup.exists():
                shutil.rmtree(backup)
            self.repository._sync_dir(profile)
        except Exception:
            if skills.exists():
                shutil.rmtree(skills)
            if backup.exists():
                os.replace(backup, skills)
            if old_config is None:
                config_path.unlink(missing_ok=True)
            else:
                self.repository.atomic_write(config_path, old_config)
            self.repository._sync_dir(profile)
            raise
        finally:
            if staging.exists():
                shutil.rmtree(staging)
            if committed and backup.exists():
                shutil.rmtree(backup)

    def _recover_interrupted_apply(
        self,
        agent_id: str,
        operation_id: str,
        record: Mapping[str, Any],
    ) -> dict[str, Any]:
        if record.get("status") not in {"applying", "rolling_back"}:
            return dict(record)
        with self._lock(agent_id), self.agents._skill_sync_lock, self.repository._lock:
            current = self.repository.get_skill_optimization(agent_id, operation_id)
            if current.get("status") not in {"applying", "rolling_back"}:
                return current
            interrupted_status = current["status"]
            self._restore_checkpoint_tree(agent_id, operation_id, current)
            if interrupted_status == "rolling_back":
                rollback = dict(current.get("rollback") or {})
                rollback["rolled_back_at"] = iso()
                recovered = {
                    **current,
                    "revision": int(current["revision"]) + 1,
                    "status": "rolled_back",
                    "rollback": rollback,
                    "updated_at": iso(),
                    "recovery": {
                        "recovered_at": iso(),
                        "reason": "interrupted_rollback_completed",
                    },
                }
            else:
                recovered = {
                    **current,
                    "status": "approved",
                    "apply": None,
                    "updated_at": iso(),
                    "recovery": {
                        "recovered_at": iso(),
                        "reason": "interrupted_apply_restored_checkpoint",
                    },
                }
            return self.repository.update_skill_optimization(
                agent_id,
                operation_id,
                int(current["revision"]),
                recovered,
                expected_record=current,
            )

    def rollback(
        self,
        agent_id: str,
        operation_id: str,
        body: Mapping[str, Any],
        trusted: Any,
    ) -> dict[str, Any]:
        subject = str(getattr(trusted, "subject", "") or "").strip()
        if not subject:
            raise ServiceError(
                "verified human identity is required to rollback skills",
                status=401,
                code="trusted_subject_required",
            )
        self._profile(agent_id)
        with self._lock(agent_id), self.agents._skill_sync_lock, self.repository._lock:
            stored = self.repository.get_skill_optimization(agent_id, operation_id)
            stored = self._recover_interrupted_apply(agent_id, operation_id, stored)
            self._authorize_context(str(stored["work_context_id"]), trusted)
            if (
                stored.get("status") == "rolled_back"
                and (stored.get("rollback") or {}).get("idempotency_key") == body["idempotency_key"]
            ):
                return self._present(stored)
            self._check(stored, body)
            if stored["status"] != "applied":
                raise ServiceError(
                    "only an applied optimization can be rolled back",
                    status=409,
                    code="skill_optimization_not_applied",
                )
            if body["expected_applied_digest"] != stored["candidate_digest"]:
                raise ServiceError(
                    "applied optimization digest mismatch",
                    status=409,
                    code="skill_optimization_rollback_conflict",
                )
            for patch in stored["patches"]:
                _, content, _ = self._skill(self._profile(agent_id), patch["skill_id"])
                if self._content_digest(content) != patch["candidate_digest"]:
                    raise ServiceError(
                        "applied skill changed before rollback",
                        status=409,
                        code="skill_optimization_rollback_conflict",
                    )
            rolling_back = {
                **stored,
                "status": "rolling_back",
                "rollback": {
                    "idempotency_key": body["idempotency_key"],
                    "rolled_back_by": subject,
                    "started_at": iso(),
                    "reason": body["reason"],
                },
                "updated_at": iso(),
            }
            self.repository.update_skill_optimization(
                agent_id,
                operation_id,
                int(stored["revision"]),
                rolling_back,
                expected_record=stored,
            )
            self._restore_checkpoint_tree(agent_id, operation_id, rolling_back)
            record = {
                **rolling_back,
                "revision": int(stored["revision"]) + 1,
                "status": "rolled_back",
                "rollback": {
                    **rolling_back["rollback"],
                    "rolled_back_at": iso(),
                },
                "updated_at": iso(),
            }
            updated = self.repository.update_skill_optimization(
                agent_id,
                operation_id,
                int(stored["revision"]),
                record,
                expected_record=rolling_back,
            )
        return self._present(updated)

    def _restore_checkpoint_tree(
        self,
        agent_id: str,
        operation_id: str,
        record: Mapping[str, Any],
    ) -> None:
        profile = self._profile(agent_id)
        skills = profile / "skills"
        config_path = profile / "config.yaml"
        config = self.agents._read_config(profile)
        old_config = config_path.read_bytes() if config_path.is_file() else None
        disabled = self.agents._disabled_skills(config)
        staging = Path(tempfile.mkdtemp(prefix=".skills-rollback-", dir=profile))
        backup = profile / f".skills-applied-{operation_id}"
        committed = False
        try:
            staged_skills = staging / "skills"
            if skills.is_dir():
                shutil.copytree(skills, staged_skills, symlinks=True)
            else:
                staged_skills.mkdir()
            for patch in record["patches"]:
                checkpoint = self.repository.read_skill_optimization_checkpoint(
                    agent_id, operation_id, patch["skill_id"]
                )
                current = self.agents._find_agent_skill(profile, patch["skill_id"])
                current_relative = current.relative_to(skills) if current is not None else None
                if current_relative is not None:
                    staged_current = staged_skills / current_relative
                    if staged_current.exists():
                        shutil.rmtree(staged_current)
                if checkpoint["existed"]:
                    target = staged_skills / str(checkpoint["relative_path"]) / "SKILL.md"
                    self.repository.atomic_write(target, checkpoint["content"])
                    if checkpoint["enabled"]:
                        disabled.discard(patch["skill_id"])
                    else:
                        disabled.add(patch["skill_id"])
                else:
                    disabled.discard(patch["skill_id"])
            had_skills = skills.exists()
            if had_skills:
                os.replace(skills, backup)
            os.replace(staged_skills, skills)
            self.agents._write_disabled_skills(profile, config, disabled)
            committed = True
            if backup.exists():
                shutil.rmtree(backup)
            self.repository._sync_dir(profile)
        except Exception:
            if skills.exists():
                shutil.rmtree(skills)
            if backup.exists():
                os.replace(backup, skills)
            if old_config is None:
                config_path.unlink(missing_ok=True)
            else:
                self.repository.atomic_write(config_path, old_config)
            self.repository._sync_dir(profile)
            raise
        finally:
            if staging.exists():
                shutil.rmtree(staging)
            if committed and backup.exists():
                shutil.rmtree(backup)

    def cancel(
        self,
        agent_id: str,
        operation_id: str,
        body: Mapping[str, Any],
        trusted: Any,
    ) -> dict[str, Any]:
        self._profile(agent_id)
        with self._lock(agent_id):
            stored = self.repository.get_skill_optimization(agent_id, operation_id)
            self._authorize_context(str(stored["work_context_id"]), trusted)
            self._check(stored, body)
            if stored["status"] in {"applied", "rolled_back"}:
                raise ServiceError(
                    "applied optimization cannot be cancelled",
                    status=409,
                    code="skill_optimization_not_cancellable",
                )
            record = {
                **stored,
                "revision": int(stored["revision"]) + 1,
                "status": "cancelled",
                "approval": None,
                "cancellation": {"cancelled_at": iso(), "reason": body.get("reason")},
                "updated_at": iso(),
            }
            updated = self.repository.update_skill_optimization(
                agent_id,
                operation_id,
                int(stored["revision"]),
                record,
                expected_record=stored,
            )
        return self._present(updated)

    @staticmethod
    def _present(record: Mapping[str, Any]) -> dict[str, Any]:
        # Raw session examples and candidate bodies are deliberately never returned.
        return json.loads(json.dumps(record))


def _digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _triggered(skill_content: str, prompt: str) -> bool:
    if not skill_content:
        return False
    description = ""
    if skill_content.startswith("---"):
        try:
            frontmatter = yaml.safe_load(skill_content.split("---", 2)[1]) or {}
            description = str(frontmatter.get("description") or "")
        except (IndexError, yaml.YAMLError):
            description = ""
    stop = {"and", "the", "for", "when", "with", "this", "that", "use", "skill"}
    triggers = {token for token in _TOKEN.findall(description.lower()) if token not in stop}
    words = set(_TOKEN.findall(prompt.lower()))
    return bool(triggers & words)
