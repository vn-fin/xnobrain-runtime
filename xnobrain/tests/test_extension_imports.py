"""Imported executable extensions never carry publisher approval authority."""

import json
import tempfile
import unittest
from pathlib import Path

from xnobrain.integrations.extension_imports import reset_imported_extension_trust
from xnobrain.integrations.extension_policy import MANAGED_MCP_KEY


class ExtensionImportTests(unittest.TestCase):
    def test_import_keeps_artifacts_but_discards_all_executable_trust(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            plugin = profile / "plugins" / "publisher-plugin"
            plugin.mkdir(parents=True)
            (plugin / "__init__.py").write_text("def register(ctx): pass\n")
            state = profile / ".xnobrain" / "extensions" / "plugins"
            state.mkdir(parents=True)
            (state / "publisher-plugin.json").write_text(json.dumps({"approved": True}))
            (profile / "shell-hooks-allowlist.json").write_text('{"approved": true}')
            gateway = profile / "hooks" / "publisher-startup"
            gateway.mkdir(parents=True)
            (gateway / "handler.py").write_text("def run(): pass\n")
            config = {
                "plugins": {
                    "enabled": ["publisher-plugin", "xnobrain-runtime-updates"],
                    "entries": {"publisher-plugin": {"allow_tool_override": True}},
                },
                "hooks": {"pre_tool_call": [{"command": "echo publisher"}]},
                MANAGED_MCP_KEY: {"publisher": {"url": "https://example.org/mcp"}},
                "model": {"default": "auto"},
            }
            reset_imported_extension_trust(profile, config)
            self.assertEqual(config["plugins"]["enabled"], [])
            self.assertEqual(
                config["plugins"]["disabled"],
                ["publisher-plugin", "xnobrain-runtime-updates"],
            )
            self.assertNotIn("entries", config["plugins"])
            self.assertNotIn("hooks", config)
            self.assertNotIn(MANAGED_MCP_KEY, config)
            self.assertFalse((profile / "shell-hooks-allowlist.json").exists())
            self.assertFalse(state.exists())
            self.assertFalse((profile / "hooks").exists())
            self.assertTrue(
                (
                    profile / ".xnobrain/imported-gateway-hooks/publisher-startup/handler.py"
                ).is_file()
            )
            self.assertTrue((plugin / "__init__.py").is_file())
            self.assertEqual(config["model"], {"default": "auto"})

    def test_missing_extension_state_does_not_invent_plugin_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            config = {"model": {"default": "auto"}}
            reset_imported_extension_trust(Path(directory), config)
            self.assertEqual(config, {"model": {"default": "auto"}})


if __name__ == "__main__":
    unittest.main()
