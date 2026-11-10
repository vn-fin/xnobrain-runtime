"""Gateway launcher pairing-reply rewriting tests."""

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


if __name__ == "__main__":
    unittest.main()
