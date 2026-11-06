"""The nonroot launcher consumes settings already prepared by systemd."""

import configparser
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class SystemdEnvironmentTests(unittest.TestCase):
    def test_launcher_preserves_prepared_environment_without_reading_pid1(self):
        expected = {
            "FT_ENABLE_VM_REBALANCE": "false",
            "FT_ENABLE_AGENT_POOL_MEMORY_ADMISSION": "false",
            "FT_ENABLE_UI_CUSTOMIZATION": "true",
            "RUNTIME_HERMES_HOME": "/opt/data/home with spaces=value",
            "HERMES_DESKTOP": "1",
            "HOME": "/opt/data/home",
            "PATH": "/opt/xnobrain/.tools/node/bin:/usr/bin:/bin",
            "XDG_CONFIG_HOME": "/opt/data/home/.config",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        }
        script = (ROOT / "runtime/systemd-entrypoint.sh").read_text()
        script = script.replace("exec /usr/local/bin/xnobrain-runtime", "exec /usr/bin/env -0")
        result = subprocess.run(
            ["/bin/bash", "-c", script],
            env=expected,
            capture_output=True,
            timeout=10,
            check=True,
        )
        actual = dict(item.decode().split("=", 1) for item in result.stdout.split(b"\0") if item)
        for key, value in expected.items():
            with self.subTest(variable=key):
                self.assertEqual(actual.get(key), value)

    def test_installer_restores_nnp_after_incus_global_drop_in(self):
        installer = (ROOT / "scripts/install-container-hardening.sh").read_text()
        entries = [
            shlex.split(line)
            for line in installer.splitlines()
            if line.startswith("printf ") and "zzzz-xnobrain-hardening.conf" in line
        ]
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        destination = Path(entry[-1])
        self.assertEqual(destination.parent.name, "xnobrain-runtime.service.d")
        self.assertGreater(destination.name, "zzz-lxc-service.conf")
        effective = configparser.ConfigParser(interpolation=None)
        effective.read(ROOT / "runtime/xnobrain-runtime.service")
        effective.read_string("[Service]\nNoNewPrivileges=no\n")
        effective.read_string(entry[1].replace(r"\n", "\n"))
        self.assertEqual(effective.get("Service", "NoNewPrivileges"), "yes")
        self.assertEqual(effective.get("Service", "CapabilityBoundingSet"), "")
        self.assertEqual(effective.get("Service", "AmbientCapabilities"), "")

    @unittest.skipUnless(sys.platform == "linux", "setpriv service execution requires Linux")
    def test_main_exec_enforces_nnp_before_the_runtime_launcher(self):
        unit = configparser.ConfigParser(interpolation=None)
        unit.read(ROOT / "runtime/xnobrain-runtime.service")
        command = shlex.split(unit.get("Service", "ExecStart"))
        self.assertEqual(command[-1], "/usr/local/bin/xnobrain-systemd-entrypoint")
        probe = (
            "from pathlib import Path; "
            "rows=dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines()); "
            "print(rows['NoNewPrivs'].strip())"
        )
        result = subprocess.run(
            [*command[:-1], sys.executable, "-I", "-S", "-c", probe],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), "1")

    def test_gateway_unit_keeps_restart_reload_and_hardening(self):
        unit_path = ROOT / "runtime/xnobrain-gateway.service"
        raw = unit_path.read_text(encoding="utf-8")
        unit = configparser.ConfigParser(interpolation=None, strict=False)
        unit.read(unit_path)

        service = unit["Service"]
        self.assertEqual(service.get("User"), "xnobrain")
        self.assertEqual(service.get("Group"), "xnobrain")
        self.assertEqual(service.get("Restart"), "always")
        self.assertEqual(service.get("RestartSec"), "5s")
        self.assertEqual(service.get("ExecReload"), "/bin/kill -USR1 $MAINPID")
        self.assertEqual(service.get("KillMode"), "mixed")
        self.assertEqual(service.get("TimeoutStopSec"), "60s")
        self.assertEqual(service.get("ProtectSystem"), "strict")
        self.assertEqual(
            service.get("EnvironmentFile"), "/run/xnobrain-runtime-config/environment"
        )
        self.assertIn(
            "Environment=HERMES_HOME=/opt/data/agent/big-brother",
            raw,
        )
        self.assertIn(
            "Environment=HERMES_PROFILES_ROOT=/opt/data/agent",
            raw,
        )
        self.assertIn(
            "Environment=RUNTIME_LLM_API_KEY_FILE=/run/xnobrain-runtime/router-api-key",
            raw,
        )
        command = shlex.split(service.get("ExecStart"))
        self.assertEqual(command[:3], ["/usr/bin/setpriv", "--no-new-privs", "--"])
        self.assertEqual(command[-1], "/usr/local/bin/xnobrain-gateway-entrypoint")

        self.assertIn("xnobrain-runtime-prepare.service", unit["Unit"].get("Requires"))
        after = unit["Unit"].get("After")
        self.assertIn("xnobrain-runtime-prepare.service", after)
        self.assertIn("xnobrain-runtime.service", after)
        self.assertEqual(unit["Install"].get("WantedBy"), "multi-user.target")

    def _run_gateway_entrypoint(self, env):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            capture = temp / "capture"
            python = temp / "python"
            python.write_text(
                "#!/bin/sh\n"
                'printf "%s\\n" "${RUNTIME_LLM_API_KEY-}" "${HERMES_HOME-}" '
                '"${HERMES_ROOT_PROFILE-}" "${HERMES_PROFILES_ROOT-}" '
                '"${PYTHONPATH-}" "$*" > "${CAPTURE}"\n',
                encoding="utf-8",
            )
            python.chmod(0o755)
            complete_env = {
                "PATH": f"{temp}:/usr/bin:/bin",
                "CAPTURE": str(capture),
                "HERMES_RUNTIME_PYTHON": str(python),
            }
            complete_env.update(env)
            result = subprocess.run(
                ["/bin/sh", str(ROOT / "runtime/gateway-entrypoint.sh")],
                env=complete_env,
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            lines = capture.read_text(encoding="utf-8").splitlines()
            return lines, result

    def test_gateway_entrypoint_loads_key_file_without_echoing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            key_file = Path(temp_dir) / "router-api-key"
            key_file.write_text("secret-value\n", encoding="utf-8")

            lines, result = self._run_gateway_entrypoint(
                {
                    "RUNTIME_LLM_API_KEY": "",
                    "RUNTIME_LLM_API_KEY_FILE": str(key_file),
                    "XNOBRAIN_APP_DIR": "/workspace",
                    "HERMES_HOME": "/opt/data/agent/big-brother",
                    "HERMES_PROFILES_ROOT": "/opt/data/agent",
                }
            )

        self.assertEqual(lines[0], "secret-value")
        self.assertEqual(lines[1], "/opt/data/agent/big-brother")
        self.assertEqual(lines[2], "")
        self.assertEqual(lines[3], "/opt/data/agent")
        self.assertEqual(lines[4], "/workspace")
        self.assertIn("from xnobrain.integrations.gateway_cli import main", lines[5])
        self.assertNotIn("secret-value", result.stdout + result.stderr)

    def test_gateway_entrypoint_keeps_existing_key_and_does_not_derive_homes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            key_file = Path(temp_dir) / "router-api-key"
            key_file.write_text("file-secret\n", encoding="utf-8")

            lines, _ = self._run_gateway_entrypoint(
                {
                    "RUNTIME_LLM_API_KEY": "existing-key",
                    "RUNTIME_LLM_API_KEY_FILE": str(key_file),
                    "RUNTIME_AGENT_DATA_ROOT": "/opt/data/agent",
                    "XNOBRAIN_APP_DIR": "/opt/xnobrain-app",
                }
            )

        self.assertEqual(lines[0], "existing-key")
        self.assertEqual(lines[1], "")
        self.assertEqual(lines[2], "")
        self.assertEqual(lines[3], "")
        self.assertEqual(lines[4], "/opt/xnobrain-app")
        self.assertIn("from xnobrain.integrations.gateway_cli import main", lines[5])


if __name__ == "__main__":
    unittest.main()
