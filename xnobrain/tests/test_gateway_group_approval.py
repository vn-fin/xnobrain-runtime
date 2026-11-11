"""Gateway group authorization, seen reporting, and delegated refusals."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from xnobrain.integrations import gateway_cli

TOKEN = "gateway-group-test-token"
SEEN_ENDPOINT = "http://runtime:3000/xnobrain/api/runtime/v1/internal/channels/groups/seen"
TURN_ENDPOINT = "http://runtime:3000/xnobrain/api/runtime/v1/internal/channels/turn"


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
        await asyncio.sleep(0)
        return self.factory.response


class _ClientFactory:
    def __init__(self, response=None):
        self.response = response or _Response(payload={})
        self.calls: list[dict] = []

    def __call__(self, *args, **kwargs):
        return _Client(self)


class _Adapter:
    def __init__(self, group_name="Test group"):
        self.group_name = group_name

    async def get_group_info(self, group_id):
        return {"ok": True, "result": {"name": self.group_name}}


class _RunnerWithGroupInfo:
    def __init__(self):
        self._group_manager = SimpleNamespace(get_group_info=self.get_group_info)

    async def get_group_info(self, group_id):
        return {"ok": True, "result": {"name": "Test group"}}


def _gateway_module():
    class FakeGatewayRunner:
        _xnobrain_channel_delegation = False
        _xnobrain_group_approval = False
        native_calls: list[tuple] = []
        original_calls: list[tuple] = []

        def _is_user_authorized(self, source, *, allow_adapter_delegation=True):
            type(self).original_calls.append((source, allow_adapter_delegation))
            return "original"

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


def _source(platform="zalo_bot", chat_type="group", chat_id="group_1"):
    return SimpleNamespace(
        platform=SimpleNamespace(value=platform),
        chat_id=chat_id,
        user_id="sender_1",
        user_name="Nam",
        chat_name="Nam",
        chat_type=chat_type,
        message_id="source-message",
    )


def _groups_payload(approved=(), pending=(), daily_limit=20):
    groups = {}
    for group_id in pending:
        groups[group_id] = {"status": "pending", "name": "", "last_sender_name": ""}
    for group_id in approved:
        groups[group_id] = {"status": "approved", "name": ""}
    return {
        "schema_version": 1,
        "platforms": {"zalo_bot": {"daily_limit": daily_limit, "groups": groups}},
    }


class GatewayGroupApprovalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.profile = Path(self.temporary.name) / "agent-1"
        (self.profile / "zalo").mkdir(parents=True)
        self.write_groups(_groups_payload(approved=["group_1"]))
        gateway_cli._GROUP_APPROVED_CACHE.clear()
        gateway_cli._GROUP_SEEN_STATE.clear()
        gateway_cli._GROUP_LIMIT_NOTICE_STATE.clear()

    def write_groups(self, payload):
        (self.profile / "zalo" / "groups.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def install_auth(self):
        module, runner_class = _gateway_module()
        patches = [
            mock.patch.dict(sys.modules, {"gateway.run": module}),
            mock.patch.dict(
                "os.environ",
                {
                    "RUNTIME_INTERNAL_SERVICE_TOKEN": TOKEN,
                    "RUNTIME_INTERNAL_URL": "http://runtime:3000",
                },
            ),
            mock.patch(
                "hermes_constants.get_process_hermes_home", return_value=self.profile
            ),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        gateway_cli.install_gateway_group_approval()
        return runner_class

    def install_delegation(self, factory):
        module, runner_class = _gateway_module()
        patches = [
            mock.patch.dict(sys.modules, {"gateway.run": module}),
            mock.patch.dict(
                "os.environ",
                {
                    "RUNTIME_GATEWAY_CHANNEL_DELEGATION": "true",
                    "RUNTIME_INTERNAL_SERVICE_TOKEN": TOKEN,
                    "RUNTIME_INTERNAL_URL": "http://runtime:3000",
                },
            ),
            mock.patch(
                "hermes_constants.get_hermes_home", return_value=self.profile
            ),
            mock.patch(
                "hermes_constants.get_process_hermes_home", return_value=self.profile
            ),
            mock.patch("httpx.AsyncClient", factory),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        gateway_cli.install_runtime_channel_delegation()
        return runner_class

    async def test_approved_group_is_authorized_without_pairing(self):
        runner_class = self.install_auth()
        runner = runner_class()
        self.assertTrue(runner._is_user_authorized(_source()))
        self.assertEqual(runner_class.original_calls, [])

    async def test_revoked_group_is_denied_and_cached_by_mtime(self):
        runner_class = self.install_auth()
        runner = runner_class()
        runner._adapter_for_source = lambda source: _RunnerWithGroupInfo()
        self.write_groups(_groups_payload(pending=["group_1"]))
        with mock.patch("httpx.AsyncClient", _ClientFactory()):
            self.assertFalse(runner._is_user_authorized(_source()))
            await asyncio.sleep(0.05)
        self.write_groups(_groups_payload(approved=["group_1"]))
        self.assertTrue(runner._is_user_authorized(_source()))

    async def test_unapproved_group_reports_seen_once(self):
        runner_class = self.install_auth()
        runner = runner_class()
        runner._adapter_for_source = lambda source: _RunnerWithGroupInfo()
        factory = _ClientFactory()
        with mock.patch("httpx.AsyncClient", factory):
            self.assertFalse(runner._is_user_authorized(_source(chat_id="group_2")))
            await asyncio.sleep(0.05)
            self.assertFalse(runner._is_user_authorized(_source(chat_id="group_2")))
            await asyncio.sleep(0.05)
        self.assertEqual(len(factory.calls), 1)
        call = factory.calls[0]
        self.assertEqual(call["url"], SEEN_ENDPOINT)
        self.assertEqual(call["headers"], {"x-xnobrain-internal-token": TOKEN})
        self.assertEqual(call["json"]["platform"], "zalo_bot")
        self.assertEqual(call["json"]["agent_id"], "agent-1")
        self.assertEqual(call["json"]["group_id"], "group_2")
        self.assertEqual(call["json"]["group_name"], "Test group")
        self.assertEqual(call["json"]["sender_name"], "Nam")

    async def test_non_group_and_personal_zalo_sources_use_the_original(self):
        runner_class = self.install_auth()
        runner = runner_class()
        for source in (
            _source(platform="zalo", chat_type="group"),
            _source(platform="zalo_bot", chat_type="dm"),
            _source(platform="telegram", chat_type="group"),
        ):
            with self.subTest(source=source):
                self.assertEqual(runner._is_user_authorized(source), "original")
        self.assertEqual(len(runner_class.original_calls), 3)

    async def test_missing_or_corrupt_groups_file_fails_closed(self):
        runner_class = self.install_auth()
        runner = runner_class()
        runner._adapter_for_source = lambda source: _RunnerWithGroupInfo()
        (self.profile / "zalo" / "groups.json").unlink()
        (self.profile / "zalo" / "groups.json").write_text("{not json", encoding="utf-8")
        with mock.patch("httpx.AsyncClient", _ClientFactory()):
            self.assertFalse(runner._is_user_authorized(_source()))
            (self.profile / "zalo" / "groups.json").unlink()
            self.assertFalse(runner._is_user_authorized(_source()))
            await asyncio.sleep(0.05)

    async def test_named_profile_source_reads_its_own_groups_file(self):
        root = Path(self.temporary.name) / "hermes-root"
        named_home = root / "profiles" / "plugin-qa"
        (named_home / "zalo").mkdir(parents=True)
        (named_home / "zalo" / "groups.json").write_text(
            json.dumps(_groups_payload(pending=["group_1"])), encoding="utf-8"
        )
        runner_class = self.install_auth()
        runner = runner_class()
        runner._adapter_profile_for_source = lambda source: "plugin-qa"
        runner._adapter_for_source = lambda source: _RunnerWithGroupInfo()
        factory = _ClientFactory()
        with (
            mock.patch("hermes_constants.get_default_hermes_root", return_value=root),
            mock.patch("httpx.AsyncClient", factory),
        ):
            # The default profile approves this group, so reading its file
            # would wrongly authorize the plugin-qa group.
            self.assertFalse(runner._is_user_authorized(_source()))
            await asyncio.sleep(0.05)

            (named_home / "zalo" / "groups.json").write_text(
                json.dumps(_groups_payload(approved=["group_1"])), encoding="utf-8"
            )
            self.assertTrue(runner._is_user_authorized(_source()))

        self.assertEqual(len(factory.calls), 1)
        self.assertEqual(factory.calls[0]["json"]["agent_id"], "plugin-qa")

    async def test_default_profile_source_reads_the_process_home(self):
        runner_class = self.install_auth()
        runner = runner_class()
        source = _source()
        source.profile = "default"
        self.assertTrue(runner._is_user_authorized(source))
        self.write_groups(_groups_payload(pending=["group_1"]))
        with mock.patch("httpx.AsyncClient", _ClientFactory()):
            self.assertFalse(runner._is_user_authorized(source))
            await asyncio.sleep(0.05)

    async def test_unsafe_profile_name_is_denied(self):
        runner_class = self.install_auth()
        runner = runner_class()
        factory = _ClientFactory()
        with mock.patch("httpx.AsyncClient", factory):
            for name in ("../agent-1", "a/b", "..", "a\\b", ""):
                runner._adapter_profile_for_source = lambda source, name=name: name
                with self.subTest(name=name):
                    self.assertFalse(runner._is_user_authorized(_source()))
            await asyncio.sleep(0.05)
        self.assertEqual(factory.calls, [])
        self.assertEqual(runner_class.original_calls, [])


class GatewayGroupDelegationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.profile = Path(self.temporary.name) / "agent-1"
        (self.profile / "zalo").mkdir(parents=True)
        gateway_cli._GROUP_APPROVED_CACHE.clear()
        gateway_cli._GROUP_SEEN_STATE.clear()
        gateway_cli._GROUP_LIMIT_NOTICE_STATE.clear()

    def write_groups(self, daily_limit=2):
        payload = {
            "schema_version": 1,
            "platforms": {
                "zalo_bot": {
                    "daily_limit": daily_limit,
                    "groups": {"group_1": {"status": "approved", "name": ""}},
                }
            },
        }
        (self.profile / "zalo" / "groups.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def install(self, factory):
        module, runner_class = _gateway_module()
        patches = [
            mock.patch.dict(sys.modules, {"gateway.run": module}),
            mock.patch.dict(
                "os.environ",
                {
                    "RUNTIME_GATEWAY_CHANNEL_DELEGATION": "true",
                    "RUNTIME_INTERNAL_SERVICE_TOKEN": TOKEN,
                    "RUNTIME_INTERNAL_URL": "http://runtime:3000",
                },
            ),
            mock.patch(
                "hermes_constants.get_hermes_home", return_value=self.profile
            ),
            mock.patch(
                "hermes_constants.get_process_hermes_home", return_value=self.profile
            ),
            mock.patch("httpx.AsyncClient", factory),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        gateway_cli.install_runtime_channel_delegation()
        return runner_class

    async def test_named_profile_limit_notice_reads_its_own_groups_file(self):
        root = Path(self.temporary.name) / "hermes-root"
        named_home = root / "profiles" / "plugin-qa"
        (named_home / "zalo").mkdir(parents=True)
        (named_home / "zalo" / "groups.json").write_text(
            json.dumps(_groups_payload(daily_limit=7, approved=["group_1"])),
            encoding="utf-8",
        )
        self.write_groups(daily_limit=2)
        response = _Response(
            429,
            {
                "success": False,
                "error": {"code": "channel_group_limit_reached"},
                "status_code": 429,
            },
        )
        factory = _ClientFactory(response=response)
        runner_class = self.install(factory)
        runner = runner_class()
        runner._adapter_profile_for_source = lambda source: "plugin-qa"

        with mock.patch("hermes_constants.get_default_hermes_root", return_value=root):
            result = await runner._run_agent_inner(
                message="hello",
                context_prompt="",
                history=[],
                source=_source(),
                session_id="session-1",
                event_message_id="message-1",
            )

        self.assertIn("7 lượt", result["final_response"])
        self.assertNotIn("2 lượt", result["final_response"])

    async def test_limit_reply_is_silent_within_the_notice_window(self):
        self.write_groups(daily_limit=2)
        response = _Response(
            429,
            {
                "success": False,
                "error": {"code": "channel_group_limit_reached"},
                "status_code": 429,
            },
        )
        factory = _ClientFactory(response=response)
        runner_class = self.install(factory)
        runner = runner_class()

        first = await runner._run_agent_inner(
            message="hello",
            context_prompt="",
            history=[],
            source=_source(),
            session_id="session-1",
            event_message_id="message-1",
        )
        second = await runner._run_agent_inner(
            message="hello again",
            context_prompt="",
            history=[],
            source=_source(),
            session_id="session-2",
            event_message_id="message-2",
        )

        self.assertIn("2 lượt", first["final_response"])
        self.assertIn("You have used all 2", first["final_response"])
        self.assertEqual(second["final_response"], "NO_REPLY")

        # After the notice window the sender is reminded again.
        later = time.monotonic() + 31
        with mock.patch("xnobrain.integrations.gateway_cli.time.monotonic", return_value=later):
            third = await runner._run_agent_inner(
                message="hello once more",
                context_prompt="",
                history=[],
                source=_source(),
                session_id="session-3",
                event_message_id="message-3",
            )
        self.assertIn("You have used all 2", third["final_response"])
        self.assertEqual(runner_class.native_calls, [])
        self.assertEqual(len(factory.calls), 3)
        self.assertEqual(factory.calls[0]["url"], TURN_ENDPOINT)

    async def test_unapproved_group_refusal_is_silent(self):
        self.write_groups()
        response = _Response(
            403,
            {
                "success": False,
                "error": {"code": "channel_group_not_approved"},
                "status_code": 403,
            },
        )
        factory = _ClientFactory(response=response)
        runner_class = self.install(factory)
        runner = runner_class()

        result = await runner._run_agent_inner(
            message="hello",
            context_prompt="",
            history=[],
            source=_source(),
            session_id="session-1",
            event_message_id="message-1",
        )

        self.assertEqual(result["final_response"], "NO_REPLY")
        self.assertEqual(runner_class.native_calls, [])

    async def test_group_empty_text_never_runs_native(self):
        self.write_groups()
        factory = _ClientFactory()
        runner_class = self.install(factory)
        runner = runner_class()

        result = await runner._run_agent_inner(
            message="",
            context_prompt="",
            history=[],
            source=_source(),
            session_id="session-1",
            event_message_id="message-1",
        )

        self.assertEqual(result["final_response"], "NO_REPLY")
        self.assertEqual(factory.calls, [])
        self.assertEqual(runner_class.native_calls, [])

    async def test_personal_zalo_empty_text_still_uses_native_agent(self):
        self.write_groups()
        factory = _ClientFactory()
        runner_class = self.install(factory)
        runner = runner_class()

        result = await runner._run_agent_inner(
            message="",
            context_prompt="",
            history=[],
            source=_source(platform="zalo"),
            session_id="session-1",
            event_message_id="message-1",
        )

        self.assertEqual(result["final_response"], "native reply")
        self.assertEqual(len(runner_class.native_calls), 1)

    async def test_non_group_runtime_error_keeps_native_failure_contract(self):
        self.write_groups()
        response = _Response(404, {"success": False, "error": {"code": "not_found"}})
        factory = _ClientFactory(response=response)
        runner_class = self.install(factory)
        runner = runner_class()

        result = await runner._run_agent_inner(
            message="hello",
            context_prompt="",
            history=[],
            source=_source(chat_type="dm"),
            session_id="session-1",
            event_message_id="message-1",
        )

        self.assertEqual(result["final_response"], gateway_cli._DELEGATION_FAILURE_TEXT)
        self.assertEqual(runner_class.native_calls, [])


if __name__ == "__main__":
    unittest.main()
