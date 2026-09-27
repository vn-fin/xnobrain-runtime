import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from xnobrain.integrations import AgentAPIError
from xnobrain.services.skill_files import list_skill_files, skill_path


class SkillFilesTests(unittest.TestCase):
    def test_only_skill_tree_and_no_symlink_escape(self):
        with TemporaryDirectory() as temp:
            profile = Path(temp)
            agents = SimpleNamespace(profile_path=lambda _: profile)
            skill = profile / "skills/math/cubic/SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("test")
            (profile / "config.yaml").write_text("private")
            (profile / "skills/leak").symlink_to(profile, target_is_directory=True)
            self.assertEqual(
                [e["name"] for e in list_skill_files(agents, "a", "")["entries"]], ["math"]
            )
            self.assertEqual(skill_path(agents, "a", "math/cubic/SKILL.md", file=True), skill)
            for raw in ("../config.yaml", "/etc/passwd", "leak/config.yaml", "%2e%2e/config.yaml"):
                with self.subTest(raw=raw), self.assertRaises(AgentAPIError):
                    skill_path(agents, "a", raw, file=True)
