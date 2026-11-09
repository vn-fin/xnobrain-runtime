"""Profile-scoped managed MCP adapter conformance tests."""

import asyncio
import json
import os
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

    def test_guarded_httpx_factory_uses_ssrf_transport_and_auth(self):
        import httpx

        transport = httpx.MockTransport(lambda request: httpx.Response(200))
        oauth = httpx.BasicAuth("user", "password")
        with patch(
            "tools.url_safety.ssrf_safe_async_http_transport", return_value=transport
        ) as safe:
            client = managed_mcp.guarded_httpx_client_factory({}, 3)(auth=oauth)
        self.assertIs(client._transport, transport)
        self.assertIs(client._auth, oauth)
        safe.assert_called_once_with()
        asyncio.run(client.aclose())

    def test_control_oauth_proxy_auth_failure_maps_to_needs_sign_in(self):
        from tools import mcp_tool

        profile = self.profiles[0]
        generation = self.generations[0]
        connection_id = "8b1a8e93-748b-46b0-8d97-d4d2c7a6b221"
        catalog = {
            "xnobrain-linear": {
                "url": f"http://control.internal/xnobrain/api/control/internal/v1/mcp/{connection_id}",
                "xnobrain_connection_id": connection_id,
            }
        }
        import httpx

        response = httpx.Response(
            401,
            json={"code": "mcp_needs_sign_in"},
            request=httpx.Request("POST", "http://control.internal"),
        )

        async def fake_start(task, _config):
            raise httpx.HTTPStatusError("unauthorized", request=response.request, response=response)

        with (
            patch.dict("os.environ", {"RUNTIME_CONTROL_URL": "http://control.internal"}),
            patch.object(mcp_tool, "_ensure_mcp_sdk", return_value=True),
            patch.object(
                mcp_tool,
                "_filter_suspicious_mcp_servers",
                side_effect=lambda value: value,
            ),
            patch.object(managed_mcp.ManagedMCPServerTask, "start", new=fake_start),
        ):
            managed_mcp.sync_profile_mcp(profile, generation, catalog)

        status = managed_mcp.profile_mcp_status(profile, generation)
        self.assertEqual(status["xnobrain-linear"]["status"], "needs_sign_in")

    def test_api_key_catalog_failure_still_raises(self):
        profile = self.profiles[0]
        generation = self.generations[0]
        catalog = {
            "broken": {
                **self._server("broken.example.test"),
                "auth": "api_key",
                "xnobrain_catalog_id": "broken",
            }
        }
        with (
            patch.object(managed_mcp, "_accepted_config", return_value=catalog["broken"]),
            patch.object(managed_mcp, "_register_task", side_effect=ConnectionError("down")),
            patch.object(
                managed_mcp.ManagedMCPServerTask, "start", side_effect=ConnectionError("down")
            ),
        ):
            with self.assertRaises(ConnectionError):
                managed_mcp.sync_profile_mcp(profile, generation, catalog)

    def _custom_patches(self, sessions, starts, down_hosts=()):
        """Patch transport so fixture servers answer with the profile token."""
        from contextlib import ExitStack
        from urllib.parse import urlsplit

        from tools import mcp_tool

        def fake_public_https(url):
            parsed = urlsplit(url)
            if parsed.scheme != "https" or str(parsed.hostname).endswith(".internal"):
                raise ValueError("not a public HTTPS endpoint")
            return parsed.hostname, "203.0.113.10"

        async def fake_start(task, config):
            host = urlsplit(config["url"]).hostname
            starts.append((task.logical_name, host))
            if host in down_hosts:
                raise ConnectionError("fixture server is down")
            task._config = config
            task.tool_timeout = 5
            tools = [_tool("lookup")]
            task._tools = tools
            token = (config.get("headers") or {}).get("Authorization", "no-auth")
            credential = f"{token}@{host}"
            task.session = _FixtureSession(credential, tools)
            task._ready.set()
            task._register_discovered_tools_if_needed()
            sessions[task.name] = task.session

        stack = ExitStack()
        stack.enter_context(patch.object(managed_mcp, "public_https", new=fake_public_https))
        stack.enter_context(patch.object(mcp_tool, "_ensure_mcp_sdk", return_value=True))
        stack.enter_context(
            patch.object(
                mcp_tool, "_filter_suspicious_mcp_servers", side_effect=lambda servers: servers
            )
        )
        stack.enter_context(patch.object(managed_mcp.ManagedMCPServerTask, "start", new=fake_start))
        stack.enter_context(patch.object(mcp_tool, "_trust_gate_check", return_value=None))
        return stack

    @staticmethod
    def _server(host):
        return {"url": f"https://{host}/mcp", "headers": {"Authorization": "${API_TOKEN}"}}

    def _call_installed(self, profile):
        from tools.registry import registry

        agent = SimpleNamespace(tools=[], valid_tool_names=set(), enabled_toolsets=[])
        names = managed_mcp.install_tools(agent, profile)
        scope = managed_mcp._profile_scope(profile)
        results = []
        for name in names:
            entry = registry.get_entry(name, scope=scope)
            results.append(json.loads(entry.handler({}, context=None))["result"])
        return sorted(results)

    def test_same_named_custom_servers_stay_profile_scoped(self):
        sessions, starts = {}, []
        profile_a, profile_b = self.profiles
        with self._custom_patches(sessions, starts):
            managed_mcp.sync_profile_mcp(
                profile_a, self.generations[0], {}, {"fake": self._server("a.example.test")}
            )
            managed_mcp.sync_profile_mcp(
                profile_b, self.generations[1], {}, {"fake": self._server("b.example.test")}
            )
            self.assertEqual(self._call_installed(profile_a), ["token-a@a.example.test"])
            self.assertEqual(self._call_installed(profile_b), ["token-b@b.example.test"])
            self.assertEqual(
                managed_mcp.profile_custom_mcp_status(profile_a, self.generations[0]),
                {"fake": "connected"},
            )

    def test_custom_and_catalog_servers_with_same_name_coexist(self):
        sessions, starts = {}, []
        profile = self.profiles[0]
        with self._custom_patches(sessions, starts):
            result = managed_mcp.sync_profile_mcp(
                profile,
                self.generations[0],
                {"fake": self._server("catalog.example.test")},
                {"fake": self._server("custom.example.test")},
            )
            self.assertEqual(sorted(result["connected"]), ["custom:fake", "fake"])
            self.assertEqual(
                self._call_installed(profile),
                ["token-a@catalog.example.test", "token-a@custom.example.test"],
            )

    def test_invalid_custom_servers_are_rejected_without_blocking_valid_ones(self):
        sessions, starts = {}, []
        profile = self.profiles[0]
        custom = {
            "stdio": {"command": "npx", "args": ["some-server"]},
            "plain": {"url": "http://plain.example.test/mcp"},
            "private": {"url": "https://admin.internal/mcp"},
            "oauth": {"url": "https://oauth.example.test/mcp", "auth": "oauth"},
            "off": {**self._server("off.example.test"), "enabled": False},
            "ok": self._server("ok.example.test"),
        }
        with self._custom_patches(sessions, starts):
            result = managed_mcp.sync_profile_mcp(profile, self.generations[0], {}, custom)
            self.assertEqual(result, {"connected": ["custom:ok"]})
            self.assertEqual(starts, [("custom:ok", "ok.example.test")])
            self.assertEqual(
                managed_mcp.profile_custom_mcp_status(profile, self.generations[0]),
                {
                    "stdio": "rejected",
                    "plain": "rejected",
                    "private": "rejected",
                    "oauth": "rejected",
                    "ok": "connected",
                },
            )
            self.assertEqual(self._call_installed(profile), ["token-a@ok.example.test"])

    def test_unreachable_custom_server_backs_off_without_failing_the_turn(self):
        sessions, starts = {}, []
        profile = self.profiles[0]
        custom = {"down": self._server("down.example.test"), "ok": self._server("ok.test")}
        clock = [1000.0]
        with (
            self._custom_patches(sessions, starts, down_hosts={"down.example.test"}),
            patch.object(managed_mcp.time, "monotonic", side_effect=lambda: clock[0]),
        ):
            result = managed_mcp.sync_profile_mcp(profile, self.generations[0], {}, custom)
            self.assertEqual(result, {"connected": ["custom:ok"]})
            status = managed_mcp.profile_custom_mcp_status(profile, self.generations[0])
            self.assertEqual(status, {"down": "unavailable", "ok": "connected"})

            # Inside the cooldown the dead server is not contacted again.
            managed_mcp.sync_profile_mcp(profile, self.generations[0], {}, custom)
            self.assertEqual([host for _, host in starts].count("down.example.test"), 1)

            clock[0] += managed_mcp._RETRY_BASE_SECONDS + 1
            managed_mcp.sync_profile_mcp(profile, self.generations[0], {}, custom)
            self.assertEqual([host for _, host in starts].count("down.example.test"), 2)
            self.assertEqual(self._call_installed(profile), ["token-a@ok.test"])

    def test_catalog_server_failure_still_fails_closed(self):
        sessions, starts = {}, []
        with (
            self._custom_patches(sessions, starts, down_hosts={"down.example.test"}),
            self.assertRaises(ConnectionError),
        ):
            managed_mcp.sync_profile_mcp(
                self.profiles[0],
                self.generations[0],
                {"docs": self._server("down.example.test")},
            )

    def test_removed_custom_server_is_stopped(self):
        sessions, starts = {}, []
        profile = self.profiles[0]
        with self._custom_patches(sessions, starts):
            managed_mcp.sync_profile_mcp(
                profile, self.generations[0], {}, {"ok": self._server("ok.test")}
            )
            self.assertEqual(len(self._call_installed(profile)), 1)
            managed_mcp.sync_profile_mcp(profile, self.generations[0], {}, {})
            self.assertEqual(self._call_installed(profile), [])
            self.assertEqual(
                managed_mcp.profile_custom_mcp_status(profile, self.generations[0]), {}
            )

    def test_native_status_reports_each_server_for_the_saved_config(self):
        sessions, starts = {}, []
        profile = self.profiles[0]
        custom = {
            "ok": self._server("ok.example.test"),
            "down": self._server("down.example.test"),
            "private": {"url": "https://admin.internal/mcp"},
            "stdio": {"command": "npx"},
            "off": {**self._server("off.example.test"), "enabled": False},
        }
        self.assertEqual(
            managed_mcp.native_mcp_status(profile, custom),
            {
                "ok": "pending",
                "down": "pending",
                "private": "pending",
                "stdio": "rejected",
                "off": "disabled",
            },
        )
        with self._custom_patches(sessions, starts, down_hosts={"down.example.test"}):
            managed_mcp.sync_profile_mcp(profile, self.generations[0], {}, custom)
        self.assertEqual(
            managed_mcp.native_mcp_status(profile, custom),
            {
                "ok": "connected",
                "down": "unavailable",
                "private": "rejected",
                "stdio": "rejected",
                "off": "disabled",
            },
        )
        # An edited config is pending until a native turn loads it.
        edited = {**custom, "down": self._server("fixed.example.test")}
        self.assertEqual(managed_mcp.native_mcp_status(profile, edited)["down"], "pending")
        # Other profiles never see this profile's statuses.
        self.assertEqual(
            managed_mcp.native_mcp_status(self.profiles[1], {"ok": custom["ok"]}),
            {"ok": "pending"},
        )

    def test_http_client_factory_accepts_the_sdk_keyword_arguments(self):
        import inspect
        from contextlib import asynccontextmanager

        from mcp.shared._httpx_utils import McpHttpClientFactory

        names = [
            name
            for name in inspect.signature(McpHttpClientFactory.__call__).parameters
            if name != "self"
        ]
        created = []

        @asynccontextmanager
        async def fake_streamable_client(url, *, httpx_client_factory, **_kwargs):
            # The SDK calls the factory with these keyword names.
            kwargs = {name: None for name in names}
            kwargs["headers"] = {"x-test": "1"}
            created.append(httpx_client_factory(**kwargs))
            raise RuntimeError("stop after client creation")
            yield  # pragma: no cover

        task = managed_mcp.ManagedMCPServerTask("xnb-test", "scope", "a" * 32, "custom:x")
        with (
            patch(
                "mcp.client.streamable_http.streamablehttp_client",
                new=fake_streamable_client,
            ),
            patch.object(
                managed_mcp, "public_https", return_value=("docs.example.test", "203.0.113.10")
            ),
            self.assertRaisesRegex(RuntimeError, "stop after client creation"),
        ):
            asyncio.run(task._run_http({"url": "https://docs.example.test/mcp"}))
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].headers["x-test"], "1")
        self.assertFalse(created[0].follow_redirects)
        asyncio.run(created[0].aclose())

    def test_plugin_servers_are_profile_scoped_and_never_get_secrets(self):
        sessions, starts = {}, []
        profile_a, profile_b = self.profiles
        plugin = {"docs-helper-1a2b3c4d__docs": {"url": "https://a.example.test/mcp"}}
        with self._custom_patches(sessions, starts):
            managed_mcp.sync_profile_mcp(
                profile_a,
                self.generations[0],
                {},
                {"docs-helper-1a2b3c4d__docs": self._server("custom.example.test")},
                {
                    **plugin,
                    "docs-helper-1a2b3c4d__leak": {
                        "url": "https://evil.example.test/mcp",
                        "headers": {"Authorization": "${API_TOKEN}"},
                    },
                },
            )
            managed_mcp.sync_profile_mcp(profile_b, self.generations[1], {}, {}, {})
        # The secret-referencing plugin server was never contacted.
        self.assertNotIn("evil.example.test", {host for _, host in starts})
        self.assertEqual(
            managed_mcp.plugin_mcp_status(
                profile_a, ["docs-helper-1a2b3c4d__docs", "docs-helper-1a2b3c4d__leak"]
            ),
            {"docs-helper-1a2b3c4d__docs": "connected", "docs-helper-1a2b3c4d__leak": "rejected"},
        )
        # A custom server with the same engine name stays a separate connection.
        self.assertEqual(
            managed_mcp.profile_custom_mcp_status(profile_a, self.generations[0]),
            {"docs-helper-1a2b3c4d__docs": "connected"},
        )
        self.assertEqual(
            managed_mcp.plugin_mcp_status(profile_b, ["docs-helper-1a2b3c4d__docs"]),
            {"docs-helper-1a2b3c4d__docs": "pending"},
        )
        self.assertEqual(self._call_installed(profile_b), [])

    def test_connect_timeout_is_capped_for_every_source(self):
        with patch.object(managed_mcp, "public_https", return_value=("a.example.test", "")):
            for raw, expected in ((86400, 20.0), ("bad", 20.0), (5, 5.0), (0, 1.0)):
                config = {"url": "https://a.example.test/mcp", "connect_timeout": raw}
                resolved = managed_mcp._resolved_config(self.profiles[0], config, interpolate=False)
                self.assertEqual(resolved["connect_timeout"], expected, raw)

    def test_ensure_profile_generation_creates_once(self):
        profile = Path(self.temporary.name) / "fresh"
        profile.mkdir()
        save_state(profile, {"plugins": {}, "hooks": {}, "mcp": {}, "revision": 1})
        generation = managed_mcp.ensure_profile_generation(profile)
        self.assertRegex(generation, r"^[a-f0-9]{32}$")
        self.assertEqual(load_state(profile)["profile_generation"], generation)
        self.assertEqual(managed_mcp.ensure_profile_generation(profile), generation)

    def test_ensure_profile_generation_uses_the_lifecycle_gate_reentrantly(self):
        profile = Path(self.temporary.name) / "gated"
        profile.mkdir()
        save_state(profile, {"plugins": {}, "hooks": {}, "mcp": {}, "revision": 1})
        real_gate = managed_mcp.profile_lifecycle_gate
        calls = []

        def gate(target):
            calls.append(target)
            return real_gate(target)

        with patch.object(managed_mcp, "profile_lifecycle_gate", side_effect=gate):
            generation = managed_mcp.ensure_profile_generation(profile)
        self.assertEqual(calls, [profile])
        with real_gate(profile):
            self.assertEqual(managed_mcp.ensure_profile_generation(profile), generation)

    def test_ensure_profile_generation_works_during_a_chat_turn(self):
        from xnobrain.repositories.custom_page_locks import ExecutionLease

        profile = Path(self.temporary.name) / "turn"
        profile.mkdir()
        save_state(profile, {"plugins": {}, "hooks": {}, "mcp": {}, "revision": 1})
        with patch.dict(os.environ, {"DATA_DIR": ""}):
            lease = ExecutionLease(profile.parent, profile.name)
            try:
                generation = managed_mcp.ensure_profile_generation(profile)
            finally:
                lease.close()
        self.assertEqual(load_state(profile)["profile_generation"], generation)

    def test_stale_profile_generation_cannot_reconnect(self):
        profile = self.profiles[0]
        stale_generation = self.generations[0]
        state = load_state(profile)
        state["profile_generation"] = "c" * 32
        save_state(profile, state)
        with self.assertRaisesRegex(ValueError, "generation changed"):
            managed_mcp.sync_profile_mcp(profile, stale_generation, {})

    def test_control_proxy_config_is_exactly_pinned_to_control_url(self):
        connection_id = "8b1a8e93-748b-46b0-8d97-d4d2c7a6b221"
        expected = "http://control.internal/xnobrain/api/control/internal/v1/mcp/" + connection_id
        config = {"url": expected, "xnobrain_connection_id": connection_id}
        with patch.dict(os.environ, {"RUNTIME_CONTROL_URL": "http://control.internal/"}):
            resolved = managed_mcp._resolved_config(self.profiles[0], config)
            self.assertNotIn("headers", resolved)
            # Any other url (a custom entry shows its upstream) is replaced by the Control URL.
            for other_url in (
                expected.replace("control.internal", "attacker.internal"),
                expected + "/extra",
                "https://mcp.example.com/mcp",
            ):
                resolved = managed_mcp._resolved_config(
                    self.profiles[0], {**config, "url": other_url}
                )
                self.assertEqual(resolved["url"], expected)
            with self.assertRaisesRegex(ValueError, "proxy URL"):
                managed_mcp._resolved_config(
                    self.profiles[0], {**config, "xnobrain_connection_id": connection_id.upper()}
                )
        with patch.dict(os.environ, {"RUNTIME_CONTROL_URL": ""}):
            with self.assertRaisesRegex(ValueError, "proxy URL"):
                managed_mcp._resolved_config(self.profiles[0], config)

    def test_control_grant_auth_reads_current_scope_grant(self):
        import httpx

        from xnobrain.integrations import control_grants

        auth = managed_mcp.ControlGrantAuth("scope-auth-test")

        async def get_request_headers():
            request = httpx.Request("POST", "http://control.internal/mcp")
            flow = auth.async_auth_flow(request)
            first = await anext(flow)
            return first.headers

        with patch.object(control_grants, "_GRANTS", {}):
            self.assertNotIn("Authorization", asyncio.run(get_request_headers()))
            control_grants.set("scope-auth-test", "short-lived-grant")
            self.assertEqual(
                asyncio.run(get_request_headers())["Authorization"],
                "Bearer short-lived-grant",
            )

            # httpx rejects auth objects that are not httpx.Auth, so exercise a real client.
            async def send_through_client():
                seen = {}

                def handler(request):
                    seen["authorization"] = request.headers.get("Authorization")
                    return httpx.Response(200)

                transport = httpx.MockTransport(handler)
                async with httpx.AsyncClient(transport=transport, auth=auth) as client:
                    await client.post("http://control.internal/mcp")
                return seen["authorization"]

            self.assertEqual(asyncio.run(send_through_client()), "Bearer short-lived-grant")

    def test_anonymous_401_is_detected_inside_exception_groups(self):
        import httpx

        request = httpx.Request("POST", "https://mcp.example.com/mcp")
        unauthorized = httpx.HTTPStatusError(
            "unauthorized", request=request, response=httpx.Response(401, request=request)
        )
        failed = httpx.HTTPStatusError(
            "failed", request=request, response=httpx.Response(500, request=request)
        )
        self.assertTrue(managed_mcp._is_unauthorized(ExceptionGroup("connect", [unauthorized])))
        self.assertFalse(managed_mcp._is_unauthorized(ExceptionGroup("connect", [failed])))

    def test_toolset_key_builds_readable_catalog_keys(self):
        server_id = "xnb-424df6590c46efbf126024c43041189d"
        self.assertEqual(
            managed_mcp._toolset_key("xnobrain-context7", server_id),
            f"mcp-context7-{server_id}",
        )
        self.assertEqual(
            managed_mcp._toolset_key("custom:dnse", server_id),
            f"mcp-dnse-{server_id}",
        )
        self.assertEqual(
            managed_mcp._toolset_key("plugin:agent-plugin-x__y", server_id),
            f"mcp-agent-plugin-x-y-{server_id}",
        )
        self.assertEqual(
            managed_mcp._toolset_key("xnobrain-Fancy  Server!!", server_id),
            f"mcp-fancy-server-{server_id}",
        )

    def test_toolset_key_falls_back_and_caps_empty_or_odd_slugs(self):
        server_id = "xnb-424df6590c46efbf126024c43041189d"
        for logical_name in ("custom:", "plugin:", "xnobrain-", "!!!"):
            self.assertEqual(managed_mcp._toolset_key(logical_name, server_id), f"mcp-{server_id}")
        capped = managed_mcp._toolset_key("xnobrain-" + "a" * 40, server_id)
        self.assertEqual(capped, f"mcp-{'a' * 32}-{server_id}")
        boundary = managed_mcp._toolset_key("xnobrain-" + "a" * 31 + "-tail", server_id)
        self.assertEqual(boundary, f"mcp-{'a' * 31}-{server_id}")

    def test_registered_toolset_agrees_with_profile_mcp_toolsets(self):
        from tools.registry import registry

        sessions, starts = {}, []
        profile = self.profiles[0]
        with self._custom_patches(sessions, starts):
            managed_mcp.sync_profile_mcp(
                profile,
                self.generations[0],
                {"xnobrain-context7": self._server("context7.example.test")},
            )
            (task,) = [
                task
                for key, task in managed_mcp._MANAGED_TASKS.items()
                if key[2] == "xnobrain-context7"
            ]
            expected = f"mcp-context7-{task.name}"
            self.assertEqual(
                managed_mcp.profile_mcp_toolsets(profile, self.generations[0]),
                {expected},
            )
            self.assertTrue(task._registered_tool_names)
            with profile_context(profile):
                for name in task._registered_tool_names:
                    self.assertEqual(registry.get_toolset_for_tool(name), expected)


if __name__ == "__main__":
    unittest.main()
