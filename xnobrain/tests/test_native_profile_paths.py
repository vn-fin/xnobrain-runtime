"""CLI layout enforcement with isolated filesystem fixtures."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class NativeProfilePathsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.project = self.base / "installation"
        runtime = self.project / "runtime"
        runtime.mkdir(parents=True)
        for name in ("agent-cli.sh", "link-native-profiles.sh"):
            shutil.copy2(ROOT / "runtime" / name, runtime / name)
        self.root = self.base / "data" / "root"
        self.profiles = self.base / "data" / "profiles"
        self.root.mkdir(parents=True)
        self.profiles.mkdir()
        self.agent = self.base / "agent"
        self.agent.symlink_to(runtime / "agent-cli.sh")
        self.hermes = self.project / ".tools/hermes-agent/venv/bin/hermes"
        self.hermes.parent.mkdir(parents=True)
        self.hermes.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "print(json.dumps({'home': os.environ.get('HERMES_HOME'), "
            "'args': sys.argv[1:]}))\n",
            encoding="utf-8",
        )
        self.hermes.chmod(0o755)
        self.environment = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.base / "home"),
            "RUNTIME_HERMES_HOME": str(self.root),
            "RUNTIME_HERMES_PROFILES_ROOT": str(self.profiles),
            "HERMES_HOME": str(self.root),
        }

    def run_agent(self, *arguments):
        return subprocess.run(
            ["bash", str(self.agent), *arguments],
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def test_fresh_and_empty_cli_root_link_to_runtime(self):
        (self.root / "profiles").mkdir()
        for _ in range(2):
            result = self.run_agent("profile", "create", "math")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((self.root / "profiles").resolve(), self.profiles)
            self.assertEqual(json.loads(result.stdout)["home"], str(self.root))

    def test_concurrent_first_invocations_share_one_link(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.run_agent("profile", "list"), range(6)))
        self.assertTrue(all(result.returncode == 0 for result in results))
        self.assertEqual((self.root / "profiles").resolve(), self.profiles)
        self.assertEqual(list(self.profiles.iterdir()), [])

    def test_conflicts_block_writes_but_preserve_data_and_reads(self):
        nested = self.root / "profiles" / "math"
        nested.mkdir(parents=True)
        (nested / "SOUL.md").write_text("existing", encoding="utf-8")
        for command in ("create", "import", "rename"):
            result = self.run_agent("-p", "default", "profile", command, "math")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertIn("migration required", result.stderr)
        self.assertEqual((nested / "SOUL.md").read_text(encoding="utf-8"), "existing")
        self.assertFalse((self.profiles / "math").exists())
        self.assertEqual(self.run_agent("profile", "list").returncode, 0)
        self.assertEqual(self.run_agent("profile", "create", "--help").returncode, 0)

    def test_wrong_symlink_is_not_replaced_or_followed_for_creation(self):
        outside = self.base / "outside"
        outside.mkdir()
        (self.root / "profiles").symlink_to(outside, target_is_directory=True)
        result = self.run_agent("profile", "create", "math")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.root / "profiles").resolve(), outside)
        self.assertEqual(list(outside.iterdir()), [])

    def test_occupied_file_and_nested_destination_fail_without_data_loss(self):
        native = self.root / "profiles"
        native.write_text("existing file", encoding="utf-8")
        self.assertNotEqual(self.run_agent("profile", "create", "math").returncode, 0)
        self.assertEqual(native.read_text(encoding="utf-8"), "existing file")
        native.unlink()
        self.environment["RUNTIME_HERMES_PROFILES_ROOT"] = str(native / "nested")
        self.assertNotEqual(self.run_agent("profile", "create", "math").returncode, 0)
        self.assertFalse(native.is_symlink())

    def test_named_profile_scope_and_explicit_override_are_preserved(self):
        selected = self.profiles / "analyst"
        selected.mkdir()
        self.environment["HERMES_HOME"] = str(selected)
        result = self.run_agent("profile", "list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["args"], ["-p", "analyst", "profile", "list"])
        result = self.run_agent("-p", "writer", "profile", "list")
        self.assertEqual(json.loads(result.stdout)["args"], ["-p", "writer", "profile", "list"])

    def test_native_install_and_startup_use_shared_wrapper_and_linker(self):
        installer = (ROOT / "scripts/install-linux.sh").read_text(encoding="utf-8")
        wrapper_link = (
            'ln -sfn "$project_dir/runtime/agent-cli.sh" "$npm_prefix/bin/agent"'
        )
        self.assertIn(wrapper_link, installer)
        self.assertLess(installer.index("npm install --global"), installer.index(wrapper_link))
        prepare = (ROOT / "scripts/prepare-service-data.sh").read_text(encoding="utf-8")
        self.assertLess(
            prepare.index("link-native-profiles.sh"), prepare.index("apply-profile-templates.sh")
        )

    def test_installed_cli_creates_profile_discoverable_by_runtime(self):
        engine = Path("/usr/local/lib/hermes-agent/venv/bin/hermes")
        if not engine.is_file():
            self.skipTest("installed development engine is unavailable")
        self.hermes.unlink()
        self.hermes.symlink_to(engine)
        result = self.run_agent("profile", "create", "math", "--no-skills", "--no-alias")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.profiles / "math" / "workspace").is_dir())
        self.assertEqual((self.root / "profiles" / "math").resolve(), self.profiles / "math")
        from xnobrain.integrations.hermes import AgentManager

        manager = AgentManager(root_profile=self.root, profiles_root=self.profiles)
        self.assertIn("math", manager.list_agent_names())
        self.assertEqual(manager.profile_path("math"), self.profiles / "math")
        self.environment["HERMES_HOME"] = str(self.profiles / "math")
        result = self.run_agent("profile", "create", "science", "--no-skills", "--no-alias")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.profiles / "science" / "workspace").is_dir())
        self.assertIn("science", manager.list_agent_names())
