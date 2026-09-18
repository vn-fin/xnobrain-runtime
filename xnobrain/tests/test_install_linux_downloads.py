"""Source-level checks for transient downloads in native VM builds."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class InstallerDownloadTests(unittest.TestCase):
    def test_node_and_hermes_installer_retry_transient_http_errors(self):
        installer = (ROOT / "scripts" / "install-linux.sh").read_text(
            encoding="utf-8"
        )
        self.assertEqual(installer.count("--retry 8 --retry-max-time 300"), 2)
        self.assertIn("--retry-max-time 300", installer)
        self.assertIn('rm -f -- "$archive_path"', installer)
        self.assertIn("https://nodejs.org/dist/", installer)
        self.assertIn(
            "https://raw.githubusercontent.com/NousResearch/hermes-agent/",
            installer,
        )
        self.assertIn('export PIP_RETRIES="${PIP_RETRIES:-5}"', installer)
        self.assertIn('export npm_config_fetch_retries="${npm_config_fetch_retries:-5}"', installer)
        self.assertIn("Acquire::Retries=5", installer)

    def test_native_vm_installer_uses_apt_download_retries(self):
        script = (ROOT / "deploy" / "incus" / "install-native-runtime.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("Acquire::Retries=5", script)


if __name__ == "__main__":
    unittest.main()
