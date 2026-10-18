"""New profiles receive bundled skills without inheriting another profile's state."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import yaml

from xnobrain.integrations.config import GlobalConfigManager
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
        self.assertTrue(
            all(item["bundled"] for item in self.manager.list_skills("researcher")["skills"])
        )

    def test_pinned_auxiliary_models_lists_only_explicit_choices(self):
        self.manager.create_agent({"name": "auxiliary"})
        profile = self.root / "profiles" / "auxiliary"
        self.assertEqual(self.manager.pinned_auxiliary_models("auxiliary"), [])
        config = yaml.safe_load((profile / "config.yaml").read_text())
        config["auxiliary"] = {
            "vision": {"model": "cx/gpt-5"},
            "compression": {"model": "auto", "provider": ""},
            "web_extract": {"base_url": "https://example.test/v1"},
        }
        (profile / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        self.assertEqual(
            self.manager.pinned_auxiliary_models("auxiliary"), ["vision", "web_extract"]
        )

    def test_publication_inventory_distinguishes_defaults_from_custom_skills(self):
        self.manager.create_agent({"name": "publisher"})
        profile = self.root / "profiles" / "publisher"
        custom = profile / "skills" / "custom" / "my-report" / "SKILL.md"
        custom.parent.mkdir(parents=True)
        custom.write_text("---\nname: my-report\n---\nCustom report")
        # A default remains bundled after a local edit or rename in frontmatter.
        (profile / "skills" / "pdf" / "SKILL.md").write_text(
            "---\nname: renamed-pdf\n---\nEdited default"
        )
        inventory = {
            item["skill_id"]: item for item in self.manager.list_skills("publisher")["skills"]
        }
        self.assertTrue(inventory["renamed-pdf"]["bundled"])
        self.assertTrue(inventory["niche-example"]["bundled"])
        self.assertFalse(inventory["niche-example"]["enabled"])
        self.assertFalse(inventory["my-report"]["bundled"])

    def test_catalog_identifies_defaults_without_a_profile_manifest(self):
        self.manager.create_agent({"name": "legacy"})
        profile = self.root / "profiles" / "legacy"
        (profile / "skills" / ".bundled_manifest").unlink()
        catalog = self.root / "engine" / "skills" / "documents" / "pdf"
        catalog.mkdir(parents=True)
        (catalog / "SKILL.md").write_text("---\nname: pdf\n---\nBundled PDF")
        inventory = {
            item["skill_id"]: item for item in self.manager.list_skills("legacy")["skills"]
        }
        self.assertTrue(inventory["pdf"]["bundled"])

    def test_bundled_catalog_override_and_frontmatter_names(self):
        self.manager.create_agent({"name": "publisher"})
        catalog = self.root / "catalog"
        folder = catalog / "different-folder"
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text("---\nname: pdf\n---\nBundled PDF")
        profile = self.root / "profiles" / "publisher"
        (profile / "skills" / ".bundled_manifest").unlink()
        with patch.dict(os.environ, {"HERMES_BUNDLED_SKILLS": str(catalog)}):
            inventory = {
                item["skill_id"]: item for item in self.manager.list_skills("publisher")["skills"]
            }
        self.assertTrue(inventory["pdf"]["bundled"])

    def test_common_profile_custom_skills_are_not_automatically_defaults(self):
        self.manager._seed_bundled_skills(self.profile)
        custom = self.profile / "skills" / "my-report" / "SKILL.md"
        custom.parent.mkdir()
        custom.write_text("---\nname: my-report\n---\nShared custom report")
        inventory = {
            item["skill_id"]: item
            for item in GlobalConfigManager(root_profile=self.profile).list_skills()["skills"]
        }
        self.assertTrue(inventory["pdf"]["bundled"])
        self.assertFalse(inventory["my-report"]["bundled"])

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
