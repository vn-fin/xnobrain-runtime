"""In-memory Control grant propagation and reconnect scope tests."""

from __future__ import annotations

import asyncio
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from xnobrain.integrations import control_grants, managed_mcp


class ControlGrantTests(unittest.TestCase):
    def test_empty_grant_does_not_clear_existing_grant(self):
        scope = "control-grant-retained"
        with patch.object(control_grants, "_GRANTS", {}):
            control_grants.set(scope, "valid-grant")
            control_grants.set(scope, "")
            self.assertEqual(control_grants.get(scope), "valid-grant")

    def test_new_grant_reconnects_only_parked_control_tasks_in_scope(self):
        scope = "control-grant-scope"
        selected = threading.Event()
        other_scope = threading.Event()
        oauth = threading.Event()
        keys = [
            (scope, "generation", "xnobrain-render"),
            ("other", "generation", "xnobrain-render"),
            (scope, "generation", "xnobrain-oauth"),
        ]
        tasks = [
            SimpleNamespace(_control_proxy=True, _reconnect_event=selected),
            SimpleNamespace(_control_proxy=True, _reconnect_event=other_scope),
            SimpleNamespace(_control_proxy=False, _reconnect_event=oauth),
        ]
        with (
            patch.object(
                managed_mcp,
                "_MANAGED_TASKS",
                dict(zip(keys, tasks)),
            ),
            patch.object(
                managed_mcp,
                "_CUSTOM_STATUS",
                {key: {"status": "needs_sign_in"} for key in keys},
            ),
            patch.object(control_grants, "_GRANTS", {}),
            patch("tools.mcp_tool._mcp_loop", None),
        ):
            control_grants.set(scope, "new-grant")

        self.assertTrue(selected.is_set())
        self.assertFalse(other_scope.is_set())
        self.assertFalse(oauth.is_set())

    def test_run_grant_is_cleared_after_bind_run_exits(self):
        scope = "control-grant-run-exit"
        with (
            patch.object(control_grants, "_GRANTS", {}),
            patch.object(control_grants, "_RUN_GRANTS", {}),
            patch.object(managed_mcp, "_profile_scope", return_value=scope),
            patch.object(managed_mcp, "reconnect_control_tasks"),
        ):
            with control_grants.bind_run(Path("/tmp/profile"), "run-grant"):
                self.assertEqual(control_grants.get(scope), "run-grant")
            self.assertEqual(control_grants.get(scope), "")

    def test_overlapping_runs_keep_other_run_grant(self):
        scope = "control-grant-overlap"
        with (
            patch.object(control_grants, "_GRANTS", {}),
            patch.object(control_grants, "_RUN_GRANTS", {}),
            patch.object(managed_mcp, "_profile_scope", return_value=scope),
            patch.object(managed_mcp, "reconnect_control_tasks"),
        ):
            with control_grants.bind_run(Path("/tmp/profile"), "first-grant"):
                self.assertEqual(control_grants.get(scope), "first-grant")
                with control_grants.bind_run(Path("/tmp/profile"), "second-grant"):
                    self.assertEqual(control_grants.get(scope), "second-grant")
                self.assertEqual(control_grants.get(scope), "first-grant")
            self.assertEqual(control_grants.get(scope), "")

    def test_management_grant_expires_after_ttl(self):
        scope = "control-grant-management-ttl"
        clock = [1000.0]
        with (
            patch.object(control_grants, "_GRANTS", {}),
            patch.object(control_grants, "_RUN_GRANTS", {}),
            patch.object(control_grants.time, "monotonic", side_effect=lambda: clock[0]),
        ):
            control_grants.set(scope, "management-grant")
            self.assertEqual(control_grants.get(scope), "management-grant")
            clock[0] += control_grants._MANAGEMENT_GRANT_TTL_SECONDS + 1
            self.assertEqual(control_grants.get(scope), "")
            self.assertNotIn(scope, control_grants._GRANTS)


class _BlockingSession:
    def __init__(self, entered: asyncio.Event, release: asyncio.Event):
        self.entered = entered
        self.release = release

    async def call_tool(self, name, arguments):
        self.entered.set()
        await self.release.wait()
        return {"name": name}


class ControlGrantRunScopeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.grants = patch.object(control_grants, "_GRANTS", {})
        self.server_grants = patch.object(control_grants, "_SERVER_GRANTS", {})
        self.grants.start()
        self.server_grants.start()
        self.addCleanup(self.grants.stop)
        self.addCleanup(self.server_grants.stop)

    @staticmethod
    def _authorization(auth):
        request = httpx.Request("POST", "http://control.internal/mcp")
        flow = auth.auth_flow(request)
        return next(flow).headers.get("Authorization")

    async def test_concurrent_runs_keep_their_own_server_grant(self):
        first_entered = asyncio.Event()
        second_entered = asyncio.Event()
        release = asyncio.Event()
        first_session = managed_mcp._GrantScopedSession(
            _BlockingSession(first_entered, release), "server-a"
        )
        second_session = managed_mcp._GrantScopedSession(
            _BlockingSession(second_entered, release), "server-b"
        )
        scope = "profile-scope"

        async def run(session, grant, server_key):
            with control_grants.bind_run(Path("/tmp/profile"), grant):
                with control_grants.server_call_scope(
                    server_key, control_grants.current_run_grant()
                ):
                    return await session.call_tool("tool", {})

        first = asyncio.create_task(run(first_session, "grant-first", "server-a"))
        second = asyncio.create_task(run(second_session, "grant-second", "server-b"))
        await asyncio.wait_for(
            asyncio.gather(first_entered.wait(), second_entered.wait()), timeout=2
        )

        self.assertEqual(
            self._authorization(managed_mcp.ControlGrantAuth(scope, "server-a")),
            "Bearer grant-first",
        )
        self.assertEqual(
            self._authorization(managed_mcp.ControlGrantAuth(scope, "server-b")),
            "Bearer grant-second",
        )
        release.set()
        await asyncio.gather(first, second)
        self.assertIsNone(
            managed_mcp.ControlGrantAuth(scope, "server-a")
            .auth_flow(httpx.Request("POST", "http://control.internal/mcp"))
            .__next__()
            .headers.get("Authorization")
        )

    async def test_handler_wrapper_carries_run_grant_to_mcp_worker_thread(self):
        def read_pending():
            return control_grants.pending_server_grant("server-worker")

        handler = managed_mcp._grant_bound_handler(read_pending, "server-worker")
        with control_grants.bind_run(Path("/tmp/profile"), "grant-worker"):
            grant = await asyncio.to_thread(handler)

        self.assertEqual(grant, "grant-worker")
        self.assertIsNone(control_grants.pending_server_grant("server-worker"))

    async def test_bound_empty_run_blocks_profile_grant_fallback(self):
        scope = "profile-scope"
        control_grants.set(scope, "web-grant")
        entered = asyncio.Event()
        release = asyncio.Event()
        session = managed_mcp._GrantScopedSession(
            _BlockingSession(entered, release), "server-empty"
        )

        async def run():
            with control_grants.bind_run(Path("/tmp/profile"), ""):
                with control_grants.server_call_scope(
                    "server-empty", control_grants.current_run_grant()
                ):
                    return await session.call_tool("tool", {})

        task = asyncio.create_task(run())
        await asyncio.wait_for(entered.wait(), timeout=2)
        self.assertEqual(control_grants.get_for_server("server-empty", scope), "")
        self.assertIsNone(self._authorization(managed_mcp.ControlGrantAuth(scope, "server-empty")))
        release.set()
        await task
        self.assertEqual(control_grants.get_for_server("server-empty", scope), "web-grant")


if __name__ == "__main__":
    unittest.main()
