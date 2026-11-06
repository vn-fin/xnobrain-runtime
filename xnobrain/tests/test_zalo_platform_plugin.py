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

    def truncate_message(self, text, limit):
        if not text:
            return [text]
        return [text[index : index + limit] for index in range(0, len(text), limit)]


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


class ZaloAdapterPluginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.adapter = _load_adapter_module()

    def _config(self, **extra):
        return types.SimpleNamespace(extra=extra, enabled=True)

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


if __name__ == "__main__":
    unittest.main()
