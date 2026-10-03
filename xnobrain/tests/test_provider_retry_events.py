"""Retry progress is scoped, safe, immediate, and does not change recovery."""

from __future__ import annotations

import io
import json
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import httpx
from openai import OpenAI

from xnobrain.integrations.provider_retry_events import (
    _on_api_error,
    observe_provider_retries,
    retry_event_fields,
)


class ProviderRetryEventsTests(unittest.TestCase):
    def setUp(self):
        home = TemporaryDirectory()
        self.addCleanup(home.cleanup)
        env = patch.dict(os.environ, {"HERMES_HOME": home.name})
        env.start()
        self.addCleanup(env.stop)
        self.payload = {
            "session_id": "one",
            "provider": "custom:xnobrain",
            "model": "ag/gemini-test",
            "retryable": True,
            "retry_count": 0,
            "max_retries": 3,
            "status_code": 502,
            "error": {"message": "Bearer secret"},
            "request": {"messages": ["private prompt"]},
        }

    def test_registration_is_idempotent_and_projection_excludes_payloads(self):
        from hermes_cli import lifecycle
        from hermes_cli.plugins import get_plugin_manager

        callback = Mock()
        with observe_provider_retries("one", callback):
            with observe_provider_retries("one", callback):
                lifecycle.invoke_hook("api_request_error", **self.payload)
        callback.assert_called_once_with("provider.retrying", status_code=502)
        self.assertEqual(
            get_plugin_manager().iter_hook_callbacks("api_request_error").count(_on_api_error),
            1,
        )
        _on_api_error(**self.payload)
        self.assertEqual(callback.call_count, 1)
        for value in ("502", True, 200, 600, None):
            self.assertEqual(retry_event_fields(status_code=value), {})

    def test_scope_and_nonretryable_errors_are_ignored(self):
        callback = Mock()
        with observe_provider_retries("one", callback):
            for overrides in (
                {"session_id": "other"},
                {"session_id": ""},
                {"provider": "custom:other"},
                {"model": "cx/test"},
                {"retryable": False},
                {"retry_count": 2},
                {"retry_count": -1},
                {"max_retries": None},
            ):
                _on_api_error(**{**self.payload, **overrides})
        callback.assert_not_called()

    def test_concurrent_workers_and_exception_cleanup(self):
        callbacks = {name: Mock() for name in ("one", "two")}
        barrier = threading.Barrier(2)

        def worker(name):
            with self.assertRaisesRegex(RuntimeError, "synthetic"):
                with observe_provider_retries(name, callbacks[name]):
                    barrier.wait(timeout=5)
                    _on_api_error(**{**self.payload, "session_id": name})
                    raise RuntimeError("synthetic")
            _on_api_error(**{**self.payload, "session_id": name})

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(worker, callbacks))
        for callback in callbacks.values():
            callback.assert_called_once_with("provider.retrying", status_code=502)

    def test_engine_emits_notice_before_retry_and_still_recovers(self):
        from run_agent import AIAgent

        from xnobrain.integrations.conversation_runner import ConversationRunnerMixin

        agent = AIAgent(
            base_url="https://example.test/v1",
            api_key="synthetic",
            provider="custom:xnobrain",
            model="ag/gemini-test",
            enabled_toolsets=[],
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            skip_background_review=True,
            stream_delta_callback=lambda _delta: None,
        )
        ConversationRunnerMixin._install_provider_runtime_request_guard(agent)
        callback = Mock()
        calls = []

        def respond(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(
                    502,
                    json={"error": {"message": "all credentials failed"}},
                )
            callback.assert_called_once_with("provider.retrying", status_code=502)
            chunk = {
                "id": "synthetic",
                "object": "chat.completion.chunk",
                "model": "ag/gemini-test",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": "Recovered"},
                        "finish_reason": "stop",
                    }
                ],
            }
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode(),
            )

        def create_client(*_args, **_kwargs):
            return OpenAI(
                base_url="https://example.test/v1",
                api_key="synthetic",
                max_retries=0,
                http_client=httpx.Client(transport=httpx.MockTransport(respond)),
            )

        agent._create_request_openai_client = create_client
        with (
            observe_provider_retries(agent.session_id, callback),
            patch("agent.conversation_loop.jittered_backoff", return_value=0),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            result = agent.run_conversation("synthetic test")
        callback.assert_called_once_with("provider.retrying", status_code=502)
        self.assertEqual(len(calls), 2, result)
        self.assertEqual(result["final_response"], "Recovered")
        self.assertFalse(result.get("failed"))
