"""Security-hardening tests for the vendored Zalo Bot platform plugin.

The plugin lives outside the Python package tree and targets the Hermes plugin
API, so this module loads the vendored directory as a package with a minimal
Hermes stub (same approach as ``test_zalo_bot_plugin.py``).
"""

import asyncio
import importlib.util
import json
import logging
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PLUGIN_DIR = _REPO_ROOT / "runtime" / "hermes-plugins" / "platforms" / "zalo_bot"
_PACKAGE_NAME = "zalo_bot_plugin_hardening"
_STUB_MODULES = (
    "gateway",
    "gateway.config",
    "gateway.platforms",
    "gateway.platforms.base",
    "gateway.session",
    "hermes_constants",
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
    IMAGE = "image"
    STICKER = "sticker"


class _MessageEvent:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _SendResult:
    def __init__(self, success, message_id=None, error=None, **kwargs):
        self.success = success
        self.message_id = message_id
        self.error = error


class _StubBaseAdapter:
    def __init__(self, config, platform):
        self.config = config
        self.platform = platform
        self._message_handler = None
        self._connected = False
        self._fatal_error = None

    @property
    def is_connected(self):
        return self._connected

    def _mark_connected(self):
        self._connected = True

    def _mark_disconnected(self):
        self._connected = False

    def _set_fatal_error(self, code, message, retryable=True):
        self._fatal_error = (code, message, retryable)

    def build_source(self, **kwargs):
        return types.SimpleNamespace(**kwargs)

    async def handle_message(self, event):
        if self._message_handler is not None:
            await self._message_handler(event)


def _install_stubs():
    gateway = _make_module("gateway")
    gateway.__path__ = []
    gateway_config = _make_module(
        "gateway.config",
        Platform=_Platform,
        PlatformConfig=types.SimpleNamespace,
    )
    gateway_platforms = _make_module("gateway.platforms")
    gateway_platforms.__path__ = []
    gateway_base = _make_module(
        "gateway.platforms.base",
        BasePlatformAdapter=_StubBaseAdapter,
        MessageEvent=_MessageEvent,
        MessageType=_MessageType,
        SendResult=_SendResult,
        cache_image_from_url=lambda *args, **kwargs: None,
    )
    gateway_session = _make_module(
        "gateway.session",
        SessionSource=types.SimpleNamespace,
    )
    hermes_constants = _make_module(
        "hermes_constants",
        get_hermes_home=lambda: Path(tempfile.gettempdir()) / "zalo-bot-hardening-test-home",
    )
    gateway.config = gateway_config
    gateway.platforms = gateway_platforms
    gateway_platforms.base = gateway_base

    sys.modules.update(
        {
            "gateway": gateway,
            "gateway.config": gateway_config,
            "gateway.platforms": gateway_platforms,
            "gateway.platforms.base": gateway_base,
            "gateway.session": gateway_session,
            "hermes_constants": hermes_constants,
        }
    )


def _load_plugin_package():
    importlib.invalidate_caches()
    saved = {name: sys.modules.get(name) for name in _STUB_MODULES}
    saved_package = sys.modules.get(_PACKAGE_NAME)
    saved_adapter = sys.modules.get(f"{_PACKAGE_NAME}.adapter")
    _install_stubs()
    try:
        spec = importlib.util.spec_from_file_location(
            _PACKAGE_NAME,
            _PLUGIN_DIR / "__init__.py",
            submodule_search_locations=[str(_PLUGIN_DIR)],
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[_PACKAGE_NAME] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
        for name, previous in (
            (_PACKAGE_NAME, saved_package),
            (f"{_PACKAGE_NAME}.adapter", saved_adapter),
        ):
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


class _FakeGroupManager:
    def __init__(self, group_info=None, error=None):
        self.group_info = group_info
        self.error = error
        self.kicked = []
        self.pinned = []

    async def get_group_info(self, group_id):
        if self.error is not None:
            raise self.error
        return self.group_info

    async def kick_member(self, group_id, user_id, reason=""):
        self.kicked.append((group_id, user_id, reason))
        return {"ok": True}

    async def pin_message(self, group_id, message_id):
        self.pinned.append((group_id, message_id))
        return {"ok": True}


class _FakeZaloClient:
    """Minimal connected client: getMe succeeds, polling blocks until cancel."""

    def __init__(self, bot_token):
        self.bot_token = bot_token

    async def get_me(self):
        return {
            "ok": True,
            "result": {
                "id": "bot-secret-id",
                "account_name": "Bot",
                "display_name": "Bot",
            },
        }

    async def get_updates(self, timeout=30):
        await asyncio.sleep(3600)
        return []

    async def close(self):
        return None


class ZaloBotHardeningTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.package = _load_plugin_package()
        cls.adapter_module = cls.package.adapter
        cls.Adapter = cls.adapter_module.ZaloAdapter

    def _adapter(self, **extra):
        with mock.patch.dict(os.environ, {}, clear=True):
            return self.Adapter(types.SimpleNamespace(extra=extra))

    def _install_sender(self, adapter):
        sent = []

        async def fake_send(chat_id, text):
            sent.append((chat_id, text))
            return self.adapter_module.SendResult(success=True, message_id="1")

        adapter._send_text = fake_send
        return sent

    @staticmethod
    def _dm_message(user_id="user1", chat_id="chat1", text="hello"):
        return {
            "from": {"id": user_id, "display_name": "User"},
            "chat": {"id": chat_id, "chat_type": "PRIVATE"},
            "text": text,
        }

    # ── H1: admin check fails closed ──────────────────────────────────

    async def test_admin_check_denies_when_lookup_fails(self):
        adapter = self._adapter(bot_token="1:secret", allow_all="true")
        sent = self._install_sender(adapter)
        manager = _FakeGroupManager(error=RuntimeError("group API down"))
        adapter._group_manager = manager

        slash = self.adapter_module.parse_slash_command("/kick @victim spam")
        await adapter._handle_slash_command(slash, "group1", "user1", "User", "text", None, "GROUP")

        self.assertTrue(any("không có quyền" in text for _, text in sent))
        self.assertEqual(manager.kicked, [])

    async def test_admin_check_denies_without_group_chat_type(self):
        adapter = self._adapter(bot_token="1:secret", allow_all="true")
        sent = self._install_sender(adapter)
        manager = _FakeGroupManager(
            group_info={
                "ok": True,
                "result": {
                    "members": [{"user_id": "user1", "role": "member"}],
                },
            }
        )
        adapter._group_manager = manager

        slash = self.adapter_module.parse_slash_command("/pin 123")
        await adapter._handle_slash_command(slash, "chat1", "user1", "User", "text", None, "")

        self.assertTrue(any("không có quyền" in text for _, text in sent))
        self.assertEqual(manager.pinned, [])

    async def test_admin_check_allows_live_owner_role(self):
        adapter = self._adapter(bot_token="1:secret", allow_all="true")
        self._install_sender(adapter)
        manager = _FakeGroupManager(
            group_info={
                "ok": True,
                "result": {
                    "members": [{"user_id": "user1", "role": "owner"}],
                },
            }
        )
        adapter._group_manager = manager

        slash = self.adapter_module.parse_slash_command("/kick @victim spam")
        await adapter._handle_slash_command(slash, "group1", "user1", "User", "text", None, "GROUP")

        self.assertEqual(manager.kicked, [("group1", "victim", "spam")])

    # ── H3: DM policy and pairing ─────────────────────────────────────

    def test_unknown_dm_policy_fails_closed(self):
        for raw in ("open", "bogus", "OPEN"):
            with self.subTest(raw=raw):
                adapter = self._adapter(bot_token="1:secret", dm_policy=raw, allow_all="true")
                self.assertEqual(adapter.dm_policy, "disabled")
                self.assertEqual(adapter._dm_policy, "disabled")
                self.assertFalse(adapter._passes_allowlist("user1"))

        default_adapter = self._adapter(bot_token="1:secret")
        self.assertEqual(default_adapter.dm_policy, "pairing")
        self.assertEqual(default_adapter._dm_policy, "pairing")

    def test_allowlist_policy_without_allowlist_denies(self):
        adapter = self._adapter(bot_token="1:secret", dm_policy="allowlist")
        self.assertFalse(adapter._passes_allowlist("user1"))

        allowed = self._adapter(bot_token="1:secret", dm_policy="allowlist", allowed_users="user1")
        self.assertTrue(allowed._passes_allowlist("user1"))
        self.assertFalse(allowed._passes_allowlist("user2"))

    async def test_pairing_policy_dispatches_first_dm_immediately(self):
        adapter = self._adapter(bot_token="1:secret", dm_policy="pairing")
        sent = self._install_sender(adapter)
        dispatched = []

        async def handler(event):
            dispatched.append(event)

        adapter._message_handler = handler

        await adapter._handle_incoming("", self._dm_message(text="hello"))

        self.assertEqual(len(dispatched), 1)
        self.assertEqual(dispatched[0].text, "hello")
        self.assertEqual(dispatched[0].source.chat_type, "dm")
        self.assertEqual(sent, [])

    # ── M2/M3: group side effects and provider-ID dedupe ──────────────

    async def test_join_welcome_requires_explicit_group_authorization(self):
        adapter = self._adapter(bot_token="1:secret", dm_policy="pairing")
        sent = self._install_sender(adapter)
        adapter._group_settings.set("group-join-1", "welcome", True)

        await adapter._handle_incoming(
            "message.member_joined",
            {
                "from": {"id": "user1", "display_name": "User"},
                "chat": {"id": "group-join-1", "chat_type": "GROUP"},
                "message_id": "join-1",
            },
        )
        self.assertEqual(sent, [])

        allowed = self._adapter(bot_token="1:secret", allow_all="true")
        allowed_sent = self._install_sender(allowed)
        allowed._group_settings.set("group-join-2", "welcome", True)
        await allowed._handle_incoming(
            "message.member_joined",
            {
                "from": {"id": "user2", "display_name": "User"},
                "chat": {"id": "group-join-2", "chat_type": "GROUP"},
                "message_id": "join-2",
            },
        )
        self.assertTrue(allowed_sent)

    async def test_duplicate_provider_message_id_is_delivered_once(self):
        adapter = self._adapter(
            bot_token="1:secret",
            dm_policy="allowlist",
            allowed_users="user1",
        )
        dispatched = []

        async def handler(event):
            dispatched.append(event)

        adapter._message_handler = handler

        message = self._dm_message(user_id="user1", chat_id="chat-dedupe", text="hello")
        message["message_id"] = "provider-message-1"

        await adapter._handle_incoming("", message)
        await adapter._handle_incoming("", message, update_id="update-1")

        self.assertEqual(len(dispatched), 1)
        self.assertEqual(dispatched[0].message_id, "provider-message-1")

    async def test_missing_provider_id_preserves_messages_without_dedupe(self):
        adapter = self._adapter(
            bot_token="1:secret",
            dm_policy="allowlist",
            allowed_users="user1",
        )
        dispatched = []

        async def handler(event):
            dispatched.append(event)

        adapter._message_handler = handler

        message = self._dm_message(user_id="user1", chat_id="chat-no-id", text="hello")
        await adapter._handle_incoming("", message)
        await adapter._handle_incoming("", message)

        self.assertEqual(len(dispatched), 2)
        self.assertEqual(dispatched[0].message_id, "")

    # ── Group slash commands and empty mentions ───────────────────────

    @staticmethod
    def _group_message(text, group_id="group-1", user_id="user1", message_id="group-msg-1"):
        return {
            "from": {"id": user_id, "display_name": "User"},
            "chat": {"id": group_id, "chat_type": "GROUP"},
            "text": text,
            "message_id": message_id,
        }

    async def test_hermes_slash_command_is_dropped_after_mention(self):
        adapter = self._adapter(bot_token="1:secret", dm_policy="pairing")
        adapter._bot_name = "TestBot"
        dispatched = []

        async def handler(event):
            dispatched.append(event)

        adapter._message_handler = handler

        await adapter._handle_incoming(
            "", self._group_message("@TestBot /model x")
        )
        await adapter._handle_incoming(
            "",
            self._group_message(
                "@TestBot /new", user_id="user2", message_id="group-msg-2"
            ),
        )

        self.assertEqual(dispatched, [])

    async def test_adapter_slash_command_is_still_handled_in_groups(self):
        adapter = self._adapter(
            bot_token="1:secret", dm_policy="pairing", allow_all="true"
        )
        adapter._bot_name = "TestBot"
        dispatched = []
        handled = []

        async def handler(event):
            dispatched.append(event)

        async def slash(slash_command, *args):
            handled.append(slash_command["command"])

        adapter._message_handler = handler
        adapter._handle_slash_command = slash

        await adapter._handle_incoming("", self._group_message("@TestBot /menu"))

        self.assertEqual(handled, ["/menu"])
        self.assertEqual(dispatched, [])

    async def test_empty_mention_is_not_dispatched(self):
        adapter = self._adapter(bot_token="1:secret", dm_policy="pairing")
        adapter._bot_name = "TestBot"
        dispatched = []

        async def handler(event):
            dispatched.append(event)

        adapter._message_handler = handler

        await adapter._handle_incoming("", self._group_message("@TestBot   "))

        self.assertEqual(dispatched, [])

    # ── M1: config type coercion ──────────────────────────────────────

    def test_string_config_is_coerced(self):
        adapter = self._adapter(
            bot_token="1:secret",
            dm_policy="allowlist",
            allow_all="false",
            allowed_users="user1, user2",
        )
        self.assertFalse(adapter.allow_all)
        self.assertEqual(adapter.allowed_users, {"user1", "user2"})
        self.assertTrue(adapter._passes_allowlist("user1"))

        truthy = self._adapter(bot_token="1:secret", allow_all="TRUE")
        self.assertTrue(truthy.allow_all)

        single = self._adapter(bot_token="1:secret", dm_policy="allowlist", allowed_users="abc")
        self.assertEqual(single.allowed_users, {"abc"})
        self.assertFalse(single._passes_allowlist("a"))

    # ── H2: token redaction ───────────────────────────────────────────

    async def test_http_loggers_quiet_and_post_error_redacts_token(self):
        token = "123456:abcdefSECRET"
        adapter = self._adapter(bot_token=token)
        self.assertEqual(logging.getLogger("httpx").level, logging.WARNING)
        self.assertEqual(logging.getLogger("httpcore").level, logging.WARNING)

        client = self.adapter_module._ZaloClient(token)

        class FailingHTTP:
            async def post(self, url, json=None):
                raise RuntimeError(f"HTTP Request: POST {url}")

        client._client = FailingHTTP()
        with self.assertRaises(RuntimeError) as raised:
            await client._post("getMe")

        self.assertNotIn(token, str(raised.exception))
        self.assertIn("***", str(raised.exception))

    async def test_connect_error_log_redacts_token(self):
        token = "123456:abcdefSECRET"
        adapter = self._adapter(bot_token=token)

        class FailingClient:
            def __init__(self, bot_token):
                self.bot_token = bot_token

            async def get_me(self):
                raise RuntimeError(
                    f"HTTP Request: POST https://bot-api.zaloplatforms.com/bot{token}/getMe failed"
                )

            async def close(self):
                return None

        with mock.patch.object(self.adapter_module, "_ZaloClient", FailingClient):
            with self.assertLogs(level="DEBUG") as logs:
                connected = await adapter.connect()

        self.assertFalse(connected)
        self.assertNotIn(token, "\n".join(logs.output))

    # ── Per-profile runtime state for the channels service ────────────

    def _state_home(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    async def test_state_file_lifecycle_and_no_secrets(self):
        token = "123456:abcdefSECRET"
        home = self._state_home()
        with mock.patch.object(
            self.adapter_module, "get_hermes_home", return_value=home
        ):
            adapter = self._adapter(bot_token=token)
            states = []
            original = adapter._write_state

            def capture(state):
                original(state)
                states.append(state)

            adapter._write_state = capture
            with mock.patch.object(
                self.adapter_module, "_ZaloClient", _FakeZaloClient
            ):
                self.assertTrue(await adapter.connect())

            self.assertEqual(states[0], "connecting")
            self.assertIn("connected", states)
            path = home / "zalo_bot_state.json"
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(record["state"], "connected")
            self.assertEqual(set(record), {"state", "updated_at", "pid"})
            self.assertIsInstance(record["pid"], int)
            contents = path.read_text(encoding="utf-8")
            self.assertNotIn(token, contents)
            self.assertNotIn("bot-secret-id", contents)

            await adapter.disconnect()

        self.assertEqual(states[-1], "disconnected")
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(record["state"], "disconnected")

    def test_heartbeat_refreshes_updated_at(self):
        home = self._state_home()
        with mock.patch.object(
            self.adapter_module, "get_hermes_home", return_value=home
        ):
            adapter = self._adapter(bot_token="123456:abcdefSECRET")
            adapter._write_state("connected")
            path = home / "zalo_bot_state.json"
            before = json.loads(path.read_text(encoding="utf-8"))["updated_at"]
            adapter._state_last_write = 0.0
            time.sleep(0.01)

            adapter._refresh_state_heartbeat()

            record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(record["state"], "connected")
        self.assertGreater(record["updated_at"], before)

    def test_state_write_failure_does_not_raise(self):
        home = self._state_home()
        with mock.patch.object(
            self.adapter_module, "get_hermes_home", return_value=home
        ):
            adapter = self._adapter(bot_token="123456:abcdefSECRET")
            with mock.patch.object(
                self.adapter_module.os, "replace", side_effect=OSError("disk full")
            ):
                adapter._write_state("connected")

            self.assertFalse((home / "zalo_bot_state.json").exists())
            self.assertEqual(list(home.iterdir()), [])

    # ── Standalone API for Runtime tools (plan 020 B2) ────────────────

    async def test_client_media_and_webhook_methods_post_expected_payloads(self):
        client = self.adapter_module._ZaloClient("1:secret")
        calls = []

        async def fake_post(endpoint, data=None):
            calls.append((endpoint, data))
            return {"ok": True, "result": {"message_id": "9"}}

        client._post = fake_post

        self.assertEqual(await client.send_sticker("chat", "st-1"), {
            "ok": True, "result": {"message_id": "9"}
        })
        await client.send_voice("chat", "https://cdn.example/v.aac")
        await client.get_webhook_info()

        self.assertEqual(
            calls,
            [
                ("sendSticker", {"chat_id": "chat", "sticker": "st-1"}),
                ("sendVoice", {"chat_id": "chat", "voice": "https://cdn.example/v.aac"}),
                ("getWebhookInfo", None),
            ],
        )

    async def test_standalone_api_action_allowlist(self):
        module = self.adapter_module
        self.assertEqual(
            module._STANDALONE_API_ACTIONS,
            frozenset(
                {"get_me", "get_webhook_info", "send_photo", "send_sticker", "send_voice"}
            ),
        )
        pconfig = types.SimpleNamespace(extra={"bot_token": "123456:abcdefSECRET"})

        unknown = await module._standalone_api(pconfig, "send_text")
        self.assertEqual(unknown, {"error": "unsupported_action"})

        class FailingClient:
            def __init__(self, bot_token):
                self.bot_token = bot_token

            async def send_sticker(self, chat_id, sticker):
                raise RuntimeError(f"HTTP POST /bot{self.bot_token}/sendSticker")

            async def close(self):
                return None

        with mock.patch.object(module, "_ZaloClient", FailingClient):
            failed = await module._standalone_api(
                pconfig, "send_sticker", chat_id="chat", sticker="st-1"
            )
        self.assertEqual(failed, {"error": "api_error"})
        self.assertNotIn("123456:abcdefSECRET", json.dumps(failed))


if __name__ == "__main__":
    unittest.main()
