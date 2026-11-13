"""Focused tests for the bundled Zalo platform plugin.

The plugin lives outside the Python package tree and targets the Hermes plugin
API, so this module loads ``adapter.py`` by path with a minimal Hermes stub.
That keeps the test independent of whether Hermes is importable on the host.
"""

import asyncio
import importlib.util
import os
import socket
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
    def __init__(
        self,
        success,
        message_id=None,
        error=None,
        raw_response=None,
        continuation_message_ids=(),
        **kwargs,
    ):
        self.success = success
        self.message_id = message_id
        self.error = error
        self.raw_response = raw_response
        self.continuation_message_ids = continuation_message_ids


class _StubBaseAdapter:
    def __init__(self, config, platform):
        self.config = config
        self.platform = platform
        self.name = str(platform)
        self._running = False
        self.has_fatal_error = False
        self.fatal_error_message = None
        self._fatal_error_code = None
        self.handled = []

    def _set_fatal_error(self, code, message, *, retryable):
        self._fatal_error_code = code
        self.fatal_error_message = message
        self.has_fatal_error = True

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

    async def handle_message(self, event):
        self.handled.append(event)

    def truncate_message(self, text, limit):
        if not text:
            return [text]
        return [text[index : index + limit] for index in range(0, len(text), limit)]


class _FakeResponse:
    def __init__(self, status, payload, text):
        self.status = status
        self._payload = payload
        self._text = text

    async def json(self):
        return self._payload

    async def text(self):
        return self._text


class _FakeResponseContext:
    def __init__(self, status, payload, text):
        self._response = _FakeResponse(status, payload, text)

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _FakeHTTP:
    """Minimal aiohttp ClientSession double that records every request."""

    def __init__(self, status=200, payload=None, text=""):
        self.status = status
        self.payload = payload if payload is not None else {}
        self.text = text
        self.calls = []
        self.closed = False
        self.on_get = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def close(self):
        self.closed = True

    def _record(self, method, url, payload, headers):
        self.calls.append(
            {"method": method, "url": url, "json": payload, "headers": headers}
        )
        return _FakeResponseContext(self.status, self.payload, self.text)

    def post(self, url, json=None, headers=None, timeout=None):
        return self._record("POST", url, json, headers)

    def get(self, url, headers=None, timeout=None):
        context = self._record("GET", url, None, headers)
        if self.on_get is not None:
            self.on_get()
        return context


def _fake_aiohttp(session):
    module = types.ModuleType("aiohttp")
    module.ClientTimeout = lambda **kwargs: types.SimpleNamespace(**kwargs)
    module.ClientSession = lambda *args, **kwargs: session

    class ClientConnectorError(OSError):
        pass

    module.ClientConnectorError = ClientConnectorError
    return module


def _install_stubs():
    hermes_cli = _make_module("hermes_cli")
    hermes_cli.__path__ = []
    subprocess_compat = _make_module(
        "hermes_cli._subprocess_compat",
        windows_detach_popen_kwargs=lambda: {},
    )
    hermes_cli._subprocess_compat = subprocess_compat

    hermes_constants = _make_module(
        "hermes_constants",
        find_node_executable=lambda name: "/usr/bin/node",
        get_hermes_home=lambda: Path(tempfile.gettempdir()) / "zalo-test-hermes-home",
        with_hermes_node_path=lambda: dict(os.environ),
    )

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
        spec = importlib.util.spec_from_file_location("zalo_platform_adapter", _ADAPTER_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


class ZaloAdapterPluginTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.adapter = _load_adapter_module()

    def _config(self, **extra):
        return types.SimpleNamespace(extra=extra, enabled=True)

    def _session_dir(self) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Path(directory.name)

    def _build_adapter(self, session: Path, **extra):
        return self.adapter.ZaloAdapter(
            self._config(session_path=str(session), **extra)
        )

    def _group_message(self, **overrides):
        value = {
            "messageId": "m1",
            "threadId": "123456",
            "threadType": "group",
            "groupName": "Team",
            "senderId": "789",
            "senderName": "Nam",
            "text": "hello",
            "timestamp": 1,
            "mentionsSelf": False,
            "quotesSelf": False,
            "media": [],
        }
        value.update(overrides)
        return value

    def test_default_bridge_port_is_3110_and_never_3000(self):
        with tempfile.TemporaryDirectory() as root:
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "HERMES_PROFILES_ROOT": "",
                        "HERMES_ROOT_PROFILE": "",
                        "HERMES_HOME": "",
                    },
                ),
                mock.patch.object(
                    self.adapter,
                    "get_hermes_home",
                    return_value=Path(root) / "missing-profile",
                ),
            ):
                instance = self.adapter.ZaloAdapter(self._config())

        self.assertEqual(instance._bridge_port, 3110)
        self.assertNotEqual(instance._bridge_port, 3000)

    def test_bridge_port_override_and_reserved_port_fallback(self):
        with tempfile.TemporaryDirectory() as root:
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "HERMES_PROFILES_ROOT": "",
                        "HERMES_ROOT_PROFILE": "",
                        "HERMES_HOME": "",
                    },
                ),
                mock.patch.object(
                    self.adapter,
                    "get_hermes_home",
                    return_value=Path(root) / "missing-profile",
                ),
            ):
                self.assertEqual(
                    self.adapter.ZaloAdapter(self._config(bridge_port="3211"))._bridge_port,
                    3211,
                )
                self.assertEqual(
                    self.adapter.ZaloAdapter(self._config(bridge_port=3000))._bridge_port,
                    3110,
                )
                self.assertEqual(self.adapter._coerce_port("not-a-port"), 3110)

    def test_profile_index_uses_sorted_profiles_root(self):
        with tempfile.TemporaryDirectory() as root:
            profiles = Path(root)
            for name in ("zeta", "alpha", "beta"):
                (profiles / name).mkdir()
            active = profiles / "beta"

            self.assertEqual(
                self.adapter._profile_index(
                    home=active, profiles_root=str(profiles), root_profile=""
                ),
                1,
            )
            self.assertEqual(
                self.adapter._profile_index(
                    home=active, profiles_root=str(profiles), root_profile=str(active)
                ),
                0,
            )
            self.assertEqual(
                self.adapter._profile_index(
                    home=profiles / "missing", profiles_root=str(profiles), root_profile=""
                ),
                0,
            )
            # Fallback: parent directory of HERMES_HOME.
            self.assertEqual(
                self.adapter._profile_index(
                    home=active, profiles_root="", root_profile=""
                ),
                1,
            )

    def test_default_bridge_port_adds_profile_index(self):
        with tempfile.TemporaryDirectory() as root:
            profiles = Path(root)
            for name in ("alpha", "beta"):
                (profiles / name).mkdir()
            active = profiles / "beta"
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "HERMES_PROFILES_ROOT": str(profiles),
                        "HERMES_ROOT_PROFILE": str(profiles / "alpha"),
                    },
                ),
                mock.patch.object(self.adapter, "get_hermes_home", return_value=active),
            ):
                self.assertEqual(self.adapter._default_bridge_port(), 3111)
                self.assertEqual(
                    self.adapter.ZaloAdapter(self._config())._bridge_port, 3111
                )
                self.assertEqual(self.adapter._coerce_port(3000), 3111)
                self.assertEqual(
                    self.adapter.ZaloAdapter(self._config(bridge_port=3211))._bridge_port,
                    3211,
                )

    def test_default_session_directory_is_profile_zalo_dir(self):
        instance = self.adapter.ZaloAdapter(self._config())

        self.assertEqual(instance._session_path.name, "zalo")
        self.assertEqual(
            instance._session_path.parent,
            Path(tempfile.gettempdir()) / "zalo-test-hermes-home",
        )

    def test_register_declares_zalo_env_contract(self):
        captured = {}

        class Context:
            def register_platform(self, **kwargs):
                captured.update(kwargs)

        self.adapter.register(Context())

        self.assertEqual(captured["name"], "zalo")
        self.assertEqual(captured["label"], "Zalo")
        self.assertEqual(captured["allowed_users_env"], "ZALO_ALLOWED_USERS")
        self.assertEqual(captured["allow_all_env"], "ZALO_ALLOW_ALL_USERS")
        self.assertEqual(captured["cron_deliver_env_var"], "ZALO_HOME_CHANNEL")
        self.assertTrue(callable(captured["standalone_sender_fn"]))
        self.assertTrue(callable(captured["check_fn"]))
        self.assertEqual(captured["max_message_length"], 2000)

    def test_is_connected_requires_config_or_zalo_enabled(self):
        config = types.SimpleNamespace(enabled=True)
        gateway_module = types.ModuleType("hermes_cli.gateway")
        gateway_module.get_env_value = lambda name: os.environ.get(name, "")
        with mock.patch.dict(sys.modules, {"hermes_cli.gateway": gateway_module}):
            with mock.patch.dict(os.environ, {"ZALO_ENABLED": ""}):
                self.assertFalse(self.adapter._is_connected(config))
            with mock.patch.dict(os.environ, {"ZALO_ENABLED": "true"}):
                self.assertTrue(self.adapter._is_connected(config))

    def test_stale_script_hash_reports_restart_needed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            script = Path(temp_dir) / "bridge.js"
            script.write_text("console.log('bridge');\n", encoding="utf-8")
            disk_hash = self.adapter._file_content_hash(script)

            self.assertTrue(disk_hash)
            self.assertTrue(
                self.adapter._bridge_is_current({"scriptHash": disk_hash}, script)
            )
            self.assertFalse(
                self.adapter._bridge_is_current({"scriptHash": "0" * 16}, script)
            )
            self.assertFalse(self.adapter._bridge_is_current({}, script))

    def test_standalone_sender_errors_when_bridge_is_down(self):
        try:
            import aiohttp  # noqa: F401
        except ImportError:
            self.skipTest("aiohttp is not installed")

        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        finally:
            probe.close()

        result = asyncio.run(
            self.adapter._standalone_send(
                self._config(bridge_port=port),
                "123456789",
                "hello",
            )
        )

        self.assertIn("error", result)
        self.assertIn("not running", result["error"])

    def test_standalone_sender_rejects_media_and_empty_text(self):
        media_result = asyncio.run(
            self.adapter._standalone_send(
                self._config(),
                "123456789",
                "hello",
                media_files=[("/tmp/image.png", False)],
            )
        )
        empty_result = asyncio.run(
            self.adapter._standalone_send(self._config(), "123456789", "   ")
        )

        self.assertIn("error", media_result)
        self.assertIn("text-only", media_result["error"])
        self.assertIn("error", empty_result)

    async def test_poll_sends_the_bearer_token(self):
        session = self._session_dir()
        (session / "bridge.token").write_text("token-1\n", encoding="utf-8")
        adapter = self._build_adapter(session, bridge_port=3210)
        fake = _FakeHTTP(payload=[])
        adapter._running = True
        adapter._http_session = fake

        async def stop(*args, **kwargs):
            adapter._running = False

        fake.on_get = lambda: setattr(adapter, "_running", False)
        with mock.patch.object(self.adapter.asyncio, "sleep", stop):
            await adapter._poll_messages()

        self.assertEqual(len(fake.calls), 1)
        self.assertTrue(fake.calls[0]["url"].endswith(":3210/messages"))
        self.assertEqual(
            fake.calls[0]["headers"], {"Authorization": "Bearer token-1"}
        )

    async def test_poll_makes_no_request_without_the_token(self):
        adapter = self._build_adapter(self._session_dir(), bridge_port=3211)
        fake = _FakeHTTP(payload=[])
        adapter._running = True
        adapter._http_session = fake

        async def stop(*args, **kwargs):
            adapter._running = False

        with mock.patch.object(self.adapter.asyncio, "sleep", stop):
            await adapter._poll_messages()

        self.assertEqual(fake.calls, [])

    async def test_send_sends_the_token_and_thread_type(self):
        session = self._session_dir()
        (session / "bridge.token").write_text("token-1", encoding="utf-8")
        adapter = self._build_adapter(session, bridge_port=3212)
        fake = _FakeHTTP(
            payload={"success": True, "messageId": "33", "messageIds": ["32", "33"]}
        )
        adapter._running = True
        adapter._http_session = fake
        long_text = "x" * 2500

        with mock.patch.dict(sys.modules, {"aiohttp": _fake_aiohttp(fake)}):
            result = await adapter.send(
                "999", long_text, metadata={"chat_type": "group"}
            )

        self.assertTrue(result.success)
        self.assertEqual(result.message_id, "33")
        self.assertEqual(result.continuation_message_ids, ("32",))
        typing_call, send_call = fake.calls
        self.assertTrue(typing_call["url"].endswith(":3212/typing"))
        self.assertEqual(
            typing_call["json"], {"chatId": "999", "threadType": "group"}
        )
        self.assertTrue(send_call["url"].endswith(":3212/send"))
        self.assertEqual(send_call["json"]["threadType"], "group")
        self.assertEqual(send_call["json"]["message"], long_text)
        for call in fake.calls:
            self.assertEqual(call["headers"], {"Authorization": "Bearer token-1"})

    async def test_send_without_the_token_makes_no_request(self):
        adapter = self._build_adapter(self._session_dir(), bridge_port=3214)
        fake = _FakeHTTP(payload={"success": True, "messageId": "33"})
        adapter._running = True
        adapter._http_session = fake

        with mock.patch.dict(sys.modules, {"aiohttp": _fake_aiohttp(fake)}):
            result = await adapter.send("999", "hello")

        self.assertFalse(result.success)
        self.assertEqual(fake.calls, [])

    async def test_send_reuses_the_remembered_group_thread_type(self):
        session = self._session_dir()
        (session / "bridge.token").write_text("token-1", encoding="utf-8")
        adapter = self._build_adapter(session, bridge_port=3215)
        fake = _FakeHTTP(payload={"success": True, "messageId": "33"})
        adapter._running = True
        adapter._http_session = fake
        adapter._remember_thread("999", "group")

        with mock.patch.dict(sys.modules, {"aiohttp": _fake_aiohttp(fake)}):
            await adapter.send("999", "hello")

        self.assertEqual(fake.calls[-1]["json"]["threadType"], "group")

    async def test_auto_reply_is_off_by_default(self):
        session = self._session_dir()
        (session / "bridge.token").write_text("token-1", encoding="utf-8")
        adapter = self._build_adapter(session, bridge_port=3216)
        fake = _FakeHTTP(payload={"success": True})
        adapter._running = True
        adapter._http_session = fake

        with mock.patch.dict(sys.modules, {"aiohttp": _fake_aiohttp(fake)}):
            await adapter._route_messages(
                [
                    {
                        "messageId": "m1",
                        "threadId": "555",
                        "threadType": "group",
                        "senderId": "9",
                        "senderName": "Nam",
                        "text": "hi",
                        "mentionsSelf": True,
                    }
                ]
            )

        self.assertEqual(adapter.handled, [])
        self.assertEqual(fake.calls, [])
        # The thread kind is still remembered for approved tool sends.
        self.assertEqual(adapter._thread_type_for("555"), "group")

    async def test_seen_is_posted_after_the_event(self):
        session = self._session_dir()
        (session / "bridge.token").write_text("token-1", encoding="utf-8")
        adapter = self._build_adapter(session, bridge_port=3216, auto_reply=True)
        fake = _FakeHTTP(payload={"success": True})
        adapter._running = True
        adapter._http_session = fake

        with mock.patch.dict(sys.modules, {"aiohttp": _fake_aiohttp(fake)}):
            await adapter._route_messages(
                [
                    {
                        "messageId": "m1",
                        "threadId": "123",
                        "threadType": "user",
                        "senderId": "9",
                        "senderName": "Nam",
                        "text": "hi",
                    }
                ]
            )

        self.assertEqual(len(adapter.handled), 1)
        call = fake.calls[-1]
        self.assertTrue(call["url"].endswith(":3216/seen"))
        self.assertEqual(call["json"], {"messageId": "m1"})
        self.assertEqual(call["headers"], {"Authorization": "Bearer token-1"})

    async def test_standalone_sender_sends_the_token_and_thread_type(self):
        session = self._session_dir()
        (session / "bridge.token").write_text("token-1", encoding="utf-8")
        fake = _FakeHTTP(payload={"success": True, "messageId": "42"})
        config = self._config(session_path=str(session), bridge_port=3217)

        with mock.patch.dict(sys.modules, {"aiohttp": _fake_aiohttp(fake)}):
            result = await self.adapter._standalone_send(
                config, "123456789", "hello", thread_type="group"
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["message_id"], "42")
        call = fake.calls[0]
        self.assertEqual(call["json"]["threadType"], "group")
        self.assertEqual(call["headers"], {"Authorization": "Bearer token-1"})

    async def test_standalone_sender_without_token_makes_no_request(self):
        fake = _FakeHTTP(payload={"success": True, "messageId": "42"})
        config = self._config(
            session_path=str(self._session_dir()), bridge_port=3218
        )

        with mock.patch.dict(sys.modules, {"aiohttp": _fake_aiohttp(fake)}):
            result = await self.adapter._standalone_send(config, "123456789", "hello")

        self.assertIn("error", result)
        self.assertIn("not running", result["error"])
        self.assertEqual(fake.calls, [])

    def test_group_message_event_uses_the_group_source(self):
        adapter = self._build_adapter(self._session_dir())

        event = adapter._build_message_event(
            self._group_message(mentionsSelf=True)
        )

        self.assertIsNotNone(event)
        self.assertEqual(event.text, "hello")
        self.assertEqual(event.source["chat_type"], "group")
        self.assertEqual(event.source["chat_id"], "123456")
        self.assertEqual(event.source["chat_name"], "Team")
        self.assertEqual(event.source["user_id"], "789")
        self.assertEqual(event.source["user_name"], "Nam")

    def test_group_mention_gating_buffers_and_replays(self):
        adapter = self._build_adapter(self._session_dir())

        for index in range(60):
            event = adapter._build_message_event(
                self._group_message(messageId=f"m{index}", text=f"line {index}")
            )
            self.assertIsNone(event)

        backlog = adapter._group_backlog["123456"]
        self.assertEqual(len(backlog), 50)
        self.assertEqual(backlog[0], ("Nam", "line 10"))

        event = adapter._build_message_event(
            self._group_message(messageId="target", text="question", mentionsSelf=True)
        )

        self.assertIsNotNone(event)
        lines = event.text.splitlines()
        self.assertEqual(lines[0], "[Earlier messages in this group, not addressed to you]")
        self.assertEqual(lines[1], "Nam: line 10")
        self.assertEqual(lines[-2], "[Message to you]")
        self.assertEqual(lines[-1], "question")
        self.assertNotIn("123456", adapter._group_backlog)

    def test_group_quote_counts_as_a_mention(self):
        adapter = self._build_adapter(self._session_dir())

        event = adapter._build_message_event(
            self._group_message(quotesSelf=True, mentionsSelf=False)
        )

        self.assertIsNotNone(event)
        self.assertEqual(event.text, "hello")

    def test_require_mention_false_processes_every_group_message(self):
        adapter = self._build_adapter(self._session_dir(), require_mention=False)

        event = adapter._build_message_event(self._group_message())

        self.assertIsNotNone(event)
        self.assertEqual(event.text, "hello")
        self.assertEqual(adapter._group_backlog, {})

    def test_direct_messages_ignore_mention_gating(self):
        adapter = self._build_adapter(self._session_dir(), require_mention=True)

        event = adapter._build_message_event(
            {
                "messageId": "m1",
                "threadId": "123",
                "threadType": "user",
                "senderId": "9",
                "senderName": "Nam",
                "text": "hello",
            }
        )

        self.assertIsNotNone(event)
        self.assertEqual(event.source["chat_type"], "dm")

    def test_inbound_media_becomes_a_text_note(self):
        adapter = self._build_adapter(self._session_dir())

        event = adapter._build_message_event(
            self._group_message(
                mentionsSelf=True,
                text="",
                media=[
                    {"kind": "image", "name": "photo.jpg", "sizeBytes": 1024},
                    {"kind": "file", "name": "report.pdf", "sizeBytes": None},
                ],
            )
        )

        self.assertIsNotNone(event)
        self.assertEqual(
            event.text.splitlines(),
            [
                "[Zalo image attachment: photo.jpg, not available to the agent]",
                "[Zalo file attachment: report.pdf, not available to the agent]",
            ],
        )

    def test_media_over_the_default_limit_uses_the_limit_note(self):
        adapter = self._build_adapter(self._session_dir())

        event = adapter._build_message_event(
            self._group_message(
                mentionsSelf=True,
                text="see attached",
                media=[
                    {
                        "kind": "video",
                        "name": "clip.mp4",
                        "sizeBytes": 11 * 1024 * 1024,
                    }
                ],
            )
        )

        self.assertIsNotNone(event)
        self.assertEqual(
            event.text,
            "see attached\n"
            "[Zalo video attachment: clip.mp4, over the 10 MB limit]",
        )

    def test_media_limit_uses_the_configured_value(self):
        adapter = self._build_adapter(self._session_dir(), media_max_mb=1)

        event = adapter._build_message_event(
            self._group_message(
                mentionsSelf=True,
                text="",
                media=[
                    {"kind": "image", "name": "big.png", "sizeBytes": 2 * 1024 * 1024}
                ],
            )
        )

        self.assertIsNotNone(event)
        self.assertEqual(
            event.text,
            "[Zalo image attachment: big.png, over the 1 MB limit]",
        )

    def _media_adapter(self, **extra):
        root = self._session_dir()
        session = root / "zalo"
        workspace = root / "workspace"
        session.mkdir(parents=True, exist_ok=True)
        workspace.mkdir(parents=True, exist_ok=True)
        adapter = self._build_adapter(
            session, auto_reply=True, workspace_path=str(workspace), **extra
        )
        return adapter, session, workspace

    @staticmethod
    def _stream_http(responses):
        """Return a fake aiohttp ClientSession serving scripted GET responses."""

        class _StreamContent:
            def __init__(self, chunks):
                self._chunks = chunks

            async def iter_chunked(self, _size):
                for chunk in self._chunks:
                    yield chunk

        class _StreamResp:
            def __init__(self, status=200, chunks=(), headers=None):
                self.status = status
                self.headers = headers or {}
                self.content = _StreamContent(chunks)

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class _Session:
            def __init__(self):
                self.calls = []
                self._queue = list(responses)

            def get(self, url, **kwargs):
                self.calls.append((url, kwargs))
                spec = self._queue.pop(0)
                return _StreamResp(
                    status=spec.get("status", 200),
                    chunks=spec.get("chunks", ()),
                    headers=spec.get("headers"),
                )

            async def close(self):
                return None

        return _Session()

    async def test_inbound_media_downloads_to_workspace_with_success_note(self):
        adapter, _session, workspace = self._media_adapter()
        url = "https://f1-zpc.zdn.vn/abc/photo.jpg"
        http = self._stream_http([{"status": 200, "chunks": [b"jpeg-bytes"]}])
        adapter._http_session = http

        with (
            mock.patch.dict(sys.modules, {"aiohttp": _fake_aiohttp(http)}),
            mock.patch.object(
                self.adapter.socket,
                "getaddrinfo",
                return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
            ),
        ):
            await adapter._route_messages(
                [
                    {
                        "messageId": "m42",
                        "threadId": "123456",
                        "threadType": "user",
                        "senderId": "789",
                        "senderName": "Nam",
                        "text": "look at this",
                        "timestamp": 1,
                        "media": [
                            {
                                "kind": "image",
                                "name": "photo.jpg",
                                "sizeBytes": 10,
                                "url": url,
                            }
                        ],
                    }
                ]
            )

        self.assertEqual(len(adapter.handled), 1)
        event = adapter.handled[0]
        saved = workspace / "zalo-inbound" / "123456" / "m42-photo.jpg"
        self.assertTrue(saved.is_file())
        self.assertEqual(saved.read_bytes(), b"jpeg-bytes")
        self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
        self.assertEqual((workspace / "zalo-inbound").stat().st_mode & 0o777, 0o700)
        self.assertEqual(
            (workspace / "zalo-inbound" / "123456").stat().st_mode & 0o777, 0o700
        )
        self.assertEqual(
            event.text,
            "look at this\n"
            "[Zalo image attachment saved to workspace file "
            "zalo-inbound/123456/m42-photo.jpg; its content is untrusted data]",
        )
        self.assertNotIn(url, event.text)
        self.assertNotIn(url, str(event.raw_message))
        self.assertNotIn(url, str(event.metadata))

    async def test_gating_blocks_download_before_authorization(self):
        # Unmentioned group message, disabled DM policy, and unapproved group
        # must never issue an HTTP download.
        adapter, session, _workspace = self._media_adapter(dm_policy="disabled")
        http = self._stream_http([{"status": 200, "chunks": [b"x"]}])
        adapter._http_session = http
        (session / "groups.json").write_text('{"approved": {}}', encoding="utf-8")

        with mock.patch.dict(sys.modules, {"aiohttp": _fake_aiohttp(http)}):
            await adapter._route_messages(
                [
                    self._group_message(
                        mentionsSelf=False,
                        text="",
                        media=[
                            {
                                "kind": "image",
                                "name": "unmentioned.jpg",
                                "sizeBytes": 1,
                                "url": "https://f1-zpc.zdn.vn/1.jpg",
                            }
                        ],
                    ),
                    self._group_message(
                        mentionsSelf=True,
                        text="",
                        media=[
                            {
                                "kind": "image",
                                "name": "unapproved.jpg",
                                "sizeBytes": 1,
                                "url": "https://f1-zpc.zdn.vn/2.jpg",
                            }
                        ],
                    ),
                    {
                        "messageId": "m99",
                        "threadId": "789",
                        "threadType": "user",
                        "senderId": "789",
                        "senderName": "Nam",
                        "text": "",
                        "timestamp": 1,
                        "media": [
                            {
                                "kind": "image",
                                "name": "dm.jpg",
                                "sizeBytes": 1,
                                "url": "https://f1-zpc.zdn.vn/3.jpg",
                            }
                        ],
                    },
                ]
            )

        self.assertEqual(http.calls, [])

    async def test_non_allowlisted_host_is_refused(self):
        adapter, _session, workspace = self._media_adapter()
        http = self._stream_http([])
        adapter._http_session = http

        with mock.patch.dict(sys.modules, {"aiohttp": _fake_aiohttp(http)}):
            await adapter._route_messages(
                [
                    {
                        "messageId": "m1",
                        "threadId": "100",
                        "threadType": "user",
                        "senderId": "100",
                        "senderName": "Nam",
                        "text": "",
                        "timestamp": 1,
                        "media": [
                            {
                                "kind": "file",
                                "name": "evil.pdf",
                                "sizeBytes": 10,
                                "url": "https://evil.example.com/evil.pdf",
                            }
                        ],
                    }
                ]
            )

        self.assertEqual(http.calls, [])
        self.assertEqual(
            adapter.handled[0].text,
            "[Zalo file attachment: evil.pdf, unavailable]",
        )
        self.assertFalse((workspace / "zalo-inbound").exists())

    async def test_private_ip_dns_resolution_is_refused(self):
        adapter, _session, _workspace = self._media_adapter()
        http = self._stream_http([])
        adapter._http_session = http

        with (
            mock.patch.dict(sys.modules, {"aiohttp": _fake_aiohttp(http)}),
            mock.patch.object(
                self.adapter.socket,
                "getaddrinfo",
                return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
            ),
        ):
            await adapter._route_messages(
                [
                    {
                        "messageId": "m2",
                        "threadId": "100",
                        "threadType": "user",
                        "senderId": "100",
                        "senderName": "Nam",
                        "text": "",
                        "timestamp": 1,
                        "media": [
                            {
                                "kind": "image",
                                "name": "ssrf.jpg",
                                "sizeBytes": 10,
                                "url": "https://f1-zpc.zdn.vn/ssrf.jpg",
                            }
                        ],
                    }
                ]
            )

        self.assertEqual(http.calls, [])
        self.assertEqual(
            adapter.handled[0].text,
            "[Zalo image attachment: ssrf.jpg, unavailable]",
        )

    async def test_redirect_to_non_allowlisted_host_is_refused(self):
        adapter, _session, _workspace = self._media_adapter()
        http = self._stream_http(
            [
                {
                    "status": 302,
                    "headers": {"Location": "https://169.254.169.254/latest"},
                }
            ]
        )
        adapter._http_session = http

        with (
            mock.patch.dict(sys.modules, {"aiohttp": _fake_aiohttp(http)}),
            mock.patch.object(
                self.adapter.socket,
                "getaddrinfo",
                return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
            ),
        ):
            await adapter._route_messages(
                [
                    {
                        "messageId": "m3",
                        "threadId": "100",
                        "threadType": "user",
                        "senderId": "100",
                        "senderName": "Nam",
                        "text": "",
                        "timestamp": 1,
                        "media": [
                            {
                                "kind": "image",
                                "name": "redir.jpg",
                                "sizeBytes": 10,
                                "url": "https://f1-zpc.zdn.vn/redir.jpg",
                            }
                        ],
                    }
                ]
            )

        self.assertEqual(len(http.calls), 1)
        self.assertEqual(
            adapter.handled[0].text,
            "[Zalo image attachment: redir.jpg, unavailable]",
        )

    async def test_streamed_size_cap_aborts_and_deletes_partial_file(self):
        adapter, _session, workspace = self._media_adapter(media_max_mb=0.0001)
        # 0.0001 MB = 104 bytes; send 200 bytes with missing sizeBytes.
        http = self._stream_http(
            [{"status": 200, "chunks": [b"a" * 80, b"b" * 120]}]
        )
        adapter._http_session = http

        with (
            mock.patch.dict(sys.modules, {"aiohttp": _fake_aiohttp(http)}),
            mock.patch.object(
                self.adapter.socket,
                "getaddrinfo",
                return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
            ),
        ):
            await adapter._route_messages(
                [
                    {
                        "messageId": "m4",
                        "threadId": "100",
                        "threadType": "user",
                        "senderId": "100",
                        "senderName": "Nam",
                        "text": "",
                        "timestamp": 1,
                        "media": [
                            {
                                "kind": "file",
                                "name": "huge.bin",
                                "sizeBytes": None,
                                "url": "https://f1-zpc.zdn.vn/huge.bin",
                            }
                        ],
                    }
                ]
            )

        self.assertEqual(
            adapter.handled[0].text,
            "[Zalo file attachment: huge.bin, over the 0.0001 MB limit]",
        )
        thread_dir = workspace / "zalo-inbound" / "100"
        self.assertEqual(list(thread_dir.iterdir()), [])

    async def test_path_sanitizing_prevents_escape_and_symlink_traversal(self):
        adapter, _session, workspace = self._media_adapter()
        http = self._stream_http([{"status": 200, "chunks": [b"ok"]}])
        adapter._http_session = http

        with (
            mock.patch.dict(sys.modules, {"aiohttp": _fake_aiohttp(http)}),
            mock.patch.object(
                self.adapter.socket,
                "getaddrinfo",
                return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
            ),
        ):
            await adapter._route_messages(
                [
                    {
                        "messageId": "../../m5",
                        "threadId": "../escape",
                        "threadType": "user",
                        "senderId": "100",
                        "senderName": "Nam",
                        "text": "",
                        "timestamp": 1,
                        "media": [
                            {
                                "kind": "file",
                                "name": "../../secret.txt",
                                "sizeBytes": 2,
                                "url": "https://f1-zpc.zdn.vn/secret.txt",
                            }
                        ],
                    }
                ]
            )

        saved = workspace / "zalo-inbound" / "escape" / "m5-secret.txt"
        self.assertTrue(saved.is_file())
        self.assertTrue(saved.resolve().is_relative_to(workspace.resolve()))
        self.assertIn(
            "zalo-inbound/escape/m5-secret.txt; its content is untrusted data]",
            adapter.handled[0].text,
        )

    def test_retention_cleanup_deletes_old_files_and_keeps_recent(self):
        adapter, _session, workspace = self._media_adapter(history_retention_days=7)
        thread_dir = workspace / "zalo-inbound" / "100"
        thread_dir.mkdir(parents=True)
        old_file = thread_dir / "m1-old.jpg"
        new_file = thread_dir / "m2-new.jpg"
        old_file.write_bytes(b"old")
        new_file.write_bytes(b"new")
        now = 1_700_000_000.0
        os.utime(old_file, (now - 8 * 86400, now - 8 * 86400))
        os.utime(new_file, (now - 2 * 86400, now - 2 * 86400))

        removed = adapter._prune_inbound_media(now=now)

        self.assertEqual(removed, 1)
        self.assertFalse(old_file.exists())
        self.assertTrue(new_file.exists())


if __name__ == "__main__":
    unittest.main()
