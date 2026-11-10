"""Gateway launcher pairing-reply and operator-notice filtering tests."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from xnobrain.integrations import gateway_cli

_UPSTREAM_REQUEST = (
    "Hi~ I don't recognize you yet!\n\n"
    "Here's your pairing code: `123456`\n\n"
    "Ask the bot owner to run:\n"
    "`hermes pairing approve zalo_bot 123456`"
)
_UPSTREAM_LIMIT = "Too many pairing requests right now~ Please try again later!"


class _PluginAdapter:
    """Adapter that defines its own ``send`` like the ``zalo_bot`` plugin."""

    def __init__(self):
        self.sent = []

    async def send(self, chat_id, text):
        self.sent.append((chat_id, text))
        return "sent"


def _gateway_module(adapter):
    class FakeGatewayRunner:
        _xnobrain_pairing_messages = False

        def _adapter_for_source(self, source):
            return adapter

    module = types.ModuleType("gateway.run")
    module.GatewayRunner = FakeGatewayRunner
    return module, FakeGatewayRunner


def _notice_gateway_module():
    delivered = []

    class FakeGatewayRunner:
        _xnobrain_operator_notice_filter = False

        def _adapter_for_source(self, source):
            return None

        async def _deliver_platform_notice(self, source, content: str) -> None:
            delivered.append((source, content))

    module = types.ModuleType("gateway.run")
    module.GatewayRunner = FakeGatewayRunner
    return module, FakeGatewayRunner, delivered


class GatewayPairingMessageTests(unittest.IsolatedAsyncioTestCase):
    def install(self, adapter):
        module, runner_class = _gateway_module(adapter)
        with mock.patch.dict(sys.modules, {"gateway.run": module}):
            gateway_cli.install_gateway_pairing_messages()
            resolved = runner_class()._adapter_for_source(None)
        return resolved

    async def test_pairing_request_is_rewritten_without_code_or_command(self):
        adapter = _PluginAdapter()
        resolved = self.install(adapter)

        await resolved.send("chat-1", _UPSTREAM_REQUEST)

        self.assertEqual(adapter.sent, [("chat-1", gateway_cli.PAIRING_REQUEST_REPLY)])
        self.assertNotIn("123456", gateway_cli.PAIRING_REQUEST_REPLY)
        self.assertNotIn("hermes pairing approve", gateway_cli.PAIRING_REQUEST_REPLY)

    async def test_pairing_limit_is_rewritten(self):
        adapter = _PluginAdapter()
        resolved = self.install(adapter)

        await resolved.send("chat-2", _UPSTREAM_LIMIT)

        self.assertEqual(adapter.sent, [("chat-2", gateway_cli.PAIRING_LIMIT_REPLY)])

    async def test_other_text_passes_through_unchanged(self):
        adapter = _PluginAdapter()
        resolved = self.install(adapter)

        await resolved.send("chat-3", "An ordinary agent reply.")

        self.assertEqual(adapter.sent, [("chat-3", "An ordinary agent reply.")])

    async def test_install_is_idempotent(self):
        adapter = _PluginAdapter()
        module, runner_class = _gateway_module(adapter)
        with mock.patch.dict(sys.modules, {"gateway.run": module}):
            gateway_cli.install_gateway_pairing_messages()
            first = runner_class._adapter_for_source
            gateway_cli.install_gateway_pairing_messages()
            second = runner_class._adapter_for_source

        self.assertIs(first, second)
        self.assertTrue(runner_class._xnobrain_pairing_messages)


class GatewayOperatorNoticeTests(unittest.IsolatedAsyncioTestCase):
    _HOME_NOTICE = (
        "📬 No home channel is set for Zalo Bot. A home channel is where Hermes "
        "delivers cron job results and cross-platform messages.\n\n"
        "Type /sethome to make this chat your home channel, or ignore to skip."
    )

    def install(self):
        module, runner_class, delivered = _notice_gateway_module()
        with mock.patch.dict(sys.modules, {"gateway.run": module}):
            gateway_cli.install_gateway_operator_notice_filter()
        return runner_class, delivered

    async def test_home_channel_notice_is_dropped(self):
        runner_class, delivered = self.install()

        result = await runner_class()._deliver_platform_notice(object(), self._HOME_NOTICE)

        self.assertIsNone(result)
        self.assertEqual(delivered, [])

    async def test_home_channel_notice_is_dropped_when_passed_by_keyword(self):
        runner_class, delivered = self.install()

        result = await runner_class()._deliver_platform_notice(
            object(), content=self._HOME_NOTICE
        )

        self.assertIsNone(result)
        self.assertEqual(delivered, [])

    async def test_other_notice_passes_through_unchanged(self):
        runner_class, delivered = self.install()
        source = object()
        notice = "⚠️ An unrelated operator notice."

        await runner_class()._deliver_platform_notice(source, notice)

        self.assertEqual(delivered, [(source, notice)])

    async def test_install_is_idempotent(self):
        module, runner_class, delivered = _notice_gateway_module()
        with mock.patch.dict(sys.modules, {"gateway.run": module}):
            gateway_cli.install_gateway_operator_notice_filter()
            first = runner_class._deliver_platform_notice
            gateway_cli.install_gateway_operator_notice_filter()
            second = runner_class._deliver_platform_notice

        self.assertIs(first, second)
        self.assertTrue(runner_class._xnobrain_operator_notice_filter)
        await runner_class()._deliver_platform_notice(object(), self._HOME_NOTICE)
        self.assertEqual(delivered, [])


class UpstreamGatewayPairingTextGuardTests(unittest.TestCase):
    def test_pinned_gateway_pairing_prefixes_still_exist(self):
        try:
            spec = importlib.util.find_spec("gateway.run")
        except (ImportError, ModuleNotFoundError, ValueError):
            spec = None
        if spec is None or not spec.origin:
            self.skipTest("gateway package is not importable")

        source = Path(spec.origin).read_text(encoding="utf-8")
        self.assertIn(gateway_cli.PAIRING_REQUEST_TEXT_PREFIX, source)
        self.assertIn(gateway_cli.PAIRING_LIMIT_TEXT_PREFIX, source)
        self.assertIn(gateway_cli.HOME_CHANNEL_NOTICE_PREFIX, source)


if __name__ == "__main__":
    unittest.main()
