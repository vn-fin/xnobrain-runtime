"""Focused tests for the vendored Zalo Bot platform plugin.

The plugin lives outside the Python package tree and targets the Hermes plugin
API, so this module loads the vendored directory as a package with a minimal
Hermes stub. That keeps the test independent of whether Hermes is importable on
the host and of the engine package layout.
"""

import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PLUGIN_DIR = (
    _REPO_ROOT / "runtime" / "hermes-plugins" / "platforms" / "zalo_bot"
)
_PACKAGE_NAME = "zalo_bot_plugin"
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
        get_hermes_home=lambda: Path(tempfile.gettempdir()) / "zalo-bot-test-home",
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


class ZaloBotPluginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package = _load_plugin_package()
        cls.adapter = sys.modules.get(f"{_PACKAGE_NAME}.adapter") or cls.package.adapter

    def _captured_registration(self):
        captured = {}

        class Context:
            def register_platform(self, **kwargs):
                captured.update(kwargs)

        self.adapter.register(Context())
        return captured

    def test_register_declares_zalo_bot_platform_contract(self):
        captured = self._captured_registration()

        self.assertEqual(captured["name"], "zalo_bot")
        self.assertEqual(captured["label"], "Zalo Bot")
        self.assertEqual(captured["required_env"], ["ZALO_BOT_TOKEN"])
        self.assertEqual(captured["allowed_users_env"], "ZALO_BOT_ALLOWED_USERS")
        self.assertEqual(captured["allow_all_env"], "ZALO_BOT_ALLOW_ALL_USERS")
        self.assertEqual(captured["cron_deliver_env_var"], "ZALO_BOT_HOME_CHANNEL")

    def test_register_has_no_interactive_setup(self):
        captured = self._captured_registration()

        self.assertNotIn("setup_fn", captured)
        self.assertFalse(hasattr(self.adapter, "interactive_setup"))

    def test_personal_plugin_env_names_are_gone(self):
        forbidden = (
            "ZALO_ALLOWED_USERS",
            "ZALO_ALLOW_ALL_USERS",
            "ZALO_HOME_CHANNEL",
            "ZALO_CRM_DIR",
            "ZALO_HISTORY_DIR",
        )
        for filename in ("adapter.py", "plugin.yaml"):
            text = (_PLUGIN_DIR / filename).read_text(encoding="utf-8")
            for name in forbidden:
                with self.subTest(file=filename, env=name):
                    self.assertNotIn(name, text)


if __name__ == "__main__":
    unittest.main()
