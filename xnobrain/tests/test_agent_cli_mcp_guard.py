"""The agent CLI refuses MCP credentials supplied from an agent run."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REFUSAL = (
    "Refusing to store MCP credentials from an agent run; "
    "ask the user to connect through the chat connect card."
)


class AgentCliMcpGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.project = self.base / "installation"
        runtime = self.project / "runtime"
        runtime.mkdir(parents=True)
        shutil.copy2(ROOT / "runtime" / "agent-cli.sh", runtime / "agent-cli.sh")
        linker = runtime / "link-native-profiles.sh"
        linker.write_text("#!/usr/bin/env bash\nset -euo pipefail\nexit 0\n", encoding="utf-8")
        linker.chmod(0o755)
        hermes = self.project / ".tools/hermes-agent/venv/bin/hermes"
        hermes.parent.mkdir(parents=True)
        hermes.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$@\"\n", encoding="utf-8")
        hermes.chmod(0o755)
        self.agent = runtime / "agent-cli.sh"
        self.environment = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.base / "home"),
            "XNOBRAIN_ACTIVITY_FD": "1",
        }

    def run_cli(self, *arguments, session=None):
        environment = dict(self.environment)
        if session is not None:
            environment["HERMES_SESSION_ID"] = session
        return subprocess.run(
            ["bash", str(self.agent), *arguments],
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def test_agent_run_refuses_mcp_add_with_literal_credentials(self):
        for credential in (
            ("--header", "Authorization: Bearer k"),
            ("--header=Authorization: Bearer k",),
            ("-H", "Authorization: Bearer k"),
            ("--env", "TOKEN=k"),
            ("--env=TOKEN=k",),
            ("-e", "TOKEN=k"),
        ):
            with self.subTest(credential=credential):
                result = self.run_cli(
                    "mcp",
                    "add",
                    "x",
                    "--url",
                    "https://a.example",
                    *credential,
                    session="agent-session",
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr.strip(), REFUSAL)

    def test_agent_run_allows_credential_free_mcp_add(self):
        result = self.run_cli(
            "mcp", "add", "x", "--url", "https://a.example", session="agent-session"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            ["mcp", "add", "x", "--url", "https://a.example"],
        )

    def test_operator_shell_can_still_run_mcp_add(self):
        result = self.run_cli(
            "mcp",
            "add",
            "x",
            "--url",
            "https://a.example",
            "--header",
            "Authorization: Bearer k",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--header", result.stdout)


if __name__ == "__main__":
    unittest.main()
