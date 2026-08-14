"""Installer contract for the immutable new-agent profile template."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

import yaml


class ProfileTemplateInstallerTests(unittest.TestCase):
    def test_installer_keeps_named_profiles_and_separates_big_brother_from_seed(self):
        repository = Path(__file__).resolve().parents[2]
        script = repository / "scripts" / "apply-profile-templates.sh"
        templates = repository / "runtime" / "profile-templates"

        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "hermes"
            named = root / "profiles" / "worker"
            named.mkdir(parents=True)
            (named / "SOUL.md").write_text("Worker-owned soul\n", encoding="utf-8")

            environment = {
                **os.environ,
                "XNOBRAIN_PROFILE_TEMPLATES_DIR": str(templates),
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
            self.assertIn("You are an AI agent", (template / "SOUL.md").read_text(encoding="utf-8"))
            self.assertIn("## Source citations", (template / "AGENTS.md").read_text(encoding="utf-8"))
            agents = (template / "AGENTS.md").read_text(encoding="utf-8")
            self.assertNotIn("XNOBrain", agents)
            self.assertNotIn("Hermes", agents)
            self.assertNotIn("9router", agents.lower())
            template_config = yaml.safe_load(
                (template / "config.yaml").read_text(encoding="utf-8")
            )
            self.assertEqual(template_config["approvals"]["mode"], "off")
            self.assertFalse(template_config["skills"]["write_approval"])
            self.assertFalse(template_config["memory"]["write_approval"])
            self.assertEqual(template_config["prompt_caching"]["cache_ttl"], "1h")
            self.assertEqual(template_config["compression"]["proactive_prune_tokens"], 48_000)
            self.assertEqual(
                (named / "SOUL.md").read_text(encoding="utf-8"),
                "Worker-owned soul\n",
            )

            (root / "SOUL.md").write_text(
                "Big Brother changed this after install\n",
                encoding="utf-8",
            )
            self.assertNotEqual(
                (root / "SOUL.md").read_text(encoding="utf-8"),
                (template / "SOUL.md").read_text(encoding="utf-8"),
            )
