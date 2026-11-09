"""Private channel turns: auth, feature gate, mapping, dedupe, and denials."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml
from fastapi import FastAPI
from pydantic import ValidationError

from xnobrain.handlers.operations.channels import operations
from xnobrain.integrations import AgentAPIError
from xnobrain.models.channels import ChannelTurnRequest
from xnobrain.repositories.files import FileRepository
from xnobrain.routes.definition import API_PREFIX
from xnobrain.routes.setup import setup_routes
from xnobrain.services.base import ServiceError
from xnobrain.services.channels import ChannelsService

TOKEN = "channel-turn-test-token"


class HandlerStub:
    async def dispatch(self, request, body):  # pragma: no cover - disabled feature
        raise AssertionError("disabled feature route was called")


def _body(**overrides):
    value = {
        "platform": "zalo",
        "agent_id": "agent-1",
        "thread_id": "123456",
        "thread_type": "dm",
        "sender_id": "789",
        "sender_name": "Nam",
        "text": "hello there",
        "message_id": "message-1",
        "ts": 1_700_000_000,
    }
    value.update(overrides)
    return value


class _FakeAgents:
    def __init__(self, conversations):
        self._conversations = conversations

    def get_conversation(self, agent_id, conversation_id):
        if conversation_id not in self._conversations:
            raise AgentAPIError(
                "Conversation not found",
                code="conversation_not_found",
                status=404,
            )
        return {"id": conversation_id}


class _FakeConversationRuns:
    def __init__(self):
        self.records: dict[str, dict] = {}
        self.events_by_run: dict[str, list[dict]] = {}

    async def events(self, agent_id, conversation_id, run_id, after=0):
        for event in self.events_by_run.get(run_id, []):
            yield event

    def get_run(self, agent_id, conversation_id, run_id):
        return dict(self.records[run_id])


class _GrantSource:
    def __init__(self, token="channel-grant"):
        self.token = token
        self.calls = []

    async def grant(self, scope, *, agent_id, platform, user_id, tenant_id, now=None):
        self.calls.append((scope, agent_id, platform, user_id, tenant_id))
        return self.token


class _Platform:
    def __init__(self, repository, profile):
        self.repository = repository
        self.profile = profile
        self.conversations: set[str] = set()
        self.agents = _FakeAgents(self.conversations)
        self.conversation_runs = _FakeConversationRuns()
        self.create_calls: list[tuple] = []
        self.run_calls: list[tuple] = []
        self.approvals: list[tuple[str, dict]] = []
        self.resolve_error: Exception | None = None
        self.start_error: Exception | None = None
        self.start_status = "completed"
        self.start_output = "channel reply"
        self.start_usage = {
            "input_tokens": 3,
            "output_tokens": 4,
            "total_tokens": 7,
        }
        self._runs_by_key: dict[str, dict] = {}

    def _conversation_profile(self, agent_id):
        if agent_id != "agent-1":
            raise ServiceError("agent profile not found", status=404, code="not_found")
        return self.profile

    def create_conversation(self, agent_id, body, trusted_context=None, *, host_authority=None):
        conversation_id = f"conv_{len(self.create_calls) + 1}"
        self.create_calls.append((agent_id, body, trusted_context))
        self.conversations.add(conversation_id)
        if host_authority is not None:
            self.repository.create_conversation_context(
                self.profile,
                {
                    "schema_version": 1,
                    "id": "personal",
                    "owner_kind": "personal",
                    "organization_id": None,
                    "payer_kind": "personal",
                    "sponsor_grant_id": None,
                    "state": "active",
                    "conversation_id": conversation_id,
                    "agent_id": agent_id,
                    "actor_user_id": host_authority.subject,
                    "actor_tenant_id": host_authority.tenant_id,
                    "executor_workspace_id": None,
                    "created_at": "2026-01-01T00:00:00Z",
                    "legacy_backfill": False,
                    "channel_platform": host_authority.platform,
                },
            )
        return {"id": conversation_id, "title": body.get("title")}

    async def start_conversation_run(
        self,
        agent_id,
        conversation_id,
        body,
        *,
        mcp_grant=None,
        host_authority=None,
    ):
        self.run_calls.append((agent_id, conversation_id, dict(body)))
        if self.start_error is not None:
            raise self.start_error
        key = str(body.get("idempotency_key") or "")
        existing = self._runs_by_key.get(key)
        if existing is not None:
            return existing
        run_id = "run_" + uuid.uuid4().hex
        record = {
            "id": run_id,
            "status": self.start_status,
            "output": self.start_output,
            "usage": dict(self.start_usage),
        }
        self.conversation_runs.records[run_id] = record
        self._runs_by_key[key] = record
        return record

    def resolve_approval(self, run_id, body):
        if self.resolve_error is not None:
            raise self.resolve_error
        self.approvals.append((run_id, dict(body)))
        return {"run_id": run_id, "resolved": True}


class ChannelTurnModelTests(unittest.TestCase):
    def test_request_accepts_safe_zalo_and_bot_ids(self):
        self.assertEqual(ChannelTurnRequest.model_validate(_body()).platform, "zalo")
        bot = ChannelTurnRequest.model_validate(
            _body(
                platform="zalo_bot",
                thread_id="thread_1",
                sender_id="user_1",
            )
        )
        self.assertEqual(bot.platform, "zalo_bot")

    def test_request_rejects_unsafe_ids_and_unknown_platforms(self):
        for overrides in (
            {"platform": "whatsapp"},
            {"thread_id": "../escape"},
            {"sender_id": "bad/id"},
            {"message_id": ""},
            {"text": ""},
            {"thread_type": "channel"},
            {"unexpected": True},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValidationError):
                    ChannelTurnRequest.model_validate(_body(**overrides))

    def test_platform_specific_id_patterns_are_enforced(self):
        with self.assertRaises(ValidationError):
            ChannelTurnRequest.model_validate(
                _body(platform="zalo", thread_id="123abc", sender_id="456")
            )
        bot = ChannelTurnRequest.model_validate(
            _body(platform="zalo_bot", thread_id="thread_1", sender_id="user_1")
        )
        self.assertEqual(bot.thread_id, "thread_1")


class ChannelTurnRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_turn_route_requires_the_channel_feature_gate(self):
        path = f"{API_PREFIX}/internal/channels/turn"
        with mock.patch.dict(os.environ, {"FT_ENABLE_AGENT_CHANNELS": "true"}, clear=True):
            enabled_app = FastAPI()
            setup_routes(enabled_app, HandlerStub())
        with mock.patch.dict(os.environ, {"FT_ENABLE_AGENT_CHANNELS": "false"}, clear=True):
            disabled_app = FastAPI()
            setup_routes(disabled_app, HandlerStub())
        enabled_paths = {route.path for route in enabled_app.routes}
        disabled_paths = {route.path for route in disabled_app.routes}
        self.assertIn(path, enabled_paths)
        self.assertNotIn(path, disabled_paths)

    async def test_operation_reads_the_internal_token_header(self):
        calls: list[tuple] = []

        async def run_internal_turn(body, token):
            calls.append((body, token))
            return {"conversation_id": "conv", "run_id": "run", "reply": "ok"}

        service = SimpleNamespace(run_internal_turn=run_internal_turn)
        handler = SimpleNamespace(service=SimpleNamespace(channels=service))
        request = SimpleNamespace(
            headers={"x-xnobrain-internal-token": TOKEN},
            path_params={},
        )
        operation = operations(handler, request, _body())["channels_internal_turn"]

        result = await operation[0]()

        self.assertEqual(result["reply"], "ok")
        self.assertEqual(calls[0][1], TOKEN)


class ChannelTurnServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.profiles = root / "profiles"
        self.profile = self.profiles / "agent-1"
        self.profile.mkdir(parents=True)
        self.profile.joinpath("config.yaml").write_text(
            yaml.safe_dump(
                {
                    "platforms": {
                        "zalo": {
                            "enabled": True,
                            "extra": {"dm_policy": "pairing"},
                        },
                        "zalo_bot": {
                            "enabled": True,
                            "extra": {"dm_policy": "allowlist"},
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        self.environment = mock.patch.dict(
            os.environ,
            {
                "FT_ENABLE_AGENT_CHANNELS": "true",
                "RUNTIME_INTERNAL_SERVICE_TOKEN": TOKEN,
                "HERMES_ROOT_PROFILE": str(root / "root"),
                "DATA_DIR": str(root / "data"),
                "RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": "",
                "ZALO_ALLOW_ALL_USERS": "",
                "ZALO_BOT_ALLOW_ALL_USERS": "",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.repository = FileRepository(root / "data", self.profiles)
        self.platform = _Platform(self.repository, self.profile)
        self.service = ChannelsService(self.platform)
        self.service.repository.write_owner(
            self.profile,
            {
                "user_id": "user-1",
                "tenant_id": "personal-user-1",
                "organization_id": "",
                "agent_id": "agent-1",
                "bound_at": 1,
            },
        )
        self.grants = _GrantSource()
        self.service.mcp_grants = self.grants

    async def test_service_requires_the_internal_token(self):
        for token in (None, "", "wrong-token", TOKEN + "x"):
            with self.subTest(token=token):
                with self.assertRaises(ServiceError) as raised:
                    await self.service.run_internal_turn(_body(), token)
                self.assertEqual(raised.exception.status, 401)
                self.assertEqual(raised.exception.code, "channel_unauthorized")
        self.assertEqual(self.platform.run_calls, [])

    async def test_service_fails_closed_when_the_feature_is_disabled(self):
        with mock.patch.dict(os.environ, {"FT_ENABLE_AGENT_CHANNELS": "false"}):
            with self.assertRaises(ServiceError) as raised:
                await self.service.run_internal_turn(_body(), TOKEN)
        self.assertEqual(raised.exception.status, 404)
        self.assertEqual(raised.exception.code, "feature_disabled")

    async def test_unknown_platform_is_rejected_before_creation(self):
        with self.assertRaises(ServiceError) as raised:
            await self.service.run_internal_turn(_body(platform="whatsapp"), TOKEN)
        self.assertEqual(raised.exception.status, 422)
        self.assertEqual(raised.exception.code, "invalid_platform")
        self.assertEqual(self.platform.create_calls, [])

    async def test_allow_all_and_unsafe_dm_policies_are_refused(self):
        self.profile.joinpath(".env").write_text("ZALO_ALLOW_ALL_USERS=true\n", encoding="utf-8")
        with self.assertRaises(ServiceError) as raised:
            await self.service.run_internal_turn(_body(), TOKEN)
        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(raised.exception.code, "channel_dm_policy_unsafe")
        self.assertEqual(self.platform.create_calls, [])

        self.profile.joinpath(".env").unlink()
        self.profile.joinpath("config.yaml").write_text(
            yaml.safe_dump(
                {"platforms": {"zalo": {"enabled": True, "extra": {"dm_policy": "disabled"}}}}
            ),
            encoding="utf-8",
        )
        with self.assertRaises(ServiceError) as raised:
            await self.service.run_internal_turn(_body(), TOKEN)
        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(raised.exception.code, "channel_dm_policy_unsafe")

    async def test_turn_creates_a_private_thread_mapping_and_returns_usage(self):
        result = await self.service.run_internal_turn(_body(), TOKEN)

        self.assertEqual(result["conversation_id"], "conv_1")
        self.assertTrue(result["run_id"].startswith("run_"))
        self.assertEqual(result["reply"], "channel reply")
        self.assertEqual(
            result["usage"],
            {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7},
        )
        self.assertEqual(len(self.platform.run_calls), 1)
        agent_id, conversation_id, body = self.platform.run_calls[0]
        self.assertEqual((agent_id, conversation_id), ("agent-1", "conv_1"))
        self.assertEqual(body["run_mode"], "background")
        self.assertEqual(body["idempotency_key"], "zalo:message-1")
        self.assertEqual(body["input"], "hello there")
        thread_path = self.profile / "zalo" / "threads.json"
        self.assertEqual(thread_path.stat().st_mode & 0o777, 0o600)
        mapping = json.loads(thread_path.read_text(encoding="utf-8"))
        record = mapping["platforms"]["zalo"]["123456"]
        self.assertEqual(record["agent_id"], "agent-1")
        self.assertEqual(record["conversation_id"], "conv_1")
        self.assertEqual(record["sender_id"], "789")

    async def test_thread_reuses_one_conversation_across_messages(self):
        first = await self.service.run_internal_turn(_body(), TOKEN)
        second = await self.service.run_internal_turn(
            _body(message_id="message-2", text="second"), TOKEN
        )

        self.assertEqual(first["conversation_id"], second["conversation_id"])
        self.assertEqual(len(self.platform.create_calls), 1)
        self.assertEqual(len(self.platform.run_calls), 2)

    async def test_idempotent_retry_reuses_the_same_run(self):
        first = await self.service.run_internal_turn(_body(), TOKEN)
        second = await self.service.run_internal_turn(_body(), TOKEN)

        self.assertEqual(first["run_id"], second["run_id"])
        self.assertEqual(first["reply"], second["reply"])
        self.assertEqual(len(self.platform.create_calls), 1)
        self.assertEqual(len(self.platform.run_calls), 2)

    async def test_active_turn_returns_conflict(self):
        self.platform.start_error = ServiceError(
            "a response is already running for this conversation",
            status=409,
            code="conversation_running",
        )
        with self.assertRaises(ServiceError) as raised:
            await self.service.run_internal_turn(_body(), TOKEN)
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(raised.exception.code, "channel_turn_running")

    async def test_failed_run_returns_a_public_error(self):
        self.platform.start_status = "failed"
        self.platform.start_output = "secret provider body"
        with self.assertRaises(ServiceError) as raised:
            await self.service.run_internal_turn(_body(), TOKEN)
        self.assertEqual(raised.exception.status, 502)
        self.assertEqual(raised.exception.code, "channel_turn_failed")
        self.assertNotIn("secret", str(raised.exception))

    async def test_channel_approval_requests_are_denied(self):
        platform_records = self.platform.conversation_runs.records

        async def run_with_approval(agent_id, conversation_id, body, **kwargs):
            self.platform.run_calls.append((agent_id, conversation_id, dict(body)))
            run_id = "run_" + uuid.uuid4().hex
            platform_records[run_id] = {
                "id": run_id,
                "status": "completed",
                "output": "denied tool handled",
                "usage": {},
            }
            self.platform.conversation_runs.events_by_run[run_id] = [
                {"event": "approval.request", "data": {"command": "secret command"}}
            ]
            return platform_records[run_id]

        self.platform.start_conversation_run = run_with_approval
        result = await self.service.run_internal_turn(_body(), TOKEN)

        self.assertEqual(result["reply"], "denied tool handled")
        self.assertEqual(len(self.platform.approvals), 1)
        self.assertEqual(self.platform.approvals[0][1], {"choice": "deny"})

    async def test_connect_request_returns_web_only_instruction(self):
        record = {
            "id": "run_" + uuid.uuid4().hex,
            "status": "completed",
            "output": "A connect card is shown in the chat.",
            "usage": {},
        }

        async def run_with_connect(agent_id, conversation_id, body, **kwargs):
            self.platform.run_calls.append((agent_id, conversation_id, dict(body)))
            self.platform.conversation_runs.records[record["id"]] = record
            self.platform.conversation_runs.events_by_run[record["id"]] = [
                {
                    "event": "integration.connect_requested",
                    "request": {"kind": "channel", "platform": "zalo"},
                }
            ]
            return record

        self.platform.start_conversation_run = run_with_connect
        with mock.patch.dict(
            os.environ,
            {"RUNTIME_CONTROL_URL": "https://control.internal.example"},
        ):
            result = await self.service.run_internal_turn(_body(), TOKEN)

        self.assertIn("web app", result["reply"])
        self.assertIn("Do not send", result["reply"])
        self.assertNotIn("connect card", result["reply"].lower())
        self.assertNotIn("control.internal", result["reply"])

    async def test_approval_denial_failure_log_is_redacted(self):
        self.platform.resolve_error = RuntimeError("secret command and message text")
        record = {
            "id": "run_" + uuid.uuid4().hex,
            "status": "completed",
            "output": "reply",
            "usage": {},
        }

        async def run_with_approval(agent_id, conversation_id, body, **kwargs):
            self.platform.run_calls.append((agent_id, conversation_id, dict(body)))
            self.platform.conversation_runs.records[record["id"]] = record
            self.platform.conversation_runs.events_by_run[record["id"]] = [
                {"event": "approval.request", "data": {"command": "secret command"}}
            ]
            return record

        self.platform.start_conversation_run = run_with_approval
        records: list[str] = []

        class Capture(logging.Handler):
            def emit(self, log_record):
                records.append(log_record.getMessage())

        logger = logging.getLogger("xnobrain.services.channels")
        handler = Capture()
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)

        result = await self.service.run_internal_turn(_body(), TOKEN)

        self.assertEqual(result["reply"], "reply")
        self.assertTrue(any("RuntimeError" in message for message in records))
        self.assertTrue(all("secret" not in message for message in records))


if __name__ == "__main__":
    unittest.main()
