"""Installer contract for the immutable new-agent profile template."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest


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
                "BRAIN4ALL_PROFILE_TEMPLATES_DIR": str(templates),
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
