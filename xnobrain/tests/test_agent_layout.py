import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from xnobrain.agent_layout import configure_layout, resolve_layout
from xnobrain.agent_layout_audit import inventory


class AgentLayoutTests(unittest.TestCase):
    def test_canonical_environment(self):
        with TemporaryDirectory() as temp:
            base = str(Path(temp) / "agent")
            env = {"RUNTIME_AGENT_DATA_ROOT": base}
            layout = configure_layout(env)
            self.assertTrue(layout.canonical)
            self.assertEqual(env["HERMES_HOME"], base + "/big-brother")
            self.assertEqual(env["HERMES_PROFILES_ROOT"], base)
            self.assertFalse(Path(base).exists())

    def test_legacy_preserved(self):
        env = {"HERMES_HOME": "/opt/data/home/.hermes"}
        self.assertFalse(configure_layout(env).canonical)
        self.assertEqual(env["HERMES_HOME"], "/opt/data/home/.hermes")

    def test_conflict_invalid_and_source_paths(self):
        for value in ("relative", "/opt/data/../agent", "/workspace/data", "/opt", "/"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                resolve_layout({"RUNTIME_AGENT_DATA_ROOT": value})
        with self.assertRaises(ValueError):
            resolve_layout({"RUNTIME_AGENT_DATA_ROOT": "/opt/data/agent", "HERMES_HOME": "/old"})

    def test_symlink_root_rejected(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "link").symlink_to(root, target_is_directory=True)
            with self.assertRaises(ValueError):
                resolve_layout({"RUNTIME_AGENT_DATA_ROOT": str(root / "link" / "agent")})

    def test_conflicting_lower_precedence_alias_rejected(self):
        with TemporaryDirectory() as temp:
            base = str(Path(temp) / "agent")
            with self.assertRaises(ValueError):
                resolve_layout(
                    {
                        "RUNTIME_AGENT_DATA_ROOT": base,
                        "RUNTIME_HERMES_HOME": base + "/big-brother",
                        "HERMES_HOME": "/old",
                    }
                )

    def test_root_profile_symlink_rejected(self):
        with TemporaryDirectory() as temp:
            base = Path(temp)
            (base / "big-brother").symlink_to(base, target_is_directory=True)
            with self.assertRaises(ValueError):
                resolve_layout({"RUNTIME_AGENT_DATA_ROOT": str(base)})

    def test_dry_run_rejects_file_roots_and_counts_directory_collision(self):
        with TemporaryDirectory() as temp:
            base = Path(temp)
            source, destination = base / "source", base / "destination"
            source.mkdir()
            destination.mkdir()
            (source / "skills").mkdir()
            (destination / "skills").write_text("collision")
            self.assertEqual(inventory(source, destination)["conflicts"], 1)
            with self.assertRaises(ValueError):
                inventory(destination / "skills", source)

    def test_dry_run_counts_conflicts_without_mutation(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source, destination = root / "old", root / "agent"
            source.mkdir()
            destination.mkdir()
            (source / "SKILL.md").write_text("private content")
            (destination / "SKILL.md").write_text("different")
            result = inventory(source, destination)
            self.assertEqual(result["conflicts"], 1)
            self.assertFalse(result["activation_performed"])
            self.assertNotIn("private content", str(result))
            self.assertEqual((destination / "SKILL.md").read_text(), "different")
