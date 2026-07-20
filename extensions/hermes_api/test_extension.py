from __future__ import annotations

import asyncio
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

try:
    from . import extension
except ModuleNotFoundError as import_error:
    extension = None
    _IMPORT_ERROR = import_error
else:
    _IMPORT_ERROR = None


class _FakeAgent:
    def __init__(self):
        self.calls = []

    def run_conversation(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {"final_response": "ok"}


@unittest.skipIf(extension is None, f"Hermes upstream runtime unavailable: {_IMPORT_ERROR}")
class ExtendedAPIServerAdapterTests(unittest.TestCase):
    def test_skill_categories_keep_metadata_but_not_storage_directories(self):
        calls = []
        skill_manager = SimpleNamespace(
            _resolve_skill_dir=lambda name, category=None: (
                calls.append((name, category))
                or Path("/profiles/agent1/skills") / (category or "") / name
            ),
        )

        extension._install_flat_profile_skill_storage(skill_manager)
        resolved = skill_manager._resolve_skill_dir("google-news-digest", "research")
        extension._install_flat_profile_skill_storage(skill_manager)

        self.assertEqual(resolved, Path("/profiles/agent1/skills/google-news-digest"))
        self.assertEqual(calls, [("google-news-digest", None)])

    def test_external_profile_is_resolved_from_managed_profiles_root(self):
        adapter = object.__new__(extension.ExtendedAPIServerAdapter)
        with tempfile.TemporaryDirectory() as root:
            Path(root, "agent1").mkdir()
            with patch.dict(os.environ, {"HERMES_PROFILES_ROOT": root}):
                resolved = adapter._resolve_request_profile(
                    SimpleNamespace(match_info={"profile": "agent1"})
                )
                rejected = adapter._resolve_request_profile(
                    SimpleNamespace(match_info={"profile": "../root"})
                )

        self.assertEqual(resolved, "agent1")
        self.assertIs(rejected, extension.upstream._PROFILE_REJECTED)

    def test_empty_run_history_is_hydrated_from_profile_session(self):
        adapter = object.__new__(extension.ExtendedAPIServerAdapter)
        adapter._conversation_history_for_session = lambda session_id: [
            {"role": "user", "content": f"history for {session_id}"},
        ]
        agent = _FakeAgent()

        with patch.object(extension.upstream.APIServerAdapter, "_create_agent", return_value=agent):
            created = adapter._create_agent(session_id="session-42")
            created.run_conversation(user_message="next", conversation_history=[])

        self.assertEqual(
            agent.calls[0][1]["conversation_history"],
            [{"role": "user", "content": "history for session-42"}],
        )

    def test_explicit_run_history_is_preserved(self):
        adapter = object.__new__(extension.ExtendedAPIServerAdapter)
        adapter._conversation_history_for_session = lambda _session_id: [
            {"role": "user", "content": "stored"},
        ]
        agent = _FakeAgent()
        supplied = [{"role": "user", "content": "supplied"}]

        with patch.object(extension.upstream.APIServerAdapter, "_create_agent", return_value=agent):
            created = adapter._create_agent(session_id="session-42")
            created.run_conversation("next", supplied)

        self.assertIs(agent.calls[0][0][1], supplied)

    def test_async_run_history_is_resolved_before_agent_call(self):
        async def scenario():
            adapter = object.__new__(extension.ExtendedAPIServerAdapter)

            async def load_history(session_id):
                await asyncio.sleep(0)
                return [{"role": "user", "content": f"history for {session_id}"}]

            adapter._conversation_history_for_session = load_history
            agent = _FakeAgent()
            with patch.object(extension.upstream.APIServerAdapter, "_create_agent", return_value=agent):
                created = adapter._create_agent(session_id="session-async")
                await asyncio.to_thread(
                    created.run_conversation,
                    user_message="next",
                    conversation_history=[],
                )
            return agent

        agent = asyncio.run(scenario())
        self.assertEqual(
            agent.calls[0][1]["conversation_history"],
            [{"role": "user", "content": "history for session-async"}],
        )

    def test_memory_write_approval_blocks_until_run_choice(self):
        from tools.approval import (
            register_gateway_notify,
            reset_current_session_key,
            resolve_gateway_approval,
            set_current_session_key,
            unregister_gateway_notify,
        )

        session_id = "session-memory-approval"
        notified = []
        result = []
        register_gateway_notify(session_id, lambda payload: notified.append(payload))

        def request_approval():
            token = set_current_session_key(session_id)
            try:
                result.append(extension._api_write_approval_callback(
                    "add: preferred report format is PDF",
                    "Save to memory: apply 1 op(s) to user profile",
                    allow_permanent=False,
                ))
            finally:
                reset_current_session_key(token)

        worker = threading.Thread(target=request_approval)
        worker.start()
        try:
            for _ in range(100):
                if notified:
                    break
                worker.join(0.01)

            self.assertEqual(len(notified), 1)
            self.assertEqual(notified[0]["pattern_key"], "memory_write")
            self.assertFalse(notified[0]["allow_permanent"])
            self.assertEqual(resolve_gateway_approval(session_id, "once"), 1)
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(result, ["once"])
        finally:
            unregister_gateway_notify(session_id)


if __name__ == "__main__":
    unittest.main()
