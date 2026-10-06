"""Default activation policy for skills bundled by Hermes."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

from .hermes_support import AgentAPIError

# Keep the default prompt index focused on broadly useful document, research,
# planning, and engineering workflows. Browser automation, computer use,
# memory, todo planning, and self-improvement remain powerful without entries
# here: Hermes exposes them as native availability-gated tools and injects
# their guidance directly into the system prompt. Every other bundled skill
# remains installed and can be enabled from the Skills page. User-installed
# skills are never changed by this policy.
DEFAULT_ENABLED_BUNDLED_SKILLS = frozenset(
    {
        "docx",
        "grounded-citations",
        "ocr-and-documents",
        "pdf",
        "plan",
        "powerpoint",
        "requesting-code-review",
        "systematic-debugging",
        "test-driven-development",
        "xlsx",
    }
)


class DefaultSkillsMixin:
    @staticmethod
    def _seed_bundled_skills(profile_dir: Path) -> None:
        """Seed a new profile through Hermes without changing process globals."""
        install_dir = str(os.environ.get("HERMES_INSTALL_DIR") or "").strip()
        if install_dir:
            installation = Path(install_dir).resolve()
        else:
            installation = Path(__file__).resolve().parents[2] / ".tools" / "hermes-agent"
            if not (installation / "tools" / "skills_sync.py").is_file():
                return
        env = os.environ.copy()
        env["HERMES_HOME"] = str(profile_dir.resolve())
        try:
            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from tools.skills_sync import sync_skills; sync_skills(quiet=True)",
                ],
                cwd=installation,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise AgentAPIError(
                "Default skills could not be initialized",
                code="default_skills_initialization_failed",
                status=500,
            ) from exc

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
        if managed.get("default_skills_initialized") is True:
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
        disabled = {str(item).strip() for item in raw_disabled if str(item).strip()}
        # This is initialization, not a migration: choose the current defaults
        # for bundled skills while preserving unrelated user-installed skills.
        disabled.difference_update(bundled)
        disabled.update(bundled - DEFAULT_ENABLED_BUNDLED_SKILLS)
        skills["disabled"] = sorted(disabled)
        managed["default_skills_initialized"] = True

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
        target = profile_dir / "snapshots" / "config" / f"{time.time_ns()}-{digest[:12]}.yaml"
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
