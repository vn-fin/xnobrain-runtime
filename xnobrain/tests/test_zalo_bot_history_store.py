"""Conversation-store tests for the vendored Zalo Bot platform plugin.

The plugin lives outside the Python package tree and targets the Hermes plugin
API, so this module loads the vendored directory as a package with a minimal
Hermes stub (same approach as ``test_zalo_bot_plugin_hardening.py``).
"""

import importlib.util
import json
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
_PACKAGE_NAME = "zalo_bot_history_store"
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
        get_hermes_home=lambda: Path(tempfile.gettempdir())
        / "zalo-bot-history-test-home",
        get_default_hermes_root=lambda: Path(tempfile.gettempdir())
        / "zalo-bot-history-test-home",
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


class _FakeClient:
    def __init__(self, message_id="out-1"):
        self.message_id = message_id
        self.sent = []

    async def send_message(self, chat_id, text, parse_mode=None):
        self.sent.append((chat_id, text))
        return {"ok": True, "result": {"message_id": self.message_id}}


class ZaloBotHistoryStoreTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.package = _load_plugin_package()
        cls.adapter_module = cls.package.adapter
        cls.Adapter = cls.adapter_module.ZaloAdapter

    # ── Fixtures ──────────────────────────────────────────────────────

    def _home(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    def _adapter(self, home, **extra):
        with mock.patch.dict(os.environ, {}, clear=True):
            adapter = self.Adapter(types.SimpleNamespace(extra=extra))
        return adapter

    @staticmethod
    def _write_groups(home, groups):
        directory = home / "zalo"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "groups.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "platforms": {"zalo_bot": {"groups": groups}},
                }
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _history_path(home, chat_id):
        return home / "zalo_bot" / "history" / f"{chat_id}.jsonl"

    def _records(self, home, chat_id):
        path = self._history_path(home, chat_id)
        if not path.is_file():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    @staticmethod
    def _group_message(
        text="hello",
        group_id="group-1",
        user_id="member1",
        message_id="msg-1",
    ):
        return {
            "from": {"id": user_id, "display_name": "Member"},
            "chat": {"id": group_id, "chat_type": "GROUP"},
            "text": text,
            "message_id": message_id,
        }

    @staticmethod
    def _dm_message(
        text="hello",
        chat_id="chat1",
        user_id="user1",
        message_id="dm-1",
    ):
        return {
            "from": {"id": user_id, "display_name": "User"},
            "chat": {"id": chat_id, "chat_type": "PRIVATE"},
            "text": text,
            "message_id": message_id,
        }

    def _pairing_modules(self, home, approved):
        approved = set(approved)

        class _Store:
            def __init__(self, profile=None):
                self.profile = profile

            def is_approved(self, platform, user_id):
                return (platform, user_id) in approved

        pairing = types.ModuleType("gateway.pairing")
        pairing.PairingStore = _Store
        gateway = types.ModuleType("gateway")
        gateway.__path__ = []
        gateway.pairing = pairing
        constants = types.ModuleType("hermes_constants")
        constants.get_hermes_home = lambda: home
        constants.get_default_hermes_root = lambda: home
        return mock.patch.dict(
            sys.modules,
            {
                "gateway": gateway,
                "gateway.pairing": pairing,
                "hermes_constants": constants,
            },
        )

    # ── Inbound group scope ───────────────────────────────────────────

    async def test_approved_group_stores_unauthorized_member_immediately(self):
        home = self._home()
        self._write_groups(home, {"group-1": {"status": "approved"}})
        adapter = self._adapter(
            home,
            bot_token="1:secret",
            dm_policy="pairing",
            allowed_users="admin1",
        )

        with mock.patch.object(
            self.adapter_module, "get_hermes_home", return_value=home
        ):
            await adapter._handle_incoming("", self._group_message())

        path = self._history_path(home, "group-1")
        self.assertTrue(path.is_file())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        history_dir = home / "zalo_bot" / "history"
        self.assertEqual(history_dir.stat().st_mode & 0o777, 0o700)
        self.assertEqual((home / "zalo_bot").stat().st_mode & 0o777, 0o700)

        records = self._records(home, "group-1")
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["message_id"], "msg-1")
        self.assertEqual(record["chat_id"], "group-1")
        self.assertEqual(record["chat_type"], "group")
        self.assertEqual(record["direction"], "in")
        self.assertEqual(record["sender_id"], "member1")
        self.assertEqual(record["sender_name"], "Member")
        self.assertEqual(record["type"], "text")
        self.assertEqual(record["text"], "hello")
        self.assertEqual(record["trust"], "untrusted_external")
        self.assertNotIn("media_url", record)
        self.assertIsInstance(record["ts"], float)

    async def test_unapproved_group_is_not_stored(self):
        home = self._home()
        self._write_groups(home, {"group-1": {"status": "pending"}})
        adapter = self._adapter(home, bot_token="1:secret", allowed_users="admin1")

        with mock.patch.object(
            self.adapter_module, "get_hermes_home", return_value=home
        ):
            await adapter._handle_incoming("", self._group_message())

        self.assertFalse(self._history_path(home, "group-1").exists())

    async def test_follow_off_group_is_not_stored(self):
        home = self._home()
        self._write_groups(home, {"group-1": {"status": "approved"}})
        adapter = self._adapter(home, bot_token="1:secret", allowed_users="admin1")

        with mock.patch.object(
            self.adapter_module, "get_hermes_home", return_value=home
        ):
            adapter._group_settings.set("group-1", "follow", False)
            await adapter._handle_incoming("", self._group_message())

        self.assertFalse(self._history_path(home, "group-1").exists())

    async def test_approved_group_slash_command_is_not_stored(self):
        home = self._home()
        self._write_groups(home, {"group-1": {"status": "approved"}})
        adapter = self._adapter(home, bot_token="1:secret", allowed_users="admin1")

        with mock.patch.object(
            self.adapter_module, "get_hermes_home", return_value=home
        ):
            await adapter._handle_incoming("", self._group_message(text="/menu"))

        self.assertFalse(self._history_path(home, "group-1").exists())

    # ── Inbound DM scope ──────────────────────────────────────────────

    async def test_allowlisted_dm_is_stored(self):
        home = self._home()
        adapter = self._adapter(
            home,
            bot_token="1:secret",
            dm_policy="allowlist",
            allowed_users="user1",
        )

        with mock.patch.object(
            self.adapter_module, "get_hermes_home", return_value=home
        ):
            await adapter._handle_incoming("", self._dm_message())

        records = self._records(home, "chat1")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["chat_type"], "dm")
        self.assertEqual(records[0]["direction"], "in")
        self.assertEqual(records[0]["sender_id"], "user1")

    async def test_dm_not_in_allowlist_is_not_stored(self):
        home = self._home()
        adapter = self._adapter(
            home,
            bot_token="1:secret",
            dm_policy="allowlist",
            allowed_users="user1",
        )

        with mock.patch.object(
            self.adapter_module, "get_hermes_home", return_value=home
        ):
            await adapter._handle_incoming(
                "", self._dm_message(user_id="user2", chat_id="chat2")
            )

        self.assertFalse(self._history_path(home, "chat2").exists())

    async def test_disabled_dm_policy_is_not_stored(self):
        home = self._home()
        adapter = self._adapter(
            home,
            bot_token="1:secret",
            dm_policy="disabled",
            allow_all="true",
        )

        with mock.patch.object(
            self.adapter_module, "get_hermes_home", return_value=home
        ):
            await adapter._handle_incoming("", self._dm_message())

        self.assertFalse(self._history_path(home, "chat1").exists())

    async def test_pairing_policy_requires_an_approved_pairing(self):
        home = self._home()
        adapter = self._adapter(home, bot_token="1:secret", dm_policy="pairing")

        with self._pairing_modules(home, set()):
            with mock.patch.object(
                self.adapter_module, "get_hermes_home", return_value=home
            ):
                await adapter._handle_incoming("", self._dm_message())
        self.assertFalse(self._history_path(home, "chat1").exists())

        with self._pairing_modules(home, {("zalo_bot", "user1")}):
            with mock.patch.object(
                self.adapter_module, "get_hermes_home", return_value=home
            ):
                await adapter._handle_incoming(
                    "", self._dm_message(message_id="dm-2")
                )
        self.assertEqual(len(self._records(home, "chat1")), 1)

    # ── Outbound scope ────────────────────────────────────────────────

    async def test_outbound_text_is_stored_with_direction_out(self):
        home = self._home()
        adapter = self._adapter(
            home,
            bot_token="1:secret",
            dm_policy="allowlist",
            allowed_users="chat1",
        )
        adapter._bot_id = "bot-1"
        adapter._bot_name = "Bot"
        adapter._bot_display_name = "Bot Name"
        client = _FakeClient()
        adapter._client = client

        with mock.patch.object(
            self.adapter_module, "get_hermes_home", return_value=home
        ):
            result = await adapter._send_text("chat1", "reply")

        self.assertTrue(result.success)
        records = self._records(home, "chat1")
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["direction"], "out")
        self.assertEqual(record["chat_type"], "dm")
        self.assertEqual(record["sender_id"], "bot-1")
        self.assertEqual(record["sender_name"], "Bot Name")
        self.assertEqual(record["type"], "text")
        self.assertEqual(record["text"], "reply")
        self.assertEqual(record["message_id"], "out-1")

    async def test_chunked_send_is_stored_once(self):
        home = self._home()
        adapter = self._adapter(
            home,
            bot_token="1:secret",
            dm_policy="allowlist",
            allowed_users="chat1",
        )
        adapter._bot_id = "bot-1"
        client = _FakeClient()
        adapter._client = client
        content = "word " * 800

        with mock.patch.object(
            self.adapter_module, "get_hermes_home", return_value=home
        ):
            with mock.patch.object(
                self.adapter_module.asyncio, "sleep", new=mock.AsyncMock()
            ):
                result = await adapter.send("chat1", content)

        self.assertTrue(result.success)
        self.assertGreater(len(client.sent), 1)
        records = self._records(home, "chat1")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["direction"], "out")
        self.assertEqual(records[0]["text"], adapter._prepare_for_zalo(content))

    async def test_outbound_skips_unapproved_group(self):
        home = self._home()
        self._write_groups(home, {"group-1": {"status": "pending"}})
        adapter = self._adapter(home, bot_token="1:secret", dm_policy="pairing")
        adapter._client = _FakeClient()

        with mock.patch.object(
            self.adapter_module, "get_hermes_home", return_value=home
        ):
            await adapter._send_text("group-1", "reply")

        self.assertFalse(self._history_path(home, "group-1").exists())

    # ── Path safety ───────────────────────────────────────────────────

    async def test_chat_id_traversal_is_sanitized(self):
        home = self._home()
        adapter = self._adapter(
            home,
            bot_token="1:secret",
            dm_policy="pairing",
            allow_all="true",
        )

        with mock.patch.object(
            self.adapter_module, "get_hermes_home", return_value=home
        ):
            await adapter._handle_incoming(
                "", self._dm_message(chat_id="../escape")
            )

        history_dir = home / "zalo_bot" / "history"
        self.assertEqual([path.name for path in history_dir.iterdir()], ["___escape.jsonl"])
        self.assertFalse((home / "escape.jsonl").exists())
        self.assertFalse((home.parent / "escape.jsonl").exists())

    # ── Profile scoping ───────────────────────────────────────────────

    def test_group_settings_are_profile_scoped(self):
        home_a = self._home()
        home_b = self._home()
        with mock.patch.object(
            self.adapter_module, "get_hermes_home", return_value=home_a
        ):
            adapter_a = self._adapter(home_a, bot_token="1:secret")
            adapter_a._group_settings.set("group-a", "follow", False)
        with mock.patch.object(
            self.adapter_module, "get_hermes_home", return_value=home_b
        ):
            adapter_b = self._adapter(home_b, bot_token="1:secret")
            adapter_b._group_settings.set("group-b", "welcome", True)

        path_a = home_a / "zalo-bot-settings" / "group-a.json"
        path_b = home_b / "zalo-bot-settings" / "group-b.json"
        self.assertTrue(path_a.is_file())
        self.assertTrue(path_b.is_file())
        self.assertFalse((home_a / "zalo-bot-settings" / "group-b.json").exists())
        self.assertFalse((home_b / "zalo-bot-settings" / "group-a.json").exists())
        self.assertEqual(path_a.stat().st_mode & 0o777, 0o600)
        self.assertEqual(
            (home_a / "zalo-bot-settings").stat().st_mode & 0o777, 0o700
        )
        self.assertFalse(adapter_a._group_settings.get("group-a")["follow"])
        self.assertTrue(adapter_a._group_settings.get("group-b").get("follow", True))
        self.assertFalse(adapter_a._group_settings.get("group-b").get("welcome"))

    # ── Retention ─────────────────────────────────────────────────────

    def test_retention_days_are_clamped(self):
        coerce = self.adapter_module._coerce_retention_days
        self.assertEqual(coerce(0), 1)
        self.assertEqual(coerce(-5), 1)
        self.assertEqual(coerce(999), 365)
        self.assertEqual(coerce("abc"), 30)
        self.assertEqual(coerce(None), 30)
        self.assertEqual(coerce(True), 30)
        self.assertEqual(coerce(14), 14)

    def test_trim_drops_old_lines(self):
        home = self._home()
        store = self.adapter_module._BotHistoryStore(home)
        now = time.time()
        store.append(
            {"chat_id": "chat1", "text": "old", "ts": now - 40 * 86400}
        )
        store.append({"chat_id": "chat1", "text": "new", "ts": now})

        store.trim(30)

        records = self._records(home, "chat1")
        self.assertEqual([record["text"] for record in records], ["new"])

    def test_trim_drops_malformed_lines_and_empty_files(self):
        home = self._home()
        store = self.adapter_module._BotHistoryStore(home)
        now = time.time()
        store.append({"chat_id": "chat1", "text": "old", "ts": now - 90 * 86400})
        path = self._history_path(home, "chat1")
        with path.open("a", encoding="utf-8") as handle:
            handle.write("not json\n")

        store.trim(30)

        self.assertFalse(path.exists())

    def test_size_cap_removes_oldest_chat_file_first(self):
        home = self._home()
        store = self.adapter_module._BotHistoryStore(home)
        now = time.time()
        store.append({"chat_id": "old-chat", "text": "a" * 100, "ts": now})
        store.append({"chat_id": "new-chat", "text": "b" * 100, "ts": now})
        old_path = self._history_path(home, "old-chat")
        new_path = self._history_path(home, "new-chat")
        os.utime(old_path, (now - 100, now - 100))
        os.utime(new_path, (now, now))

        store.trim(365, max_bytes=new_path.stat().st_size)

        self.assertFalse(old_path.exists())
        self.assertTrue(new_path.exists())

    def test_delete_helpers(self):
        home = self._home()
        store = self.adapter_module._BotHistoryStore(home)
        store.append({"chat_id": "chat1", "text": "one", "ts": time.time()})
        store.append({"chat_id": "chat2", "text": "two", "ts": time.time()})

        self.assertEqual(store.delete("chat1"), 1)
        self.assertEqual(store.delete("chat1"), 0)
        self.assertEqual(store.delete_all(), 1)
        self.assertEqual(store.delete_all(), 0)


if __name__ == "__main__":
    unittest.main()
