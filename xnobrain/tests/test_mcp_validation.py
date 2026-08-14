"""Strict MCP server schema regression tests."""

import tempfile
import unittest
from pathlib import Path

from xnobrain.integrations.hermes_support import AgentAPIError
from xnobrain.integrations.mcp import MCPIntegrationMixin


class _MCPAdapter(MCPIntegrationMixin):
    def __init__(self, root: Path) -> None:
        self.root = root
        self.config: dict = {}

    def _agent_name(self, value):
        return str(value)

    def _require_profile(self, _name):
        return self.root

    def _read_config(self, _profile):
        return dict(self.config)

    def _write_yaml_atomic(self, _path, config):
        self.config = config


class MCPValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.adapter = _MCPAdapter(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_accepts_strict_stdio_and_https_entries(self) -> None:
        result = self.adapter.update_mcp("agent", {
            "local": {"command": "npx", "args": ["-y"], "env": {"MODE": "safe"}},
            "remote": {
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "${MCP_TOKEN}"},
                "tools": {"include": ["search"]},
            },
        })
        self.assertEqual(set(result["servers"]), {"local", "remote"})

    def test_rejects_invalid_transport_fields_without_persisting(self) -> None:
        invalid = (
            {"probe": {"command": 123}},
            {"probe": {"url": "not-a-url"}},
            {"probe": {"command": "run", "url": "https://example.com"}},
            {"probe": {"command": "run", "args": [1]}},
            {"probe": {"command": "run", "env": {"TOKEN": 2}}},
            {"probe": {"command": "run", "tools": {"include": [""]}}},
            {"probe": {"command": "run", "unexpected": True}},
        )
        for servers in invalid:
            with self.subTest(servers=servers), self.assertRaises(AgentAPIError):
                self.adapter.update_mcp("agent", servers)
            self.assertEqual(self.adapter.config, {})


if __name__ == "__main__":
    unittest.main()
