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
        result = self.adapter.update_mcp(
            "agent",
            {
                "local": {"command": "npx", "args": ["-y"], "env": {"MODE": "safe"}},
                "remote": {
                    "url": "https://example.com/mcp",
                    "headers": {"Authorization": "${MCP_TOKEN}"},
                    "tools": {"include": ["search"]},
                },
            },
        )
        self.assertEqual(set(result["servers"]), {"local", "remote"})

    def test_accepts_control_connection_entry(self) -> None:
        connection_id = "13ac11ac-a8cf-e8cc-b483-ba397507195a"
        self.adapter.update_mcp(
            "agent",
            {"remote": {"url": "https://example.com/mcp", "xnobrain_connection_id": connection_id}},
        )
        self.assertEqual(
            self.adapter.config["mcp_servers"]["remote"]["xnobrain_connection_id"], connection_id
        )

    def test_rejects_invalid_transport_fields_without_persisting(self) -> None:
        invalid = (
            {"probe": {"command": 123}},
            {"probe": {"url": "not-a-url"}},
            {"probe": {"command": "run", "url": "https://example.com"}},
            {"probe": {"command": "run", "args": [1]}},
            {"probe": {"command": "run", "env": {"TOKEN": 2}}},
            {"probe": {"command": "run", "tools": {"include": [""]}}},
            {"probe": {"command": "run", "unexpected": True}},
            {"probe": {"url": "https://example.com", "xnobrain_connection_id": "nope"}},
            {
                "probe": {
                    "url": "https://example.com",
                    "headers": {"Authorization": "x"},
                    "xnobrain_connection_id": "13ac11ac-a8cf-e8cc-b483-ba397507195a",
                }
            },
        )
        for servers in invalid:
            with self.subTest(servers=servers), self.assertRaises(AgentAPIError):
                self.adapter.update_mcp("agent", servers)
            self.assertEqual(self.adapter.config, {})

    def test_shadow_servers_are_hidden_and_preserved_on_edit(self) -> None:
        self.adapter.config = {
            "mcp_servers": {"plugin__docs": {"enabled": False, "xnobrain_plugin_shadow": True}}
        }
        self.assertEqual(self.adapter.get_mcp("agent")["servers"], {})
        with self.assertRaises(AgentAPIError) as raised:
            self.adapter.update_mcp("agent", {"plugin__docs": {"url": "https://example.com/mcp"}})
        self.assertEqual(raised.exception.code, "mcp_server_managed")
        result = self.adapter.update_mcp("agent", {"custom": {"url": "https://example.com/mcp"}})
        self.assertEqual(set(result["servers"]), {"custom"})
        self.assertTrue(
            self.adapter.config["mcp_servers"]["plugin__docs"]["xnobrain_plugin_shadow"]
        )

    def test_masked_env_is_kept_on_stdio_edit(self) -> None:
        self.adapter.update_mcp(
            "agent",
            {"local": {"command": "npx", "env": {"API_KEY": "secret", "MODE": "a"}}},
        )
        masked = self.adapter.get_mcp("agent")["servers"]["local"]
        self.assertEqual(masked["env"], {"API_KEY": "***", "MODE": "***"})
        masked["args"] = ["-y"]
        masked["env"]["MODE"] = "b"
        self.adapter.update_mcp("agent", {"local": masked})
        saved = self.adapter.config["mcp_servers"]["local"]
        self.assertEqual(saved["env"], {"API_KEY": "secret", "MODE": "b"})
        self.assertEqual(self.adapter.get_mcp("agent")["servers"]["local"]["env"]["API_KEY"], "***")

    def test_masked_value_without_saved_counterpart_is_rejected(self) -> None:
        self.adapter.update_mcp("agent", {"local": {"command": "npx", "env": {"A": "x"}}})
        before = dict(self.adapter.config)
        cases = (
            {"local": {"command": "npx", "env": {"NEW_KEY": "***"}}},
            {"renamed": {"command": "npx", "env": {"A": "***"}}},
            {"remote": {"url": "https://example.com/mcp", "headers": {"Authorization": "***"}}},
            {"local": {"url": "https://example.com/mcp", "headers": {"A": "***"}}},
        )
        for servers in cases:
            with self.subTest(servers=servers), self.assertRaises(AgentAPIError) as raised:
                self.adapter.update_mcp("agent", servers)
            self.assertEqual(raised.exception.code, "invalid_mcp_config")
            self.assertEqual(self.adapter.config, before)

    def test_remote_literal_headers_are_unchanged(self) -> None:
        remote = {"url": "https://example.com/mcp", "headers": {"X-Key": "literal"}}
        self.adapter.update_mcp("agent", {"remote": remote})
        self.assertEqual(self.adapter.config["mcp_servers"]["remote"], remote)
        self.assertEqual(
            self.adapter.get_mcp("agent")["servers"]["remote"]["headers"], {"X-Key": "***"}
        )


if __name__ == "__main__":
    unittest.main()
