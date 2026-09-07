"""Contracts for explicit-only XNOBrain workflow skills."""

import unittest
from pathlib import Path

import yaml


class WorkflowSkillAssetTests(unittest.TestCase):
    def test_agent_maker_and_optimizer_are_packaged_and_explicit_only(self):
        root = Path(__file__).resolve().parents[1] / "assets" / "skills"
        for skill_id in ("agent-maker", "skill-optimizer"):
            skill = root / skill_id / "SKILL.md"
            metadata = root / skill_id / "agents" / "openai.yaml"
            self.assertTrue(skill.is_file(), skill)
            self.assertTrue(metadata.is_file(), metadata)
            self.assertEqual(
                yaml.safe_load(metadata.read_text(encoding="utf-8"))["policy"][
                    "allow_implicit_invocation"
                ],
                False,
            )
            self.assertIn(f"name: {skill_id}", skill.read_text(encoding="utf-8"))
