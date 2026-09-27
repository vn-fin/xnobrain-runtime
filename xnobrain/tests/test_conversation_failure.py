"""Synthetic failures use temporary run data only."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from xnobrain.repositories import FileRepository
from xnobrain.services.base import ServiceError
from xnobrain.services.conversation_failure import conversation_failure
from xnobrain.services.conversation_runs import ConversationRunService


class ConversationFailureTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict("os.environ", {"RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.repo = FileRepository(root / "data", root / "profiles")
        self.service = ConversationRunService(self.repo, None, None)
        self.record = {
            "id": "run_one",
            "agent_id": "agent",
            "conversation_id": "session",
            "status": "running",
            "created_at": 1,
            "revision": 0,
        }
        self.repo.put_conversation_run(self.record)

    def test_legacy_error_is_durable_and_late_success_cannot_overwrite(self):
        event = self.service._parse_frame(
            'event: error\ndata: {"error":{"message":"Synthetic failure"}}'
        )
        failed = self.service._append(self.record, event)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error"], "Synthetic failure")
        after = self.service._append(failed, {"data": {"event": "run.completed"}})
        self.assertEqual(after["status"], "failed")
        self.assertEqual(after["revision"], failed["revision"])

    def test_history_projection_and_scoped_pagination(self):
        self.repo.put_conversation_run(
            {
                **self.record,
                "id": "run_two",
                "created_at": 2,
                "status": "failed",
                "error": "token=secret",
                "input": "private prompt",
                "ownership_context": {},
            }
        )
        page = self.service.list_runs("agent", "session", 1)
        self.assertEqual(page["runs"][0]["id"], "run_two")
        self.assertNotIn("input", page["runs"][0])
        self.assertNotIn("ownership_context", page["runs"][0])
        self.assertNotIn("secret", page["runs"][0]["error"])
        second = self.service.list_runs("agent", "session", 1, page["next_cursor"])
        self.assertEqual(second["runs"][0]["id"], "run_one")
        with self.assertRaises(ServiceError):
            self.service.list_runs("other", "session", 1, page["next_cursor"])
        with self.assertRaises(ServiceError):
            self.service.list_runs("agent", "session", 101)

    def test_safe_failure_handles_nested_and_sensitive_values(self):
        self.assertEqual(conversation_failure({"error": {"message": "safe"}}), "safe")
        for value in ("Bearer private", "https://private.local/path", "api_key=private"):
            self.assertNotIn("private", conversation_failure(value))

    def test_history_route_and_lazy_handler_contract(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        from xnobrain.handlers.operations.conversations import operations
        from xnobrain.routes.setup import ROUTES

        self.assertTrue(
            any(
                route.method == "GET" and route.path.endswith("/sessions/{conversation_id}/runs")
                for route in ROUTES
            )
        )
        service = Mock()
        request = SimpleNamespace(
            path_params={"conversation_id": "session"},
            query_params={"agent": "agent", "limit": "2"},
            state=SimpleNamespace(),
            headers={},
        )
        handler = SimpleNamespace(service=service)
        call, _, status = operations(handler, request, {})["conversation_runs_list"]
        call()
        self.assertEqual(status, 200)
        service.list_conversation_runs.assert_called_once_with("agent", "session", 2, "")
