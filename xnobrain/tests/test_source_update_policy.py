"""Keep the source-update compatibility policy honest about this repository."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "deploy" / "source-update-policy.json"


class SourceUpdatePolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = json.loads(POLICY.read_text(encoding="utf-8"))

    def test_policy_shape_matches_the_reviewed_adapter(self):
        self.assertEqual(self.policy["schema_version"], 1)
        self.assertEqual(self.policy["adapter"], "tree_swap_v1")
        self.assertEqual(self.policy["preserved_paths"], [".tools"])
        self.assertEqual(self.policy["mode_only_paths"], ["runtime/agent-cli.sh"])
        self.assertGreaterEqual(self.policy["data_schema"], 1)

    def test_every_dependency_path_exists_and_is_sorted(self):
        paths = self.policy["dependency_paths"]
        self.assertEqual(paths, sorted(set(paths)))
        for path in paths:
            with self.subTest(path=path):
                target = ROOT / path.rstrip("/")
                self.assertTrue(target.exists(), path)
                self.assertEqual(path.endswith("/"), target.is_dir(), path)

    def test_install_time_inputs_are_dependency_paths(self):
        # These are consumed only when an image is built; a change to any of
        # them must force image replacement instead of a source-only update.
        for path in ("requirements.txt", "uv.lock", "scripts/install-linux.sh"):
            self.assertIn(path, self.policy["dependency_paths"])

    def test_data_schema_matches_runtime_default(self):
        source = (ROOT / "xnobrain" / "services" / "runtime_updates.py").read_text("utf-8")
        self.assertIn('os.getenv("RUNTIME_DATA_SCHEMA", "1")', source)
        self.assertEqual(self.policy["data_schema"], 1)


if __name__ == "__main__":
    unittest.main()
