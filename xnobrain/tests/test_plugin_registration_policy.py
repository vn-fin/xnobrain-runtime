"""Registration policy seam tests for cached modules, trust and launchers."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import yaml

from xnobrain.integrations import plugin_registration_policy
from xnobrain.integrations.extension_policy import save_state, tree_digest

ROOT = Path(__file__).resolve().parents[2]


def _module_with_register(register):
    module = ModuleType("xno_policy_shared")
    module.register = register
    return module


class PluginRegistrationPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def _managers(root):
        from hermes_cli.plugins import PluginManager

        return (
            PluginManager(scope_key=str(root / "a")),
            PluginManager(scope_key=str(root / "b")),
        )

    def test_cached_module_loaded_by_two_managers_records_each_manager(self):
        from hermes_cli.plugins import PluginContext, PluginManifest

        def register(ctx):
            ctx.register_middleware("pre_tool_call", lambda *args, **kwargs: None)

        module = _module_with_register(register)
        manifest = PluginManifest(name="sample", source="user")
        first, second = self._managers(self.root)
        first_facade = plugin_registration_policy._guard_module(module, manifest, first)
        second_facade = plugin_registration_policy._guard_module(module, manifest, second)

        self.assertIs(module.register, register)
        with self.assertRaises(Exception):
            first_facade.register(PluginContext(manifest, first))
        self.assertEqual(
            getattr(first, plugin_registration_policy._VIOLATIONS_ATTR),
            [("sample", "register_middleware")],
        )
        self.assertEqual(getattr(second, plugin_registration_policy._VIOLATIONS_ATTR, []), [])
        with self.assertRaises(Exception):
            second_facade.register(PluginContext(manifest, second))
        self.assertEqual(
            getattr(second, plugin_registration_policy._VIOLATIONS_ATTR),
            [("sample", "register_middleware")],
        )
        self.assertEqual(
            getattr(first, plugin_registration_policy._VIOLATIONS_ATTR),
            [("sample", "register_middleware")],
        )

    def test_only_bundled_or_byte_verified_required_is_trusted(self):
        from hermes_cli.plugins import PluginManifest

        module = _module_with_register(lambda ctx: None)
        manager, _ = self._managers(self.root)
        for source in ("entrypoint", "project", "user", "", "unknown"):
            with self.subTest(source=source):
                manifest = PluginManifest(name="sample", source=source)
                self.assertIsNot(
                    plugin_registration_policy._guard_module(module, manifest, manager),
                    module,
                )
        bundled = PluginManifest(name="sample", source="bundled")
        self.assertIs(
            plugin_registration_policy._guard_module(module, bundled, manager),
            module,
        )

    def test_required_name_with_different_bytes_is_not_trusted(self):
        from hermes_cli.plugins import PluginManager, PluginManifest

        packaged = self.root / "packaged" / "xnobrain-runtime-updates"
        packaged.mkdir(parents=True)
        (packaged / "plugin.yaml").write_text("name: xnobrain-runtime-updates\nversion: 1\n")
        profile = self.root / "profile"
        installed = profile / "plugins" / "xnobrain-runtime-updates"
        installed.mkdir(parents=True)
        (installed / "plugin.yaml").write_text("name: xnobrain-runtime-updates\nversion: 2\n")
        manager = PluginManager(scope_key=str(profile))
        manifest = PluginManifest(name="xnobrain-runtime-updates", source="user")
        module = _module_with_register(lambda ctx: None)
        with patch.dict(os.environ, {"XNOBRAIN_REQUIRED_PLUGINS_DIR": str(packaged.parent)}):
            self.assertFalse(plugin_registration_policy._is_trusted(manifest, manager))
            self.assertIsNot(
                plugin_registration_policy._guard_module(module, manifest, manager),
                module,
            )

    def _approved_profile(self, register_source):
        profile = self.root / "profile"
        (profile / "logs").mkdir(parents=True, exist_ok=True)
        plugin = profile / "plugins" / "sample"
        plugin.mkdir(parents=True)
        (plugin / "plugin.yaml").write_text("name: sample\nversion: 1.0.0\n")
        (plugin / "__init__.py").write_text(register_source)
        (profile / "config.yaml").write_text(yaml.safe_dump({"plugins": {"enabled": ["sample"]}}))
        digest = tree_digest(plugin)
        save_state(
            profile,
            {
                "plugins": {
                    "sample": {
                        "artifact_digest": digest,
                        "scan": {"status": "COMPLETED", "verdict": "safe"},
                        "approval": {"approved": True, "artifact_digest": digest},
                        "revision": 2,
                    }
                },
                "hooks": {},
                "mcp": {},
                "revision": 2,
            },
        )
        return profile

    def _approved_forbidden_profile(self):
        return self._approved_profile(
            "def register(ctx):\n"
            "    ctx.register_middleware('pre_tool_call', lambda *args, **kwargs: None)\n"
        )

    def _launcher(
        self,
        profile,
        *,
        result_path,
        register_log=None,
        profile_aware=False,
        hermes_home=None,
    ):
        bin_dir = self.root / "bin"
        bin_dir.mkdir(exist_ok=True)
        site = self.root / "site"
        site.mkdir(exist_ok=True)
        (self.root / "home").mkdir(exist_ok=True)
        (self.root / "data").mkdir(exist_ok=True)
        hermes = bin_dir / "hermes"
        hermes.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
        hermes.chmod(0o755)
        python = bin_dir / "python"
        python.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n', encoding="utf-8")
        python.chmod(0o755)
        launcher = (ROOT / "runtime" / "agent-cli.sh").read_text(encoding="utf-8")
        launcher = launcher.replace(
            "/usr/local/bin/xnobrain-link-native-profiles", "/usr/bin/true"
        ).replace("/usr/local/lib/hermes-agent/venv/bin/hermes", str(hermes))
        agent = bin_dir / "agent"
        agent.write_text(launcher, encoding="utf-8")
        agent.chmod(0o755)
        if profile_aware:
            sitecustomize = (
                "import json, os, sys\n"
                "import hermes_cli.main as native\n"
                "def _main():\n"
                "    from hermes_cli.plugins import get_plugin_manager\n"
                "    manager = get_plugin_manager()\n"
                "    result = {'argv': sys.argv[1:], 'home': os.environ.get('HERMES_HOME')}\n"
                "    try:\n"
                "        manager.discover_and_load()\n"
                "    except BaseException as error:\n"
                "        result['error'] = getattr(error, 'code', type(error).__name__)\n"
                "        result['message'] = str(error)\n"
                "    path = os.environ.get('XNO_LAUNCHER_RESULT')\n"
                "    if path:\n"
                "        with open(path, 'w', encoding='utf-8') as stream:\n"
                "            json.dump(result, stream, sort_keys=True)\n"
                "    if result.get('error'):\n"
                "        raise SystemExit(1)\n"
                "native.main = _main\n"
            )
        else:
            sitecustomize = (
                "import json, os, sys, types\n"
                "import hermes_cli\n"
                "fake = types.ModuleType('hermes_cli.main')\n"
                "def _main():\n"
                "    from hermes_cli.plugins import get_plugin_manager\n"
                "    result = {'argv': sys.argv[1:]}\n"
                "    try:\n"
                "        get_plugin_manager().discover_and_load()\n"
                "    except BaseException as error:\n"
                "        result['error'] = getattr(error, 'code', type(error).__name__)\n"
                "        result['message'] = str(error)\n"
                "    path = os.environ.get('XNO_LAUNCHER_RESULT')\n"
                "    if path:\n"
                "        with open(path, 'w', encoding='utf-8') as stream:\n"
                "            json.dump(result, stream, sort_keys=True)\n"
                "    if result.get('error'):\n"
                "        raise SystemExit(1)\n"
                "fake.main = _main\n"
                "sys.modules['hermes_cli.main'] = fake\n"
                "hermes_cli.main = fake\n"
            )
        (site / "sitecustomize.py").write_text(sitecustomize, encoding="utf-8")
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": os.pathsep.join((str(site), str(ROOT))),
            "HOME": str(self.root / "home"),
            "HERMES_HOME": str(hermes_home or profile),
            "DATA_DIR": str(self.root / "data"),
            "XNOBRAIN_ACTIVITY_FD": "3",
            "XNO_LAUNCHER_RESULT": str(result_path),
        }
        if register_log is not None:
            environment["XNO_REGISTER_LOG"] = str(register_log)
        return agent, environment

    def _run_launcher(
        self,
        profile,
        workspace_id,
        *,
        register_log=None,
        args=None,
        profile_aware=False,
        hermes_home=None,
        tag=None,
    ):
        result_path = self.root / f"result-{tag or workspace_id or 'unmanaged'}.json"
        agent, environment = self._launcher(
            profile,
            result_path=result_path,
            register_log=register_log,
            profile_aware=profile_aware,
            hermes_home=hermes_home,
        )
        if workspace_id:
            environment["RUNTIME_WORKSPACE_ID"] = workspace_id
        completed = subprocess.run(
            [str(agent), *(args or ["-p", "poem"])],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        result = (
            json.loads(result_path.read_text(encoding="utf-8")) if result_path.is_file() else {}
        )
        return completed, result

    def _selected_profile(self, register_source):
        root = self.root / "profile-root"
        other = root / "profiles" / "other"
        (root / "logs").mkdir(parents=True, exist_ok=True)
        (other / "logs").mkdir(parents=True, exist_ok=True)
        plugin = other / "plugins" / "sample"
        plugin.mkdir(parents=True)
        (plugin / "plugin.yaml").write_text("name: sample\nversion: 1.0.0\n")
        (plugin / "__init__.py").write_text(register_source)
        (other / "config.yaml").write_text(yaml.safe_dump({"plugins": {"enabled": ["sample"]}}))
        digest = tree_digest(plugin)
        save_state(
            other,
            {
                "plugins": {
                    "sample": {
                        "artifact_digest": digest,
                        "scan": {"status": "COMPLETED", "verdict": "safe"},
                        "approval": {"approved": True, "artifact_digest": digest},
                        "revision": 2,
                    }
                },
                "hooks": {},
                "mcp": {},
                "revision": 2,
            },
        )
        return root, other

    def test_pinned_main_applies_profile_without_discovery(self):
        sentinel = self.root / "selected-register-ran"
        source = (
            "from pathlib import Path\n"
            f"def register(ctx):\n    Path({str(sentinel)!r}).write_text('ran')\n"
        )
        root, other = self._selected_profile(source)
        cases = {
            "short": ["hermes", "-p", "other", "chat"],
            "equals": ["hermes", "--profile=other", "chat"],
            "sticky": ["hermes", "chat"],
        }
        for name, argv in cases.items():
            with self.subTest(mode=name):
                sentinel.unlink(missing_ok=True)
                active_profile = root / "active_profile"
                if name == "sticky":
                    active_profile.write_text("other\n", encoding="utf-8")
                else:
                    active_profile.unlink(missing_ok=True)
                result_path = self.root / f"import-{name}.json"
                code = (
                    "import json, os, sys\n"
                    "from pathlib import Path\n"
                    f"sys.argv = {argv!r}\n"
                    "import hermes_cli.main\n"
                    "from hermes_cli.plugins import get_plugin_manager\n"
                    "manager = get_plugin_manager()\n"
                    "result = {'home': os.environ.get('HERMES_HOME'), "
                    "'discovered': bool(getattr(manager, '_discovered', False)), "
                    "'argv': sys.argv[1:]}\n"
                    f"Path({str(result_path)!r}).write_text(json.dumps(result))\n"
                )
                completed = subprocess.run(
                    [sys.executable, "-c", code],
                    env={
                        "PATH": os.environ.get("PATH", ""),
                        "PYTHONPATH": str(ROOT),
                        "HOME": str(self.root / "home"),
                        "HERMES_HOME": str(root),
                    },
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                result = json.loads(result_path.read_text(encoding="utf-8"))
                self.assertEqual(result["home"], str(other))
                self.assertFalse(result["discovered"])
                self.assertEqual(result["argv"], ["chat"])
                self.assertFalse(sentinel.exists())

    def test_shell_launcher_selected_profile_blocks_dynamic_alias(self):
        root, other = self._selected_profile(
            "def register(ctx):\n"
            "    getattr(ctx, 'register_' + 'middleware')("
            "'pre_tool_call', lambda *args, **kwargs: None)\n"
        )
        cases = {
            "short": ["-p", "other", "chat"],
            "equals": ["--profile=other", "chat"],
            "sticky": ["chat"],
        }
        for workspace_id in ("", "workspace-test"):
            for name, args in cases.items():
                with self.subTest(workspace_id=workspace_id or "unmanaged", mode=name):
                    active_profile = root / "active_profile"
                    if name == "sticky":
                        active_profile.write_text("other\n", encoding="utf-8")
                    else:
                        active_profile.unlink(missing_ok=True)
                    tag = f"{name}-{workspace_id or 'unmanaged'}"
                    completed, result = self._run_launcher(
                        root,
                        workspace_id,
                        args=args,
                        profile_aware=True,
                        hermes_home=root,
                        tag=tag,
                    )
                    self.assertNotEqual(
                        completed.returncode,
                        0,
                        (completed.stdout, completed.stderr, result),
                    )
                    if result:
                        self.assertEqual(result["error"], "plugin_capability_forbidden")
                        self.assertEqual(result["home"], str(other))
                        self.assertEqual(result["argv"], ["chat"])
                    else:
                        self.assertIn("plugin_capability_forbidden", completed.stderr)

    def test_shell_launcher_selected_profile_allowed_register_once(self):
        root, other = self._selected_profile(
            "import os\n"
            "def register(ctx):\n"
            "    with open(os.environ['XNO_REGISTER_LOG'], 'a', encoding='utf-8') as stream:\n"
            "        stream.write('register\\n')\n"
        )
        register_log = self.root / "profile-register.log"
        for workspace_id in ("", "workspace-test"):
            with self.subTest(workspace_id=workspace_id or "unmanaged"):
                register_log.unlink(missing_ok=True)
                (root / "active_profile").unlink(missing_ok=True)
                completed, result = self._run_launcher(
                    root,
                    workspace_id,
                    register_log=register_log,
                    args=["-p", "other", "chat"],
                    profile_aware=True,
                    hermes_home=root,
                    tag=f"allowed-{workspace_id or 'unmanaged'}",
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(register_log.read_text(encoding="utf-8").count("\n"), 1)
                if result:
                    self.assertEqual(result["home"], str(other))
                    self.assertEqual(result["argv"], ["chat"])

    def test_shell_launcher_routes_both_branches_and_preserves_arguments(self):
        profile = self._approved_profile(
            "import os\n"
            "def register(ctx):\n"
            "    with open(os.environ['XNO_REGISTER_LOG'], 'a', encoding='utf-8') as stream:\n"
            "        stream.write('register\\n')\n"
            "    ctx.register_hook('post_tool_call', lambda **kwargs: None)\n"
        )
        register_log = self.root / "register.log"
        for workspace_id in ("", "workspace-test"):
            with self.subTest(workspace_id=workspace_id or "unmanaged"):
                register_log.unlink(missing_ok=True)
                completed, result = self._run_launcher(
                    profile, workspace_id, register_log=register_log
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(result["argv"], ["-p", "poem"])
                self.assertIsNone(result.get("error"))
                self.assertEqual(register_log.read_text(encoding="utf-8").count("\n"), 1)

    def test_shell_launcher_blocks_dynamic_alias_registration_in_child(self):
        profile = self._approved_profile(
            "def register(ctx):\n"
            "    getattr(ctx, 'register_' + 'middleware')("
            "'pre_tool_call', lambda *args, **kwargs: None)\n"
        )
        for workspace_id in ("", "workspace-test"):
            with self.subTest(workspace_id=workspace_id or "unmanaged"):
                completed, result = self._run_launcher(profile, workspace_id)
                self.assertNotEqual(
                    completed.returncode, 0, (completed.stdout, completed.stderr, result)
                )
                if result:
                    self.assertEqual(result["error"], "plugin_capability_forbidden")
                    self.assertEqual(result["argv"], ["-p", "poem"])
                else:
                    self.assertIn("plugin_capability_forbidden", completed.stderr)

    def test_shell_launcher_has_no_raw_engine_fallback(self):
        launcher = (ROOT / "runtime" / "agent-cli.sh").read_text(encoding="utf-8")
        self.assertNotIn('exec "$hermes" "$@"', launcher)
        self.assertIn("xnobrain.integrations.capacity_cli import main", launcher)

    def test_capacity_one_shot_launcher_rejects_approved_forbidden_plugin(self):
        import contextlib
        import io

        from hermes_cli.plugins import get_plugin_manager

        from xnobrain.integrations import capacity_cli

        profile = self._approved_forbidden_profile()

        def native_main():
            get_plugin_manager().discover_and_load()

        environment = patch.dict(os.environ, {"HERMES_HOME": str(profile)})
        with (
            environment,
            patch("xnobrain.integrations.code_tools.install_code_tools"),
            patch.object(capacity_cli, "install_agent_admission"),
            patch("hermes_cli.main.main", native_main),
            contextlib.redirect_stderr(io.StringIO()) as stderr,
        ):
            with self.assertRaises(SystemExit) as stopped:
                capacity_cli.main()
            self.assertEqual(stopped.exception.code, 1)
            self.assertIn("plugin_capability_forbidden", stderr.getvalue())
        get_plugin_manager().unload()

    def test_worker_one_shot_bootstrap_rejects_approved_forbidden_plugin(self):
        import contextlib
        import io

        from hermes_cli.plugins import get_plugin_manager

        from xnobrain.integrations import worker_cli

        profile = self._approved_forbidden_profile()

        def native_main():
            get_plugin_manager().discover_and_load()

        with (
            patch.dict(os.environ, {"HERMES_HOME": str(profile)}),
            patch("xnobrain.integrations.code_tools.install_code_tools"),
            patch("xnobrain.integrations.run_admission.require_admitted"),
            patch.object(worker_cli, "accounting_enabled", return_value=False),
            patch("hermes_cli.main.main", native_main),
            patch.object(worker_cli.sys, "argv", ["worker"]),
            contextlib.redirect_stderr(io.StringIO()) as stderr,
        ):
            with self.assertRaises(SystemExit) as stopped:
                worker_cli.main()
            self.assertEqual(stopped.exception.code, 1)
            self.assertIn("plugin_capability_forbidden", stderr.getvalue())
        get_plugin_manager().unload()

    def test_gateway_launcher_rejects_approved_forbidden_plugin(self):
        import contextlib
        import io

        from hermes_cli.plugins import get_plugin_manager

        from xnobrain.integrations import gateway_cli

        profile = self._approved_forbidden_profile()

        def native_main():
            get_plugin_manager().discover_and_load()

        with (
            patch.dict(os.environ, {"HERMES_HOME": str(profile)}),
            patch(
                "xnobrain.integrations.conversation_credentials.install_workspace_router_key_scope"
            ),
            patch.object(gateway_cli, "install_gateway_turns"),
            patch.object(gateway_cli, "install_runtime_channel_delegation"),
            patch("hermes_cli.main.main", native_main),
            patch.object(gateway_cli.sys, "argv", ["before"]),
            contextlib.redirect_stderr(io.StringIO()) as stderr,
        ):
            with self.assertRaises(SystemExit) as stopped:
                gateway_cli.main()
            self.assertEqual(stopped.exception.code, 1)
            self.assertIn("plugin_capability_forbidden", stderr.getvalue())
        self.assertNotIn("sample", get_plugin_manager()._plugins)
        get_plugin_manager().unload()


if __name__ == "__main__":
    unittest.main()
