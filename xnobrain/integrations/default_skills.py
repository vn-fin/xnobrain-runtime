"""Default activation policy for skills bundled by Hermes."""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path


DEFAULT_SKILLS_POLICY_REVISION = 1

# Keep the default prompt index focused on capabilities that are broadly useful
# in XNOBrain. Every other bundled skill remains installed and can be enabled
# from the Skills page. User-installed skills are never changed by this policy.
DEFAULT_ENABLED_BUNDLED_SKILLS = frozenset({
    "docx",
    "grounded-citations",
    "ocr-and-documents",
    "pdf",
    "powerpoint",
    "xlsx",
})


class DefaultSkillsMixin:
    def _apply_default_skills_policy(self, profile_dir: Path) -> bool:
        """Disable niche bundled skills once without overriding later choices."""
        manifest = profile_dir / "skills" / ".bundled_manifest"
        if not manifest.is_file():
            return False

        config = self._read_config(profile_dir)
        managed = config.get("xnobrain")
        if not isinstance(managed, dict):
            managed = {}
            config["xnobrain"] = managed
        try:
            revision = int(managed.get("default_skills_revision") or 0)
        except (TypeError, ValueError):
            revision = 0
        if revision >= DEFAULT_SKILLS_POLICY_REVISION:
            return False

        bundled = self._bundled_skill_names(manifest)
        if not bundled:
            return False
        skills = config.get("skills")
        if not isinstance(skills, dict):
            skills = {}
            config["skills"] = skills
        raw_disabled = skills.get("disabled") or []
        if isinstance(raw_disabled, str):
            raw_disabled = [item.strip() for item in raw_disabled.split(",")]
        disabled = {
            str(item).strip()
            for item in raw_disabled
            if str(item).strip()
        }
        disabled.update(bundled - DEFAULT_ENABLED_BUNDLED_SKILLS)
        skills["disabled"] = sorted(disabled)
        managed["default_skills_revision"] = DEFAULT_SKILLS_POLICY_REVISION

        self._snapshot_policy_config(profile_dir)
        self._write_yaml_atomic(profile_dir / "config.yaml", config)
        return True

    @staticmethod
    def _bundled_skill_names(manifest: Path) -> set[str]:
        try:
            lines = manifest.read_text(encoding="utf-8").splitlines()
        except OSError:
            return set()
        return {
            line.partition(":")[0].strip()
            for line in lines
            if line.strip() and line.partition(":")[0].strip()
        }

    @staticmethod
    def _snapshot_policy_config(profile_dir: Path) -> None:
        source = profile_dir / "config.yaml"
        if not source.is_file():
            return
        payload = source.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        target = (
            profile_dir
            / "snapshots"
            / "config"
            / f"{time.time_ns()}-{digest[:12]}.yaml"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o440)
        try:
            with os.fdopen(descriptor, "wb") as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
        except Exception:
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            raise
