"""Linked-thread routing for the bundled Zalo gateway adapter."""

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ADAPTER_PATH = (
    _REPO_ROOT / "runtime" / "hermes-plugins" / "platforms" / "zalo" / "adapter.py"
)
_STUB_MODULES = (
    "hermes_cli",
    "hermes_cli._subprocess_compat",
    "hermes_constants",
    "gateway",
    "gateway.config",
    "gateway.platforms",
    "gateway.platforms.base",
)


def _make_module(name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


class _Platform:
    def __init__(self, value):
        self.value = value

    def __str__(self):
        return self.value


class _MessageType:
    TEXT = "text"


class _MessageEvent:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _SendResult:
    def __init__(self, success, **kwargs):
        self.success = success
        self.__dict__.update(kwargs)


class _StubBaseAdapter:
    def __init__(self, config, platform):
        self.config = config
        self.platform = platform
        self.name = str(platform)
        self._running = False
        self.has_fatal_error = False
        self.fatal_error_message = None

    def _set_fatal_error(self, code, message, *, retryable):
        self.has_fatal_error = True
        self.fatal_error_message = message

    def _mark_connected(self):
        self._running = True

    def _mark_disconnected(self):
        self._running = False

    def _acquire_platform_lock(self, *args, **kwargs):
        return True

    def _release_platform_lock(self):
        return None

    async def _notify_fatal_error(self):
        return None

    def build_source(self, **kwargs):
        return kwargs


def _install_stubs():
    hermes_cli = _make_module("hermes_cli")
    hermes_cli.__path__ = []
    subprocess_compat = _make_module(
        "hermes_cli._subprocess_compat", windows_detach_popen_kwargs=lambda: {}
    )
    hermes_cli._subprocess_compat = subprocess_compat
    hermes_constants = _make_module(
        "hermes_constants",
        find_node_executable=lambda name: "/usr/bin/node",
        get_hermes_home=lambda: Path(tempfile.gettempdir()) / "zalo-routing-home",
        with_hermes_node_path=lambda: dict(os.environ),
    )
    gateway = _make_module("gateway")
    gateway.__path__ = []
    gateway_config = _make_module(
        "gateway.config", Platform=_Platform, PlatformConfig=types.SimpleNamespace
    )
    gateway_platforms = _make_module("gateway.platforms")
    gateway_platforms.__path__ = []
    gateway_base = _make_module(
        "gateway.platforms.base",
        BasePlatformAdapter=_StubBaseAdapter,
        MessageEvent=_MessageEvent,
        MessageType=_MessageType,
        SendResult=_SendResult,
    )
    gateway.config = gateway_config
    gateway.platforms = gateway_platforms
    gateway_platforms.base = gateway_base
    sys.modules.update(
        {
            "hermes_cli": hermes_cli,
            "hermes_cli._subprocess_compat": subprocess_compat,
            "hermes_constants": hermes_constants,
            "gateway": gateway,
            "gateway.config": gateway_config,
            "gateway.platforms": gateway_platforms,
            "gateway.platforms.base": gateway_base,
        }
    )


def _load_adapter_module():
    importlib.invalidate_caches()
    saved = {name: sys.modules.get(name) for name in _STUB_MODULES}
    _install_stubs()
    try:
        spec = importlib.util.spec_from_file_location("zalo_linked_adapter", _ADAPTER_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def _message(**overrides):
    value = {
        "messageId": "9001",
        "threadId": "111",
        "senderId": "111",
        "senderName": "Nam",
        "text": "hello from Zalo",
        "timestamp": 1_700_000_000_000,
    }
    value.update(overrides)
    return value


class LinkedZaloRoutingTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.adapter = _load_adapter_module()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.session = Path(self.temporary.name) / "zalo"
        self.session.mkdir()
        self.instance = self.adapter.ZaloAdapter(
            types.SimpleNamespace(extra={"session_path": str(self.session), "auto_reply": True}, enabled=True)
        )
        self.instance._running = True
        self.instance.handle_message = mock.AsyncMock()
        self.instance._post_linked = mock.AsyncMock(return_value=True)
        self.environment = mock.patch.dict(
            os.environ,
            {
                "RUNTIME_INTERNAL_URL": "http://runtime:3000",
                "RUNTIME_INTERNAL_SERVICE_TOKEN": "test-token",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def _write_links(self, links):
        (self.session / "links.json").write_text(json.dumps(links), encoding="utf-8")

    async def test_linked_thread_is_posted_and_not_handled(self):
        self._write_links(
            {"111": {"agent_id": "agent-1", "conversation_id": "conv-1", "name": "Nam"}}
        )

        await self.instance._route_messages([_message()])

        self.instance._post_linked.assert_awaited_once()
        payload = self.instance._post_linked.await_args.args[0]
        self.assertEqual(payload["agent_id"], "agent-1")
        self.assertEqual(payload["conversation_id"], "conv-1")
        self.assertEqual(payload["thread_id"], "111")
        self.assertEqual(payload["message_id"], "9001")
        self.assertEqual(payload["text"], "hello from Zalo")
        self.instance.handle_message.assert_not_awaited()

    async def test_unlinked_thread_keeps_hermes_flow(self):
        self._write_links({})

        await self.instance._route_messages([_message()])

        self.instance._post_linked.assert_not_awaited()
        self.instance.handle_message.assert_awaited_once()
        event = self.instance.handle_message.await_args.args[0]
        self.assertEqual(event.text, "hello from Zalo")

    async def test_failed_linked_delivery_is_retried_and_never_handled(self):
        self._write_links(
            {"111": {"agent_id": "agent-1", "conversation_id": "conv-1", "name": "Nam"}}
        )
        self.instance._post_linked = mock.AsyncMock(side_effect=RuntimeError("down"))

        await self.instance._route_messages([_message()])

        self.instance.handle_message.assert_not_awaited()
        self.assertEqual(len(self.instance._pending_linked), 1)
        self.instance._linked_retry_at = 0
        self.instance._post_linked = mock.AsyncMock(return_value=True)

        await self.instance._route_messages([])

        self.instance._post_linked.assert_awaited_once()
        self.assertEqual(self.instance._pending_linked, [])
        self.instance.handle_message.assert_not_awaited()

    async def test_missing_internal_configuration_keeps_message_pending(self):
        self._write_links(
            {"111": {"agent_id": "agent-1", "conversation_id": "conv-1", "name": "Nam"}}
        )
        self.instance._post_linked = self.adapter.ZaloAdapter._post_linked.__get__(
            self.instance, self.adapter.ZaloAdapter
        )
        with mock.patch.dict(
            os.environ,
            {"RUNTIME_INTERNAL_URL": "", "RUNTIME_INTERNAL_SERVICE_TOKEN": ""},
        ):
            await self.instance._route_messages([_message()])

        self.instance.handle_message.assert_not_awaited()
        self.assertEqual(len(self.instance._pending_linked), 1)

    async def test_incomplete_linked_message_is_dropped_without_handling(self):
        self._write_links(
            {"111": {"agent_id": "agent-1", "conversation_id": "conv-1", "name": "Nam"}}
        )

        await self.instance._route_messages([_message(messageId="")])

        self.instance._post_linked.assert_not_awaited()
        self.instance.handle_message.assert_not_awaited()
        self.assertEqual(self.instance._pending_linked, [])


if __name__ == "__main__":
    unittest.main()
