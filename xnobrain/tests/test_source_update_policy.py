"""Keep the source-update compatibility policy honest about this repository."""

from __future__ import annotations

import json
import re
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

    def test_every_policy_path_exists_and_is_sorted(self):
        for key in ("dependency_paths", "install_paths"):
            paths = self.policy[key]
            self.assertEqual(paths, sorted(set(paths)), key)
            for path in paths:
                with self.subTest(key=key, path=path):
                    target = ROOT / path.rstrip("/")
                    self.assertTrue(target.exists(), path)
                    self.assertEqual(path.endswith("/"), target.is_dir(), path)

    def test_install_paths_never_overlap_dependency_paths(self):
        def covers(path, name):
            return name == path or path.endswith("/") and name.startswith(path)

        for install in self.policy["install_paths"]:
            for dependency in self.policy["dependency_paths"]:
                with self.subTest(install=install, dependency=dependency):
                    self.assertFalse(covers(install, dependency) or covers(dependency, install))

    def test_systemd_installer_inputs_are_install_paths(self):
        # A source update reruns this installer when an install path changes,
        # so every tree file it copies outside the tree must be one.
        installer = (ROOT / "scripts" / "install-systemd-services.sh").read_text("utf-8")
        units = re.search(r"for unit in \\\n(.*?); do", installer, re.S)
        self.assertIsNotNone(units)
        copied = [f"deploy/systemd/{unit}" for unit in units.group(1).split() if unit != "\\"]
        copied += re.findall(r'"\$install_root/([^"]+)" \\\n\s+/usr/local/libexec/', installer)
        self.assertIn("runtime/memory-reclaim-helper.py", copied)
        paths = self.policy["install_paths"]
        for name in copied:
            with self.subTest(name=name):
                self.assertTrue((ROOT / name).is_file(), name)
                self.assertTrue(
                    any(
                        name == path or path.endswith("/") and name.startswith(path)
                        for path in paths
                    ),
                    name,
                )
        self.assertIn("scripts/install-systemd-services.sh", self.policy["install_paths"])

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
