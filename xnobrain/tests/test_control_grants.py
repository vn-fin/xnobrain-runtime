"""In-memory Control grant propagation and reconnect scope tests."""

from __future__ import annotations

import threading
import unittest
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


if __name__ == "__main__":
    unittest.main()
