"""Installer contract for the immutable new-agent profile template."""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml


class ProfileTemplateInstallerTests(unittest.TestCase):
    def test_installer_keeps_named_profiles_and_separates_big_brother_from_seed(self):
        repository = Path(__file__).resolve().parents[2]
        script = repository / "scripts" / "apply-profile-templates.sh"
        templates = repository / "runtime" / "profile-templates"
        required_skills = repository / "runtime" / "required-skills"

        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "hermes"
            named = root / "profiles" / "worker"
            named.mkdir(parents=True)
            (named / "SOUL.md").write_text("Worker-owned soul\n", encoding="utf-8")

            environment = {
                **os.environ,
                "XNOBRAIN_PROFILE_TEMPLATES_DIR": str(templates),
                "XNOBRAIN_REQUIRED_SKILLS_DIR": str(required_skills),
            }
            subprocess.run(
                ["bash", str(script), str(root), str(root / "profiles")],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )

            template = root / "profile-template"
            self.assertTrue((template / "config.yaml").is_file())
            self.assertEqual(
                (template / "SOUL.md").read_text(encoding="utf-8"),
                (templates / "SOUL.md").read_text(encoding="utf-8"),
            )
            self.assertIn("AI assistant", (template / "SOUL.md").read_text(encoding="utf-8"))
            self.assertIn(
                "## Source citations", (template / "AGENTS.md").read_text(encoding="utf-8")
            )
            agents = (template / "AGENTS.md").read_text(encoding="utf-8")
            self.assertNotIn("XNOBrain", agents)
            self.assertNotIn("Hermes", agents)
            self.assertNotIn("9router", agents.lower())
            template_config = yaml.safe_load((template / "config.yaml").read_text(encoding="utf-8"))
            self.assertEqual(template_config["approvals"]["mode"], "off")
            self.assertFalse(template_config["skills"]["write_approval"])
            self.assertFalse(template_config["memory"]["write_approval"])
            self.assertEqual(template_config["prompt_caching"]["cache_ttl"], "1h")
            self.assertEqual(template_config["compression"]["proactive_prune_tokens"], 48_000)
            self.assertEqual(
                (named / "SOUL.md").read_text(encoding="utf-8"),
                "Worker-owned soul\n",
            )
            for profile in (root, named, template):
                runtime_skill = profile / "skills" / "runtime-skill" / "SKILL.md"
                self.assertTrue(runtime_skill.is_file())
                self.assertIn("name: runtime-skill", runtime_skill.read_text(encoding="utf-8"))
            update_plugin = root / "plugins" / "xnobrain-runtime-updates"
            self.assertTrue((update_plugin / "plugin.yaml").is_file())
            self.assertTrue((update_plugin / "__init__.py").is_file())
            self.assertFalse((named / "plugins" / "xnobrain-runtime-updates").exists())
            for retired in ("hermes-agent", "codex", "claude-code", "opencode"):
                self.assertFalse((root / "skills" / retired).exists())
                self.assertFalse((named / "skills" / retired).exists())

            (root / "SOUL.md").write_text(
                "Big Brother changed this after install\n",
                encoding="utf-8",
            )
            self.assertNotEqual(
                (root / "SOUL.md").read_text(encoding="utf-8"),
                (template / "SOUL.md").read_text(encoding="utf-8"),
            )

            agents = (templates / "AGENTS.md").read_text(encoding="utf-8")
            hermes = (templates / "HERMES.md").read_text(encoding="utf-8")
            self.assertEqual((root / "workspace" / "AGENTS.md").read_text(encoding="utf-8"), agents)
            self.assertEqual(
                (named / "workspace" / "AGENTS.md").read_text(encoding="utf-8"), agents
            )
            self.assertFalse((root / "workspace" / "HERMES.md").exists())
            self.assertFalse((named / "workspace" / "HERMES.md").exists())
            self.assertEqual((root / "HERMES.md").read_text(encoding="utf-8"), hermes)
            self.assertEqual((named / "HERMES.md").read_text(encoding="utf-8"), hermes)
            self.assertEqual((template / "HERMES.md").read_text(encoding="utf-8"), hermes)
            (root / "workspace" / "AGENTS.md").write_text("Big Brother edit\n")
            (root / "AGENTS.md").write_text("Big Brother home edit\n")
            (root / "HERMES.md").write_text("Big Brother home edit\n")
            (root / "workspace" / "HERMES.md").write_text("Leftover overlay\n")
            (named / "workspace" / "AGENTS.md").write_text("Named edit\n")
            (named / "workspace" / "HERMES.md").write_text("Named leftover\n")
            (named / "HERMES.md").write_text("Named home edit\n")
            self.assertEqual((template / "AGENTS.md").read_text(encoding="utf-8"), agents)
            subprocess.run(
                ["bash", str(script), str(root), str(root / "profiles")],
                check=True,
                capture_output=True,
                env=environment,
            )
            self.assertEqual((root / "workspace" / "AGENTS.md").read_text(encoding="utf-8"), agents)
            self.assertFalse((root / "workspace" / "HERMES.md").exists())
            self.assertEqual((root / "HERMES.md").read_text(encoding="utf-8"), hermes)
            self.assertEqual((template / "AGENTS.md").read_text(encoding="utf-8"), agents)
            self.assertEqual((template / "HERMES.md").read_text(encoding="utf-8"), hermes)
            self.assertEqual(
                (named / "workspace" / "AGENTS.md").read_text(encoding="utf-8"), "Named edit\n"
            )
            self.assertFalse((named / "workspace" / "HERMES.md").exists())
            self.assertEqual((named / "HERMES.md").read_text(encoding="utf-8"), "Named home edit\n")
