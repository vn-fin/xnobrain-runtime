"""Tool lifecycle correlation across concurrent calls and engine callbacks."""

from __future__ import annotations

import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

from xnobrain.integrations.conversation_tools import ConversationToolCallbacks


class ConversationToolCallbacksTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events = Mock()
        self.started = Mock()
        self.completed = Mock()
        self.callbacks = ConversationToolCallbacks(self.events, self.started, self.completed)

    def test_parallel_skill_calls_keep_their_ids_and_results(self) -> None:
        barrier = threading.Barrier(2)

        def start(call_id: str, name: str) -> None:
            args = {"name": name}
            self.callbacks.progress("tool.started", "skill_view", name, args)
            barrier.wait(timeout=5)
            self.callbacks.start(call_id, "skill_view", args)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(start, "missing", "missing-skill")
            second = executor.submit(start, "control", "big-brother-control")
            first.result(timeout=5)
            second.result(timeout=5)

        success = {"success": True, "name": "big-brother-control"}
        failure = {"success": False, "error": "Skill not found"}
        for call_id, result, duration in [("control", success, 0.013), ("missing", failure, 0.022)]:
            self.callbacks.progress(
                "tool.completed",
                "skill_view",
                duration=duration,
                is_error=not result["success"],
                result=result,
            )
            self.callbacks.complete(call_id, "skill_view", {}, result)

        events = self.events.call_args_list
        self.assertEqual(len(events), 4)
        starts = {call.kwargs["tool_call_id"]: call.args[2] for call in events[:2]}
        self.assertEqual(starts, {"missing": "missing-skill", "control": "big-brother-control"})
        self.assertEqual(events[2].kwargs["tool_call_id"], "control")
        self.assertFalse(events[2].kwargs["is_error"])
        self.assertEqual(events[2].kwargs["duration"], 0.013)
        self.assertEqual(events[3].kwargs["tool_call_id"], "missing")
        self.assertTrue(events[3].kwargs["is_error"])
        self.assertEqual(self.started.call_count, 2)
        self.assertEqual(self.completed.call_count, 2)

    def test_completion_without_start_and_duplicate_completion_emit_nothing(self) -> None:
        self.callbacks.complete("unknown", "skill_view", {}, {"success": False})
        self.events.assert_not_called()
        self.callbacks.start("one", "skill_view", {"name": "example"})
        self.callbacks.start("one", "skill_view", {"name": "example"})
        self.callbacks.complete("one", "skill_view", {}, {"success": False})
        self.callbacks.complete("one", "skill_view", {}, {"success": True})
        self.assertEqual(self.events.call_count, 2)
        self.assertTrue(self.events.call_args.kwargs["is_error"])

    def test_write_approval_updates_result_before_usage_callback(self) -> None:
        result = {"success": True, "staged": True}

        def emit(event: str, *_args, **kwargs) -> None:
            if event == "tool.completed":
                kwargs["result"]["staged"] = False

        self.events.side_effect = emit
        self.callbacks.start("write", "skill_manage", {"action": "create"})
        self.callbacks.progress("tool.completed", "skill_manage", duration=1, result=result)
        self.callbacks.complete("write", "skill_manage", {}, result)
        self.assertFalse(self.completed.call_args.args[3]["staged"])

    def test_non_lifecycle_progress_is_forwarded(self) -> None:
        self.callbacks.progress("reasoning.delta", "_thinking", "Checking", None)
        self.events.assert_called_once_with("reasoning.delta", "_thinking", "Checking", None)


if __name__ == "__main__":
    unittest.main()
