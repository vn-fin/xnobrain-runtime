"""Runtime genericity test for the shared channel platform registry.

Every entry in ``PLATFORMS`` must be fully wired: an importable tools module, a
packaged skill section, the shared connect/delegation/turn surfaces and, for a
token platform, the validation-redaction prefix.
"""

import importlib
import re
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from xnobrain.handlers.extension_validation import _REDACTED_OPERATION_PREFIXES
from xnobrain.integrations import channel_tools, gateway_cli, hermes_support, zalo_channels
from xnobrain.integrations.channel_platforms import (
    PLATFORMS,
    get_platform,
    matches_id_pattern,
    platform_ids,
)
from xnobrain.integrations.integration_connect_tools import (
    CHANNEL_PLATFORMS as CONNECT_PLATFORMS,
)
from xnobrain.integrations.integration_connect_tools import (
    IntegrationConnectArguments,
    _channel_request,
)
from xnobrain.models.channels import (
    ChannelPairingListResult,
    ChannelState,
    ChannelTurnRequest,
    SendChannelMessageRequest,
)

_SKILL_DIR = (
    Path(__file__).resolve().parent.parent
    / "assets"
    / "skills"
    / "social-channels"
    / "platforms"
)
_VALID_SAMPLES = (
    "123456789",
    "C0123ABCDEF",
    "chat_123",
    "user-1",
    "abc123",
    "user@example.com",
    "3uo8dkh1p7g1mfk49ear5fzs5c",
    "#hermes",
    "direct:user123",
)
_UNSUPPORTED_ID = "signal"


def _sample_id(entry) -> str:
    for candidate in _VALID_SAMPLES:
        if re.fullmatch(entry.id_pattern, candidate):
            return candidate
    raise AssertionError(f"{entry.id} declares no usable sample id")


def _turn_body(platform: str, identifier: str) -> dict:
    return {
        "platform": platform,
        "agent_id": "agent-1",
        "thread_id": identifier,
        "thread_type": "dm",
        "sender_id": identifier,
        "text": "hello",
        "message_id": "message-1",
    }


def _send_tool_properties() -> dict:
    return channel_tools._SCHEMA["function"]["parameters"]["properties"]


class ChannelPlatformRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        profile = Path(temporary.name) / "agent-1"
        profile.mkdir()
        channel_tools.register_tools(profile)

    def test_registry_entries_are_consistent(self):
        self.assertEqual(platform_ids(), tuple(PLATFORMS))
        for entry in PLATFORMS.values():
            with self.subTest(platform=entry.id):
                self.assertEqual(PLATFORMS[entry.id], entry)
                self.assertIs(get_platform(entry.id), entry)
                self.assertTrue(matches_id_pattern(entry.id, _sample_id(entry)))
                self.assertIn(entry.auth, {"qr", "token"})
                self.assertGreater(entry.max_message_length, 0)
                self.assertTrue(entry.skill_section.endswith(".md"))
        self.assertIsNone(get_platform(_UNSUPPORTED_ID))

    def test_every_platform_tools_module_imports_and_installs(self):
        for entry in PLATFORMS.values():
            with self.subTest(platform=entry.id):
                if entry.tools_module is None:
                    # Parity-only platforms reuse the Hermes adapter and add no
                    # Runtime tool module.
                    continue
                module = importlib.import_module(entry.tools_module)
                self.assertTrue(callable(getattr(module, "install_tools", None)))

    def test_every_platform_has_a_packaged_skill_section(self):
        for entry in PLATFORMS.values():
            with self.subTest(platform=entry.id):
                path = _SKILL_DIR / entry.skill_section
                self.assertTrue(path.is_file(), path)
                self.assertTrue(path.read_text(encoding="utf-8").strip())

    def test_every_platform_is_wired_into_the_shared_surfaces(self):
        ids = set(platform_ids())
        self.assertEqual(hermes_support.CHANNEL_CONNECT_PLATFORMS, ids)
        self.assertEqual(CONNECT_PLATFORMS, ids)
        self.assertEqual(gateway_cli._DELEGATED_PLATFORMS, ids)
        self.assertEqual(
            gateway_cli._GROUP_PLATFORMS,
            {entry.id for entry in PLATFORMS.values() if entry.groups},
        )
        self.assertEqual(_send_tool_properties()["platform"]["enum"], list(platform_ids()))

    def test_every_platform_has_a_connect_card(self):
        for entry in PLATFORMS.values():
            with self.subTest(platform=entry.id):
                self.assertEqual(
                    hermes_support.CHANNEL_CONNECT_LABELS[entry.id],
                    (entry.label, entry.connect_auth_kind),
                )
                self.assertIn(
                    entry.connect_auth_kind, hermes_support.CHANNEL_CONNECT_AUTH_KINDS
                )
                request = IntegrationConnectArguments(kind="channel", platform=entry.id)
                payload = _channel_request(request, "agent-1")
                self.assertEqual(payload["platform"], entry.id)
                self.assertEqual(payload["display_name"], entry.label)
                self.assertEqual(payload["auth_kind"], entry.connect_auth_kind)

    def test_turn_request_validates_against_the_registry(self):
        for entry in PLATFORMS.values():
            with self.subTest(platform=entry.id):
                identifier = _sample_id(entry)
                request = ChannelTurnRequest.model_validate(
                    _turn_body(entry.id, identifier)
                )
                self.assertEqual(request.platform, entry.id)
                with self.assertRaises(ValidationError):
                    ChannelTurnRequest.model_validate(_turn_body(entry.id, "bad/id"))
                with self.assertRaises(ValidationError):
                    ChannelTurnRequest.model_validate(
                        _turn_body(_UNSUPPORTED_ID, identifier)
                    )
                state = ChannelState(platform=entry.id, enabled=False, status="disabled")
                self.assertEqual(state.platform, entry.id)
                pairing = ChannelPairingListResult(platform=entry.id, dm_policy="disabled")
                self.assertEqual(pairing.platform, entry.id)
                send = SendChannelMessageRequest(
                    platform=entry.id, recipient="recipient", text="hello"
                )
                self.assertEqual(send.platform, entry.id)

    def test_unsupported_platforms_are_rejected_everywhere(self):
        with self.assertRaises(ValidationError):
            SendChannelMessageRequest(platform=_UNSUPPORTED_ID, recipient="x", text="y")
        with self.assertRaises(ValidationError):
            ChannelState(platform=_UNSUPPORTED_ID, enabled=False, status="disabled")
        with self.assertRaises(ValidationError):
            IntegrationConnectArguments(kind="channel", platform=_UNSUPPORTED_ID)

    def test_token_platforms_redact_validation_bodies(self):
        self.assertIn("extensions_", _REDACTED_OPERATION_PREFIXES)
        for entry in PLATFORMS.values():
            if entry.token_operation_prefix is None:
                continue
            with self.subTest(platform=entry.id):
                self.assertIn(entry.token_operation_prefix, _REDACTED_OPERATION_PREFIXES)

    def test_declared_message_limits_match_the_send_tool(self):
        self.assertEqual(
            zalo_channels.DEFAULT_MAX_MESSAGE_LENGTH,
            PLATFORMS[zalo_channels.ZALO_PLATFORM].max_message_length,
        )
        self.assertEqual(
            _send_tool_properties()["text"]["maxLength"],
            zalo_channels.max_message_length(),
        )


if __name__ == "__main__":
    unittest.main()
