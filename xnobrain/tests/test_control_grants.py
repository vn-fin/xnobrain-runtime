"""In-memory Control grant propagation and reconnect scope tests."""

from __future__ import annotations

import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
