"""New profiles receive bundled skills without inheriting another profile's state."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import yaml

from xnobrain.integrations.hermes import AgentAPIError, AgentManager


class AgentDefaultSkillsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        installation = self.root / "engine"
        tools = installation / "tools"
        tools.mkdir(parents=True)
        (tools / "__init__.py").touch()
        (tools / "skills_sync.py").write_text(
            "import os\n"
            "from pathlib import Path\n"
            "def sync_skills(quiet=False):\n"
            "    profile = Path(os.environ['HERMES_HOME'])\n"
            "    if (profile / '.no-bundled-skills').exists():\n"
            "        return\n"
            "    skills = profile / 'skills'\n"
            "    for name in ('pdf', 'niche-example'):\n"
            "        target = skills / name / 'SKILL.md'\n"
            "        if not target.exists():\n"
            "            target.parent.mkdir(parents=True, exist_ok=True)\n"
            "            target.write_text('---\\nname: ' + name + '\\n---\\nBundled')\n"
            "    (skills / '.bundled_manifest').write_text('pdf:hash\\nniche-example:hash\\n')\n",
            encoding="utf-8",
        )
        environment = patch.dict(os.environ, {"HERMES_INSTALL_DIR": str(installation)})
        environment.start()
        self.addCleanup(environment.stop)
        self.profile = self.root / "big-brother"
        self.profile.mkdir()
        (self.profile / "config.yaml").write_text("{}\n", encoding="utf-8")
        self.template = self.root / "template"
        self.template.mkdir()
        self.manager = AgentManager(
            root_profile=self.profile,
            profiles_root=self.root / "profiles",
            legacy_agents_root=self.root / "legacy",
            profile_template=self.template,
        )

    def test_new_agent_has_bundled_catalog_and_default_activation(self):
        _, status = self.manager.create_agent({"name": "researcher"})
        profile = self.root / "profiles" / "researcher"
        self.assertEqual(status, 201)
        self.assertEqual(
            {skill["skill_id"] for skill in self.manager.list_skills("researcher")["skills"]},
            {"pdf", "niche-example"},
        )
        config = yaml.safe_load((profile / "config.yaml").read_text())
        self.assertNotIn("pdf", config["skills"]["disabled"])
        self.assertIn("niche-example", config["skills"]["disabled"])
        self.assertFalse((self.profile / "skills" / "pdf").exists())

    def test_root_skill_with_same_name_is_preserved(self):
        skill = self.profile / "skills" / "pdf" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: pdf\n---\nUser customization", encoding="utf-8")
        self.manager.create_agent({"name": "custom"})
        self.assertEqual(
            (self.root / "profiles" / "custom" / "skills" / "pdf" / "SKILL.md").read_text(),
            skill.read_text(),
        )

    def test_native_installation_is_found_without_environment_override(self):
        runtime = self.root / "native-runtime"
        (runtime / ".tools").mkdir(parents=True)
        (runtime / ".tools" / "hermes-agent").symlink_to(self.root / "engine")
        module = runtime / "xnobrain" / "integrations" / "default_skills.py"
        with (
            patch.dict(os.environ, {"HERMES_INSTALL_DIR": ""}),
            patch("xnobrain.integrations.default_skills.__file__", str(module)),
        ):
            self.manager.create_agent({"name": "native"})
        self.assertTrue(
            (self.root / "profiles" / "native" / "skills" / "pdf" / "SKILL.md").is_file()
        )

    def test_idempotent_create_does_not_reseed_or_reset_choices(self):
        self.manager.create_agent({"name": "existing"})
        profile = self.root / "profiles" / "existing"
        skill = profile / "skills" / "pdf" / "SKILL.md"
        skill.unlink()
        with patch.object(self.manager, "_seed_bundled_skills") as seed:
            _, status = self.manager.create_agent({"name": "existing", "idempotent": True})
        self.assertEqual(status, 200)
        seed.assert_not_called()
        self.assertFalse(skill.exists())

    def test_failed_seed_fails_creation_without_leftover_profile(self):
        with patch.dict(os.environ, {"HERMES_INSTALL_DIR": str(self.root / "missing")}):
            with self.assertRaises(AgentAPIError) as raised:
                self.manager.create_agent({"name": "failed"})
        self.assertEqual(raised.exception.code, "default_skills_initialization_failed")
        self.assertFalse((self.root / "profiles" / "failed").exists())

    def test_explicit_no_bundled_marker_is_respected(self):
        profile = self.root / "isolated"
        profile.mkdir()
        (profile / ".no-bundled-skills").touch()
        self.manager._seed_bundled_skills(profile)
        self.assertFalse((profile / "skills").exists())
