"""Storage validation must precede application imports and data writes."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class StorageBootEntrypointTests(unittest.TestCase):
    def test_missing_volume_fails_before_application_imports_or_data_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "xnobrain/integrations"
            package.mkdir(parents=True)
            for directory in (package.parent, package):
                (directory / "__init__.py").write_text(
                    'raise RuntimeError("application imported before storage validation")\n'
                )
            shutil.copyfile(
                ROOT / "xnobrain/integrations/runtime_data_volume.py",
                package / "runtime_data_volume.py",
            )
            python = root / ".tools/python/bin/python"
            python.parent.mkdir(parents=True)
            python.symlink_to(sys.executable)
            data = root / "data"
            environment = {
                **os.environ,
                "PYTHONPATH": str(root),
                "HERMES_RUNTIME_PYTHON": sys.executable,
                "RUNTIME_DATA_MOUNT_REQUIRED": "true",
                "RUNTIME_DATA_VOLUME_PATH": str(data),
                "RUNTIME_HERMES_HOME": str(data / "hermes"),
                "RUNTIME_DATA_DIR": str(data / "runtime"),
                "RUNTIME_HOME": str(data / "home"),
            }
            for relative in (
                "scripts/prepare-service-data.sh",
                "runtime/container-entrypoint.sh",
            ):
                with self.subTest(entrypoint=relative):
                    script = root / relative
                    script.parent.mkdir(exist_ok=True)
                    script.write_text(
                        (ROOT / relative).read_text().replace("/opt/xnobrain-app", str(root))
                    )
                    result = subprocess.run(
                        ["bash", str(script)],
                        env=environment,
                        capture_output=True,
                        text=True,
                        timeout=10,
                        check=False,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("Runtime persistent storage unavailable", result.stderr)
                    self.assertNotIn("application imported", result.stderr)
                    self.assertFalse(data.exists())
