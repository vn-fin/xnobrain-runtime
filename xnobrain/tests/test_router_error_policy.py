"""Explicit routing rejection must terminate without masking transient failures."""

from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import httpx
from openai import InternalServerError, OpenAI

from xnobrain.integrations.router_error_policy import (
    install_provider_retry_progress,
    install_router_error_policy,
)


class RouterErrorPolicyTests(unittest.TestCase):
    def test_retry_notice_precedes_each_retry_and_stops_after_five(self):
        from run_agent import AIAgent

        from xnobrain.integrations.conversation_runner import ConversationRunnerMixin

        with TemporaryDirectory() as home, patch.dict(os.environ, {"HERMES_HOME": home}):
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
            events = []
            progress = lambda event, **data: events.append((event, data))
            install_provider_retry_progress(agent, progress)
            install_provider_retry_progress(agent, progress)
            calls = []

            def respond(request):
                self.assertEqual(len(events), len(calls))
                calls.append(request)
                return httpx.Response(502, json={"error": {"message": "private provider text"}})

            def request_client(**_kwargs):
                return OpenAI(
                    base_url="https://example.test/v1",
                    api_key="synthetic",
                    max_retries=0,
                    http_client=httpx.Client(transport=httpx.MockTransport(respond)),
                )

            with (
                patch("agent.conversation_loop.jittered_backoff", return_value=0),
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
            ):
                agent._create_request_openai_client = request_client
                result = agent.run_conversation("synthetic test")

            self.assertEqual(len(calls), 6, (result, events))
            self.assertTrue(result["failed"])
            self.assertEqual(
                events,
                [
                    (
                        "provider.retrying",
                        {"status_code": 502, "retry_attempt": attempt, "max_retries": 5},
                    )
                    for attempt in range(1, 6)
                ],
            )
            self.assertNotIn("private provider text", str(events))

    def test_chat_run_returns_failed_after_one_request_to_an_unusable_route(self):
        from run_agent import AIAgent

        from xnobrain.integrations.conversation_runner import ConversationRunnerMixin

        for status, code, message in (
            (503, "no_credentials", "no healthy credentials available"),
            (401, "provider_authentication_required", "Provider authentication failed. Reconnect."),
            (
                200,
                "provider_authentication_required",
                "Provider authentication failed (HTTP 401). Reconnect.",
            ),
        ):
            with (
                self.subTest(status=status),
                TemporaryDirectory() as home,
                patch.dict(os.environ, {"HERMES_HOME": home}),
            ):
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
                progress = Mock()
                install_provider_retry_progress(agent, progress)
                calls = []

                def respond(request, *, status=status, code=code, message=message, calls=calls):
                    calls.append(request)
                    body = {"error": {"code": code, "message": message}}
                    if status == 200:
                        return httpx.Response(
                            200,
                            headers={"content-type": "text/event-stream"},
                            content=f"data: {json.dumps(body)}\n\ndata: [DONE]\n\n".encode(),
                        )
                    return httpx.Response(status, json=body)

                with OpenAI(
                    base_url="https://example.test/v1",
                    api_key="synthetic",
                    max_retries=0,
                    http_client=httpx.Client(transport=httpx.MockTransport(respond)),
                ) as client:
                    agent._create_request_openai_client = Mock(return_value=client)
                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        result = agent.run_conversation("synthetic test")
                self.assertEqual(len(calls), 1)
                self.assertTrue(result["failed"])
                self.assertIn(message, result["error"])
                progress.assert_not_called()

    def test_engine_stops_unusable_route_but_preserves_status_and_transient_retries(self):
        from agent.error_classifier import classify_api_error
        from hermes_cli.plugins import get_plugin_manager

        with TemporaryDirectory() as home, patch.dict(os.environ, {"HERMES_HOME": home}):
            install_router_error_policy()
            install_router_error_policy()
            manager = get_plugin_manager()
            self.assertEqual(
                len(manager.iter_hook_callbacks("transform_api_error_classification")),
                1,
            )
            for provider, code, retryable in (
                ("custom:xnobrain", "no_credentials", False),
                ("xnobrain", "no_credentials", False),
                ("custom:xnobrain", "service_unavailable", True),
                ("custom:other", "no_credentials", True),
            ):
                with self.subTest(provider=provider, code=code):
                    response = httpx.Response(
                        503,
                        request=httpx.Request("POST", "https://example.test/v1/chat/completions"),
                    )
                    error = InternalServerError(
                        "no healthy credentials available",
                        response=response,
                        body={
                            "error": {"code": code, "message": "no healthy credentials available"}
                        },
                    )
                    result = classify_api_error(error, provider=provider, model="ag/gemini-test")
                    self.assertEqual(result.retryable, retryable)
                    self.assertEqual(result.status_code, 503)
                    self.assertFalse(result.should_rotate_credential)
                    self.assertFalse(result.should_compress)
                    if not retryable:
                        self.assertFalse(result.should_fallback)
                        self.assertIn("Reconnect", result.message)
