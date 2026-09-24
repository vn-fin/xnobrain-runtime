"""Exercise Runtime discovery against the installed pinned engine readers."""

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from xnobrain.integrations.hermes import AgentManager
from xnobrain.integrations.profile_inventory import list_profile_inventory


class ProfileInventoryTests(unittest.TestCase):
    def test_sibling_profiles_categories_and_restart(self):
        with TemporaryDirectory() as temp:
            base = Path(temp) / "agent"
            root = base / "big-brother"
            for profile in (root, base / "analyst", base / "writer"):
                (profile / "workspace").mkdir(parents=True)
                (profile / "config.yaml").write_text("model: test-model\n")
                skill = profile / "skills/finance/hpg-analysis/SKILL.md"
                skill.parent.mkdir(parents=True)
                skill.write_text("---\nname: hpg-analysis\ndescription: Test\n---\n")
            (base / "outside-link").symlink_to(root, target_is_directory=True)
            for _ in range(2):
                manager = AgentManager(root_profile=root, profiles_root=base)
                registry = {
                    item["name"]: item for item in manager.sync_profiles_registry()["profiles"]
                }
                profiles = list_profile_inventory(manager, registry)
                self.assertEqual([p.name for p in profiles], ["default", "analyst", "writer"])
                self.assertEqual(
                    [p.path for p in profiles], [root, base / "analyst", base / "writer"]
                )
                self.assertTrue(profiles[0].is_default)
                self.assertTrue(all(p.skill_count >= 1 for p in profiles))
                self.assertFalse((root / "profiles").exists())

    def test_categorized_skill_edit_and_memory_are_profile_local(self):
        with TemporaryDirectory() as temp:
            base = Path(temp) / "agent"
            root = base / "big-brother"
            for profile in (root, base / "analyst", base / "writer"):
                (profile / "workspace").mkdir(parents=True)
                (profile / "config.yaml").write_text("model: test-model\n")
            manager = AgentManager(root_profile=root, profiles_root=base)
            body = {
                "skill_id": "hpg-analysis",
                "category": "finance",
                "content": "---\nname: hpg-analysis\ndescription: Original\n---\nTest",
            }
            asyncio.run(manager.install_skill("analyst", body))
            skill = base / "analyst/skills/finance/hpg-analysis/SKILL.md"
            self.assertEqual(skill.read_text(), body["content"])
            manager.set_skill_enabled("analyst", "hpg-analysis", {"description": "Edited"})
            self.assertIn("Edited", skill.read_text())
            self.assertFalse((root / "skills/finance/hpg-analysis").exists())
            self.assertFalse((base / "writer/skills/finance/hpg-analysis").exists())
            manager.write_memory("analyst", {"memory": "Synthetic note"})
            manager = AgentManager(root_profile=root, profiles_root=base)
            self.assertEqual(manager.read_memory("analyst")["memory"], "Synthetic note")
            self.assertNotEqual(manager.read_memory("writer")["memory"], "Synthetic note")

    def test_symlink_profile_cannot_be_opened(self):
        with TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "big-brother"
            manager = AgentManager(root_profile=root, profiles_root=base)
            (base / "analyst").symlink_to(root, target_is_directory=True)
            from xnobrain.integrations import AgentAPIError

            with self.assertRaises(AgentAPIError):
                manager.profile_path("analyst")
