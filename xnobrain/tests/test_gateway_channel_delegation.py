"""Gateway channel delegation: request shape, fallback, typing, and redaction."""

from __future__ import annotations

import asyncio
import logging
import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock

from xnobrain.integrations import gateway_cli

TOKEN = "gateway-channel-test-token"
ENDPOINT = "http://runtime:3000/xnobrain/api/runtime/v1/internal/channels/turn"


class _Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    def __init__(self, factory):
        self.factory = factory

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def post(self, url, json=None, headers=None):
        self.factory.calls.append({"url": url, "json": json, "headers": headers})
        if self.factory.error is not None:
            raise self.factory.error
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return self.factory.response


class _ClientFactory:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    def __call__(self, *args, **kwargs):
        return _Client(self)


class _Adapter:
    def __init__(self):
        self.typing: list[str] = []

    async def send_typing(self, chat_id, metadata=None):
        self.typing.append(chat_id)


def _gateway_module():
    class FakeGatewayRunner:
        _xnobrain_channel_delegation = False
        native_calls: list[tuple] = []

        async def _run_agent_inner(self, *args, **kwargs):
            type(self).native_calls.append((args, kwargs))
            return {
                "final_response": "native reply",
                "messages": [],
                "api_calls": 0,
                "tools": [],
                "history_offset": len(kwargs.get("history") or []),
                "session_id": kwargs.get("session_id"),
                "response_previewed": False,
            }

    module = types.ModuleType("gateway.run")
    module.GatewayRunner = FakeGatewayRunner
    return module, FakeGatewayRunner


def _source(platform="zalo", chat_type="dm"):
    return SimpleNamespace(
        platform=SimpleNamespace(value=platform),
        chat_id="123",
        user_id="456",
        user_name="Nam",
        chat_name="Nam",
        chat_type=chat_type,
        message_id="source-message",
    )


def _envelope(reply="runtime reply"):
    return {
        "success": True,
        "data": {
            "conversation_id": "conv-1",
            "run_id": "run-1",
            "reply": reply,
            "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
        },
        "message": "channel turn completed",
        "status_code": 200,
    }


class GatewayChannelDelegationTests(unittest.IsolatedAsyncioTestCase):
    def install(self, factory):
        module, runner_class = _gateway_module()
        patches = [
            mock.patch.dict(
                sys.modules,
                {"gateway.run": module},
            ),
            mock.patch.dict(
                "os.environ",
                {
                    "RUNTIME_GATEWAY_CHANNEL_DELEGATION": "true",
                    "RUNTIME_INTERNAL_SERVICE_TOKEN": TOKEN,
                    "RUNTIME_INTERNAL_URL": "http://runtime:3000",
                },
            ),
            mock.patch(
                "hermes_constants.get_hermes_home", return_value=SimpleNamespace(name="agent-1")
            ),
            mock.patch("httpx.AsyncClient", factory),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        gateway_cli.install_runtime_channel_delegation()
        return runner_class

    async def test_delegates_authorized_zalo_turn_and_keeps_typing(self):
        factory = _ClientFactory(response=_Response(payload=_envelope()))
        runner_class = self.install(factory)
        runner = runner_class()
        runner.adapter = _Adapter()

        def adapter_for_source(source):
            return runner.adapter

        def thread_metadata(source):
            return {"message_thread_id": "123"}

        runner._adapter_for_source = adapter_for_source
        runner._thread_metadata_for_source = thread_metadata

        result = await runner._run_agent_inner(
            message="hello there",
            context_prompt="",
            history=[{"role": "user"}, {"role": "assistant"}],
            source=_source(),
            session_id="session-1",
            event_message_id="message-1",
        )

        self.assertEqual(result["final_response"], "runtime reply")
        self.assertEqual(result["api_calls"], 1)
        self.assertEqual(result["history_offset"], 2)
        self.assertEqual(result["session_id"], "session-1")
        self.assertEqual(result["messages"][0], {"role": "user", "content": "hello there"})
        self.assertEqual(runner_class.native_calls, [])
        self.assertEqual(len(factory.calls), 1)
        call = factory.calls[0]
        self.assertEqual(call["url"], ENDPOINT)
        self.assertEqual(call["headers"], {"x-xnobrain-internal-token": TOKEN})
        self.assertEqual(
            call["json"],
            {
                "platform": "zalo",
                "agent_id": "agent-1",
                "thread_id": "123",
                "thread_type": "dm",
                "sender_id": "456",
                "sender_name": "Nam",
                "text": "hello there",
                "message_id": "message-1",
                "ts": call["json"]["ts"],
            },
        )
        self.assertTrue(runner.adapter.typing)

    async def test_zalo_bot_group_turn_is_marked_as_group(self):
        factory = _ClientFactory(response=_Response(payload=_envelope()))
        runner_class = self.install(factory)
        runner = runner_class()

        await runner._run_agent_inner(
            message="group message",
            context_prompt="",
            history=[],
            source=_source(platform="zalo_bot", chat_type="GROUP"),
            session_id="session-2",
            event_message_id="message-2",
        )

        self.assertEqual(factory.calls[0]["json"]["platform"], "zalo_bot")
        self.assertEqual(factory.calls[0]["json"]["thread_type"], "group")

    async def test_http_error_never_falls_back_to_native_agent(self):
        factory = _ClientFactory(response=_Response(status_code=404, payload={}))
        runner_class = self.install(factory)
        runner = runner_class()

        with self.assertLogs("xnobrain.integrations.gateway_cli", level="WARNING") as captured:
            result = await runner._run_agent_inner(
                message="hello",
                context_prompt="",
                history=[],
                source=_source(),
                session_id="session-3",
                event_message_id="message-3",
            )

        self.assertEqual(result["final_response"], gateway_cli._DELEGATION_FAILURE_TEXT)
        self.assertEqual(runner_class.native_calls, [])
        self.assertIn("not retrying natively", "\n".join(captured.output))

    async def test_unexpected_shape_never_falls_back_to_native_agent(self):
        factory = _ClientFactory(
            response=_Response(payload={"success": False, "data": {"reply": "no"}})
        )
        runner_class = self.install(factory)
        runner = runner_class()

        result = await runner._run_agent_inner(
            message="hello",
            context_prompt="",
            history=[],
            source=_source(),
            session_id="session-4",
            event_message_id="message-4",
        )

        self.assertEqual(result["final_response"], gateway_cli._DELEGATION_FAILURE_TEXT)
        self.assertEqual(runner_class.native_calls, [])

    async def test_transport_error_never_falls_back_or_logs_secrets(self):
        factory = _ClientFactory(error=RuntimeError(f"token={TOKEN} message=secret text"))
        runner_class = self.install(factory)
        runner = runner_class()

        with self.assertLogs("xnobrain.integrations.gateway_cli", level="WARNING") as captured:
            result = await runner._run_agent_inner(
                message="secret text",
                context_prompt="",
                history=[],
                source=_source(),
                session_id="session-5",
                event_message_id="message-5",
            )

        self.assertEqual(result["final_response"], gateway_cli._DELEGATION_FAILURE_TEXT)
        self.assertEqual(runner_class.native_calls, [])
        logs = "\n".join(captured.output)
        self.assertNotIn(TOKEN, logs)
        self.assertNotIn("secret text", logs)

    async def test_missing_message_id_fails_safely_without_native_execution(self):
        factory = _ClientFactory(response=_Response(payload=_envelope()))
        runner_class = self.install(factory)
        runner = runner_class()
        source = _source()
        source.message_id = ""

        with self.assertLogs("xnobrain.integrations.gateway_cli", level="WARNING") as captured:
            result = await runner._run_agent_inner(
                message="hello",
                context_prompt="",
                history=[],
                source=source,
                session_id="session-missing",
                event_message_id=None,
            )

        self.assertEqual(result["final_response"], gateway_cli._DELEGATION_FAILURE_TEXT)
        self.assertEqual(factory.calls, [])
        self.assertEqual(runner_class.native_calls, [])
        self.assertIn("not retrying natively", "\n".join(captured.output))

    async def test_other_platforms_use_the_native_agent_without_a_request(self):
        factory = _ClientFactory(response=_Response(payload=_envelope()))
        runner_class = self.install(factory)
        runner = runner_class()

        result = await runner._run_agent_inner(
            message="hello",
            context_prompt="",
            history=[],
            source=_source(platform="telegram"),
            session_id="session-6",
            event_message_id="message-6",
        )

        self.assertEqual(result["final_response"], "native reply")
        self.assertEqual(factory.calls, [])

    async def test_switch_off_leaves_the_gateway_untouched(self):
        module, runner_class = _gateway_module()
        with (
            mock.patch.dict(sys.modules, {"gateway.run": module}),
            mock.patch.dict(
                "os.environ", {"RUNTIME_GATEWAY_CHANNEL_DELEGATION": "false"}, clear=True
            ),
        ):
            gateway_cli.install_runtime_channel_delegation()

        runner = runner_class()
        result = await runner._run_agent_inner(
            message="hello",
            context_prompt="",
            history=[],
            source=_source(),
            session_id="session-7",
            event_message_id="message-7",
        )

        self.assertEqual(result["final_response"], "native reply")
        self.assertFalse(runner_class._xnobrain_channel_delegation)
        self.assertEqual(len(runner_class.native_calls), 1)


class GatewayIngressLogRedactionTests(unittest.TestCase):
    def test_gateway_run_ingress_payload_and_display_name_are_redacted(self):
        logger = logging.getLogger("gateway.run")
        gateway_cli.install_gateway_log_redaction()
        with self.assertLogs("gateway.run", level="INFO") as captured:
            logger.info(
                "inbound message: platform=%s user=%s chat=%s msg=%r reply_to_id=%s "
                "reply_to_text=%r",
                "zalo",
                "Nguyen Van Secret",
                "chat-1",
                "private payload",
                "msg-1",
                "private reply",
            )

        output = "\n".join(captured.output)
        self.assertIn("gateway ingress event redacted", output)
        self.assertNotIn("Nguyen Van Secret", output)
        self.assertNotIn("private payload", output)
        self.assertNotIn("private reply", output)


if __name__ == "__main__":
    unittest.main()
