"""Pinned-engine MCP registration and dispatch isolation conformance."""

import asyncio
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from xnobrain.integrations import extension_engine


class _Session:
    def __init__(self, credential_marker):
        self.credential_marker = credential_marker

    async def call_tool(self, name, arguments):
        return SimpleNamespace(
            isError=False,
            content=[SimpleNamespace(text=self.credential_marker)],
            structuredContent=None,
        )


class MCPProfileIsolationTests(unittest.TestCase):
    def test_namespaced_servers_dispatch_separately_but_tools_are_global(self):
        from tools import mcp_tool
        from tools.registry import ToolRegistry

        with tempfile.TemporaryDirectory() as directory:
            profiles = [Path(directory) / name for name in ("profile-a", "profile-b")]
            for profile in profiles:
                profile.mkdir()
                (profile / "config.yaml").write_text("plugins:\n  enabled: []\n")

            registry = ToolRegistry()
            server_ids = (
                "xno_profile_a_generation_1_docs",
                "xno_profile_b_generation_1_docs",
            )
            tool_names = []
            servers = {}

            with (
                patch("tools.registry.registry", registry),
                patch.object(mcp_tool, "_record_tool_trust_metadata"),
                patch.object(mcp_tool, "_scan_mcp_description"),
                patch.object(mcp_tool, "_track_mcp_tool_server"),
                patch.object(
                    mcp_tool,
                    "_get_connected_server_for_call",
                    side_effect=lambda name: servers[name],
                ),
                patch.object(mcp_tool, "_trust_gate_check", return_value=None),
            ):
                for profile, server_id, credential_marker in zip(
                    profiles, server_ids, ("credential-a", "credential-b")
                ):
                    server = SimpleNamespace(
                        name=server_id,
                        _tools=[
                            SimpleNamespace(
                                name="echo",
                                description="fixture",
                                inputSchema={"type": "object", "properties": {}},
                                annotations=None,
                            )
                        ],
                        tool_timeout=5,
                        session=_Session(credential_marker),
                        _rpc_lock=asyncio.Lock(),
                        _pending_call_context=None,
                    )
                    servers[server_id] = server
                    with extension_engine.profile_context(profile):
                        registered = mcp_tool._register_server_tools(
                            server_id,
                            server,
                            {"tools": {"resources": False, "prompts": False}},
                        )
                    self.assertEqual(len(registered), 1)
                    tool_names.append(registered[0])

                entry_a, entry_b = [registry.get_entry(name) for name in tool_names]
                self.assertIsNotNone(entry_a)
                self.assertIsNotNone(entry_b)
                # The registering module is native core, so both entries land
                # in the process-global registry instead of either profile overlay.
                self.assertIsNotNone(registry.snapshot_registration(tool_names[0], scope=None))
                self.assertIsNotNone(registry.snapshot_registration(tool_names[1], scope=None))
                for profile, other_tool in zip(profiles, tool_names):
                    with extension_engine.profile_context(profile):
                        self.assertIsNotNone(registry.get_entry(other_tool))

                previous_loop = mcp_tool._mcp_loop
                loop_was_running = previous_loop is not None and previous_loop.is_running()
                mcp_tool._ensure_mcp_loop()
                try:
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        results = list(
                            pool.map(
                                lambda entry: entry.handler({}, context=None),
                                (entry_a, entry_b),
                            )
                        )
                    self.assertEqual(
                        [json.loads(result)["result"] for result in results],
                        ["credential-a", "credential-b"],
                    )

                    # Native deregistration is name-based and global. Removing
                    # A's registration removes it from both profile views; B's
                    # independently namespaced tool remains callable.
                    registry.deregister(tool_names[0])
                    for profile in profiles:
                        with extension_engine.profile_context(profile):
                            self.assertIsNone(registry.get_entry(tool_names[0]))
                            self.assertIsNotNone(registry.get_entry(tool_names[1]))
                    self.assertEqual(
                        json.loads(entry_b.handler({}, context=None))["result"],
                        "credential-b",
                    )
                finally:
                    if not loop_was_running:
                        mcp_tool._stop_mcp_loop()


if __name__ == "__main__":
    unittest.main()
