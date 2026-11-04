"""Profile-scoped managed MCP adapter conformance tests."""

import asyncio
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from xnobrain.integrations import managed_mcp
from xnobrain.integrations.extension_engine import profile_context
from xnobrain.integrations.extension_policy import load_state, save_state


class _FixtureSession:
    def __init__(self, credential, tools):
        self.credential = credential
        self.tools = tools

    async def list_tools(self, *args, **kwargs):
        return SimpleNamespace(tools=self.tools, nextCursor=None)

    async def call_tool(self, name, arguments):
        return SimpleNamespace(
            isError=False,
            content=[SimpleNamespace(text=self.credential)],
            structuredContent=None,
        )


def _tool(name):
    return SimpleNamespace(
        name=name,
        description=f"Fixture tool {name}",
        inputSchema={"type": "object", "properties": {}},
        annotations=SimpleNamespace(readOnlyHint=True),
    )


class ManagedMCPTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.profiles = []
        self.generations = []
        for name, token in (("profile-a", "token-a"), ("profile-b", "token-b")):
            profile = Path(self.temporary.name) / name
            profile.mkdir()
            (profile / "config.yaml").write_text("plugins:\n  enabled: []\n")
            (profile / ".env").write_text(f"API_TOKEN={token}\n")
            (profile / ".env").chmod(0o600)
            generation = ("a" if name.endswith("a") else "b") * 32
            save_state(
                profile,
                {
                    "plugins": {},
                    "hooks": {},
                    "mcp": {},
                    "profile_generation": generation,
                    "revision": 1,
                },
            )
            self.profiles.append(profile)
            self.generations.append(generation)

    def tearDown(self):
        for profile, generation in zip(self.profiles, self.generations):
            try:
                managed_mcp.disconnect_profile_mcp(profile, generation)
            except (RuntimeError, ValueError):
                pass
        self.temporary.cleanup()

    def test_concurrent_profiles_dispatch_refresh_and_unload_independently(self):
        from model_tools import get_tool_definitions
        from tools import mcp_tool
        from tools.registry import registry

        original_start = managed_mcp.ManagedMCPServerTask.start
        original_paginate = mcp_tool._paginate_full_list
        sessions = {}

        async def fake_start(task, config):
            task._config = config
            task.tool_timeout = 5
            tools = [_tool("lookup")]
            task._tools = tools
            task.session = _FixtureSession(config["headers"]["Authorization"], tools)
            task._ready.set()
            task._register_discovered_tools_if_needed()
            sessions[task.name] = task.session

        async def fake_paginate(list_method, _kind, server_id):
            return sessions[server_id].tools

        managed_servers = {
            "docs": {
                "url": "https://docs.example.test/mcp",
                "headers": {"Authorization": "${API_TOKEN}"},
            }
        }
        with (
            patch.object(
                managed_mcp, "public_https", return_value=("docs.example.test", "203.0.113.10")
            ),
            patch.object(mcp_tool, "_ensure_mcp_sdk", return_value=True),
            patch.object(
                mcp_tool, "_filter_suspicious_mcp_servers", side_effect=lambda servers: servers
            ),
            patch.object(managed_mcp.ManagedMCPServerTask, "start", new=fake_start),
            patch.object(mcp_tool, "_paginate_full_list", side_effect=fake_paginate),
            patch.object(mcp_tool, "_trust_gate_check", return_value=None),
        ):
            for profile, generation in zip(self.profiles, self.generations):
                managed_mcp.sync_profile_mcp(profile, generation, managed_servers)

            scope_a = managed_mcp._profile_scope(self.profiles[0])
            scope_b = managed_mcp._profile_scope(self.profiles[1])
            self.assertNotEqual(scope_a, scope_b)
            tools_a = managed_mcp.profile_mcp_toolsets(self.profiles[0], self.generations[0])
            tools_b = managed_mcp.profile_mcp_toolsets(self.profiles[1], self.generations[1])
            self.assertEqual(len(tools_a), 1)
            self.assertEqual(len(tools_b), 1)

            schemas = []
            for profile, toolsets in zip(self.profiles, (tools_a, tools_b)):
                with profile_context(profile):
                    schemas.append(
                        get_tool_definitions(
                            enabled_toolsets=list(toolsets),
                            quiet_mode=True,
                            skip_tool_search_assembly=True,
                        )
                    )
            self.assertEqual(len(schemas[0]), 1)
            self.assertEqual(len(schemas[1]), 1)
            self.assertNotEqual(
                schemas[0][0]["function"]["name"], schemas[1][0]["function"]["name"]
            )

            agents = [
                SimpleNamespace(
                    tools=[{"type": "function", "function": {"name": "todo"}}],
                    valid_tool_names={"todo"},
                    enabled_toolsets=["todo"],
                )
                for _profile in self.profiles
            ]
            added = [
                managed_mcp.install_tools(agent, profile)
                for agent, profile in zip(agents, self.profiles)
            ]
            self.assertEqual(
                added,
                [[schemas[0][0]["function"]["name"]], [schemas[1][0]["function"]["name"]]],
            )
            for agent, names, toolsets in zip(agents, added, (tools_a, tools_b)):
                self.assertEqual(
                    [tool["function"]["name"] for tool in agent.tools], ["todo", *names]
                )
                self.assertEqual(agent.valid_tool_names, {"todo", *names})
                self.assertEqual(set(agent.enabled_toolsets), {"todo", *toolsets})
            self.assertEqual(managed_mcp.install_tools(agents[0], self.profiles[0]), [])

            tasks = []
            for profile, generation in zip(self.profiles, self.generations):
                scope = managed_mcp._profile_scope(profile)
                with managed_mcp._MANAGER_LOCK:
                    tasks.append(
                        next(
                            task
                            for key, task in managed_mcp._MANAGED_TASKS.items()
                            if key[0] == scope and key[1] == generation
                        )
                    )
            self.assertEqual(
                [task.session.credential for task in tasks],
                ["token-a", "token-b"],
            )

            entry_names = [task._registered_tool_names[0] for task in tasks]
            entries = [
                registry.get_entry(name, scope=task.profile_scope)
                for name, task in zip(entry_names, tasks)
            ]
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(
                    pool.map(
                        lambda entry: entry.handler({}, context=None),
                        entries,
                    )
                )
            self.assertEqual(
                [json.loads(response)["result"] for response in responses],
                ["token-a", "token-b"],
            )

            task_a = tasks[0]
            task_a.session.tools = [_tool("lookup"), _tool("search")]
            mcp_tool._ensure_mcp_loop()
            mcp_tool._run_on_mcp_loop(lambda: task_a._refresh_tools(), timeout=10)
            self.assertEqual(len(task_a._registered_tool_names), 2)
            with profile_context(self.profiles[1]):
                self.assertIsNone(registry.get_entry(task_a._registered_tool_names[-1]))

            managed_mcp.disconnect_profile_mcp(self.profiles[0], self.generations[0], ["docs"])
            self.assertEqual(
                managed_mcp.profile_mcp_status(self.profiles[0], self.generations[0]),
                {},
            )
            with profile_context(self.profiles[1]):
                self.assertIsNotNone(registry.get_entry(entry_names[1]))
            response_b = entries[1].handler({}, context=None)
            self.assertEqual(json.loads(response_b)["result"], "token-b")

        self.assertIsNotNone(original_start)
        self.assertIsNotNone(original_paginate)

    def test_native_start_registers_initial_tools_in_profile_scope(self):
        from tools import mcp_tool
        from tools.registry import registry

        async def fake_run_http(task, config):
            task.initialize_result = SimpleNamespace(
                capabilities=SimpleNamespace(tools=SimpleNamespace())
            )
            task.session = _FixtureSession(config["headers"]["Authorization"], [_tool("lookup")])
            await task._discover_tools()
            task._ready.set()
            task._mark_lifecycle_started()
            return await task._wait_for_lifecycle_event()

        managed_servers = {
            "docs": {
                "url": "https://docs.example.test/mcp",
                "headers": {"Authorization": "${API_TOKEN}"},
            }
        }
        profile_a, profile_b = self.profiles
        with (
            patch.object(
                managed_mcp,
                "public_https",
                return_value=("docs.example.test", "203.0.113.10"),
            ),
            patch.object(mcp_tool, "_ensure_mcp_sdk", return_value=True),
            patch.object(
                mcp_tool,
                "_filter_suspicious_mcp_servers",
                side_effect=lambda servers: servers,
            ),
            patch.object(mcp_tool, "_validate_remote_mcp_url", return_value=None),
            patch.object(managed_mcp.ManagedMCPServerTask, "_run_http", new=fake_run_http),
        ):
            result = managed_mcp.sync_profile_mcp(profile_a, self.generations[0], managed_servers)
            self.assertEqual(result, {"connected": ["docs"]})
            status = managed_mcp.profile_mcp_status(profile_a, self.generations[0])
            self.assertEqual(status["docs"]["status"], "connected")
            self.assertEqual(status["docs"]["tools_count"], 1)

            agent_a = SimpleNamespace(tools=[], valid_tool_names=set(), enabled_toolsets=[])
            agent_b = SimpleNamespace(tools=[], valid_tool_names=set(), enabled_toolsets=[])
            (name,) = managed_mcp.install_tools(agent_a, profile_a)
            self.assertEqual(managed_mcp.install_tools(agent_b, profile_b), [])
            self.assertEqual(agent_b.tools, [])
            with profile_context(profile_a):
                self.assertIsNotNone(registry.get_entry(name))
            with profile_context(profile_b):
                self.assertIsNone(registry.get_entry(name))
            # Never published to the process-global registry.
            self.assertNotIn(name, registry._tools)

            managed_mcp.disconnect_profile_mcp(profile_a, self.generations[0])
            with profile_context(profile_a):
                self.assertIsNone(registry.get_entry(name))

    def test_stale_profile_generation_cannot_reconnect(self):
        profile = self.profiles[0]
        stale_generation = self.generations[0]
        state = load_state(profile)
        state["profile_generation"] = "c" * 32
        save_state(profile, state)
        with self.assertRaisesRegex(ValueError, "generation changed"):
            managed_mcp.sync_profile_mcp(profile, stale_generation, {})


if __name__ == "__main__":
    unittest.main()
