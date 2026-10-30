"""The nonroot launcher consumes settings already prepared by systemd."""

import configparser
import shlex
import subprocess
import sys
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


if __name__ == "__main__":
    unittest.main()
