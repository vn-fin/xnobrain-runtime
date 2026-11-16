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
        self.assertIn("/opt/xnobrain/.tools/node/bin", raw)
        command = shlex.split(service.get("ExecStart"))
        self.assertEqual(command[:3], ["/usr/bin/setpriv", "--no-new-privs", "--"])
        self.assertEqual(command[-1], "/usr/local/bin/xnobrain-gateway-entrypoint")

        self.assertIn("xnobrain-runtime-prepare.service", unit["Unit"].get("Requires"))
        after = unit["Unit"].get("After")
        self.assertIn("xnobrain-runtime-prepare.service", after)
        self.assertIn("xnobrain-runtime.service", after)
        self.assertEqual(unit["Install"].get("WantedBy"), "multi-user.target")

    def test_native_gateway_unit_mirrors_api_unit_and_hosts_the_gateway(self):
        api_path = ROOT / "deploy/systemd/xnobrain-api.service"
        gateway_path = ROOT / "deploy/systemd/xnobrain-gateway.service"
        api = configparser.ConfigParser(interpolation=None, strict=False)
        api.read(api_path)
        gateway = configparser.ConfigParser(interpolation=None, strict=False)
        gateway.read(gateway_path)
        raw = gateway_path.read_text(encoding="utf-8")

        for key in (
            "User",
            "Group",
            "EnvironmentFile",
            "UMask",
            "NoNewPrivileges",
            "PrivateTmp",
            "ProtectSystem",
            "ReadWritePaths",
            "RestrictAddressFamilies",
        ):
            with self.subTest(setting=key):
                self.assertEqual(
                    gateway["Service"].get(key), api["Service"].get(key), key
                )
        self.assertEqual(gateway["Service"].get("ReadWritePaths"), "/srv/xnobrain-data")
        self.assertEqual(gateway["Service"].get("Restart"), "always")
        self.assertEqual(gateway["Service"].get("RestartSec"), "5s")
        self.assertEqual(
            gateway["Service"].get("ExecReload"), "/bin/kill -USR1 $MAINPID"
        )
        self.assertEqual(gateway["Service"].get("KillMode"), "mixed")
        self.assertEqual(gateway["Service"].get("TimeoutStopSec"), "60s")
        self.assertIn("ConditionPathExists=/etc/xnobrain/xnobrain.env", raw)
        self.assertIn("PartOf=xnobrain.target", raw)
        self.assertIn("Environment=XNOBRAIN_APP_DIR=/opt/xnobrain", raw)
        self.assertEqual(gateway["Install"].get("WantedBy"), "xnobrain.target")
        self.assertIn("xnobrain-prepare.service", gateway["Unit"].get("Requires"))
        self.assertIn("xnobrain-api.service", gateway["Unit"].get("After"))

        command = shlex.split(gateway["Service"].get("ExecStart"))
        self.assertEqual(
            command[-4:],
            [
                "/usr/bin/setpriv",
                "--no-new-privs",
                "--",
                "/usr/local/bin/xnobrain-gateway-entrypoint",
            ],
        )
        self.assertIn("HERMES_HOME=${RUNTIME_HERMES_HOME}", command)
        self.assertIn("HERMES_ROOT_PROFILE=${RUNTIME_HERMES_HOME}", command)
        self.assertIn("HERMES_PROFILES_ROOT=${RUNTIME_HERMES_PROFILES_ROOT}", command)
        self.assertIn(
            "RUNTIME_INTERNAL_URL=http://127.0.0.1:${RUNTIME_API_SERVER_PORT}",
            command,
        )

    def test_native_installer_installs_and_enables_the_gateway_unit(self):
        installer = (ROOT / "deploy/incus/install-native-runtime.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('"$install_root/runtime/gateway-entrypoint.sh"', installer)
        self.assertIn("/usr/local/bin/xnobrain-gateway-entrypoint", installer)
        self.assertIn('"$install_root/deploy/systemd/xnobrain-gateway.service"', installer)
        self.assertIn("/etc/systemd/system/xnobrain-gateway.service", installer)
        self.assertIn("systemctl enable xnobrain-gateway.service", installer)
        self.assertLess(
            installer.index("systemctl enable xnobrain-gateway.service"),
            installer.index("systemctl restart xnobrain.target"),
        )
        # The Zalo plugin and its bridge dependencies must stay in the image
        # before the gateway can start.
        self.assertIn("plugins/platforms/zalo", installer)
        self.assertIn('"$npm_bin" ci --omit=dev --no-audit --no-fund', installer)

    def test_container_hardening_installs_the_gateway_without_enabling_it(self):
        installer = (ROOT / "scripts/install-container-hardening.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "/opt/xnobrain/runtime/gateway-entrypoint.sh "
            "/usr/local/bin/xnobrain-gateway-entrypoint",
            installer,
        )
        self.assertIn(
            "/opt/xnobrain/runtime/xnobrain-gateway.service "
            "/etc/systemd/system/xnobrain-gateway.service",
            installer,
        )
        # Reusable images and source rollouts install files only; the workspace
        # activation step enables the unit once data and credentials exist.
        self.assertNotIn("systemctl enable xnobrain-gateway.service", installer)

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

    def test_gateway_entrypoint_derives_canonical_homes_from_agent_data_root(self):
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
        self.assertEqual(lines[1], "/opt/data/agent/big-brother")
        self.assertEqual(lines[2], "/opt/data/agent/big-brother")
        self.assertEqual(lines[3], "/opt/data/agent")
        self.assertEqual(lines[4], "/opt/xnobrain-app")
        self.assertIn("from xnobrain.integrations.gateway_cli import main", lines[5])

    def test_gateway_entrypoint_prefers_container_runtime_hermes_layout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            key_file = Path(temp_dir) / "router-api-key"
            key_file.write_text("file-secret\n", encoding="utf-8")

            lines, _ = self._run_gateway_entrypoint(
                {
                    "RUNTIME_LLM_API_KEY": "existing-key",
                    "RUNTIME_LLM_API_KEY_FILE": str(key_file),
                    # The unit file defaults HERMES_* to the Docker layout; the
                    # Incus container contract must win.
                    "HERMES_HOME": "/opt/data/agent/big-brother",
                    "HERMES_ROOT_PROFILE": "/opt/data/agent/big-brother",
                    "HERMES_PROFILES_ROOT": "/opt/data/agent",
                    "RUNTIME_HERMES_HOME": "/opt/data/hermes/root",
                    "RUNTIME_HERMES_ROOT_PROFILE": "/opt/data/hermes/root",
                    "RUNTIME_HERMES_PROFILES_ROOT": "/opt/data/hermes/profiles",
                    "XNOBRAIN_APP_DIR": "/opt/xnobrain-app",
                }
            )

        self.assertEqual(lines[0], "existing-key")
        self.assertEqual(lines[1], "/opt/data/hermes/root")
        self.assertEqual(lines[2], "/opt/data/hermes/root")
        self.assertEqual(lines[3], "/opt/data/hermes/profiles")
        self.assertIn("from xnobrain.integrations.gateway_cli import main", lines[5])


if __name__ == "__main__":
    unittest.main()
