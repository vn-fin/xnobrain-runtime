"""Zalo Bot group approval, pending cap, and per-member daily limits."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml
from fastapi import FastAPI

from xnobrain.handlers.api import APIHandlers
from xnobrain.integrations import AgentAPIError
from xnobrain.repositories.channels import (
    DEFAULT_GROUP_DAILY_LIMIT,
    MAX_PENDING_GROUP_REQUESTS,
)
from xnobrain.repositories.files import FileRepository
from xnobrain.routes.definition import API_PREFIX
from xnobrain.routes.setup import setup_routes
from xnobrain.services.base import ServiceError
from xnobrain.services.channels import ChannelsService
from xnobrain.trusted_context import TrustedRequestContext

TOKEN = "channel-group-test-token"


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

    async def events(self, agent_id, conversation_id, run_id, after=0):
        if False:  # pragma: no cover - keep this an async generator
            yield {}
        return

    def get_run(self, agent_id, conversation_id, run_id):
        return dict(self.records[run_id])


class _GrantSource:
    async def grant(self, scope, *, agent_id, platform, user_id, tenant_id, now=None):
        return "channel-grant"


class _Platform:
    def __init__(self, repository, profile, root_profile):
        self.repository = repository
        self.profile = profile
        self.config = SimpleNamespace(root_profile=root_profile)
        self.conversations: set[str] = set()
        self.agents = _FakeAgents(self.conversations)
        self.conversation_runs = _FakeConversationRuns()
        self.create_calls: list[tuple] = []
        self.run_calls: list[tuple] = []
        self._runs_by_key: dict[str, dict] = {}

    def _conversation_profile(self, agent_id):
        if agent_id != "agent-1":
            raise ServiceError("agent profile not found", status=404, code="not_found")
        return self.profile

    def _snapshot_agent(self, agent_id, kind, target, content):
        return self.repository.snapshot(agent_id, kind, target, content)

    def create_conversation(self, agent_id, body, trusted_context=None, *, host_authority=None):
        self.create_calls.append((agent_id, body, trusted_context))
        conversation_id = f"conv_{len(self.create_calls)}"
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
        self, agent_id, conversation_id, body, *, mcp_grant=None, host_authority=None
    ):
        self.run_calls.append((agent_id, conversation_id, dict(body)))
        key = str(body.get("idempotency_key") or "")
        existing = self._runs_by_key.get(key)
        if existing is not None:
            return existing
        run_id = "run_" + uuid.uuid4().hex
        record = {
            "id": run_id,
            "status": "completed",
            "output": "group reply",
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }
        self.conversation_runs.records[run_id] = record
        self._runs_by_key[key] = record
        return record

    def resolve_approval(self, run_id, body):  # pragma: no cover - no approvals
        return {"run_id": run_id, "resolved": True}


def _turn_body(**overrides):
    value = {
        "platform": "zalo_bot",
        "agent_id": "agent-1",
        "thread_id": "group_1",
        "thread_type": "group",
        "sender_id": "sender_1",
        "sender_name": "Nam",
        "text": "hello group",
        "message_id": "message-1",
        "ts": 1_700_000_000,
    }
    value.update(overrides)
    return value


class ChannelGroupFixture(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.profiles = root / "profiles"
        self.root_profile = self.profiles / "big-brother"
        self.profile = self.profiles / "agent-1"
        self.root_profile.mkdir(parents=True)
        self.profile.mkdir(parents=True)
        self.profile.joinpath("config.yaml").write_text(
            yaml.safe_dump(
                {
                    "platforms": {
                        "zalo": {"enabled": False, "extra": {"dm_policy": "disabled"}},
                        "zalo_bot": {
                            "enabled": True,
                            "extra": {"dm_policy": "pairing"},
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
                "HERMES_HOME": str(self.root_profile),
                "HERMES_ROOT_PROFILE": str(self.root_profile),
                "HERMES_PROFILES_ROOT": str(self.profiles),
                "RUNTIME_INTERNAL_SERVICE_TOKEN": TOKEN,
                "DATA_DIR": str(root / "data"),
                "RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": "",
                "ZALO_BOT_ALLOW_ALL_USERS": "",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.repository = FileRepository(
            root / "data", self.profiles, root_profile=self.root_profile
        )
        self.platform = _Platform(self.repository, self.profile, self.root_profile)
        self.service = ChannelsService(self.platform)
        self.service.mcp_grants = _GrantSource()
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

    def _trusted(self, subject="user-1", tenant="personal-user-1", organization=""):
        return TrustedRequestContext(
            subject=subject, tenant_id=tenant, organization_id=organization
        )

    def _groups_file(self) -> dict:
        return json.loads((self.profile / "zalo" / "groups.json").read_text(encoding="utf-8"))

    def _usage_file(self) -> dict:
        return json.loads(
            (self.profile / "zalo" / "group_usage.json").read_text(encoding="utf-8")
        )

    async def _approve(self, group_id="group_1", name="Group One"):
        self.service.record_group_seen(
            {
                "platform": "zalo_bot",
                "agent_id": "agent-1",
                "group_id": group_id,
                "group_name": name,
                "sender_name": "Nam",
            },
            TOKEN,
        )
        return await self.service.approve_group(
            "agent-1", "zalo_bot", group_id, self._trusted()
        )


class ChannelGroupServiceTests(ChannelGroupFixture):
    async def test_pending_record_and_cap(self):
        for index in range(MAX_PENDING_GROUP_REQUESTS + 2):
            status = self.service.record_group_seen(
                {
                    "platform": "zalo_bot",
                    "agent_id": "agent-1",
                    "group_id": f"group_{index}",
                    "group_name": f"Group {index}",
                    "sender_name": "Nam",
                },
                TOKEN,
            )["status"]
            self.assertEqual(status, "pending" if index < MAX_PENDING_GROUP_REQUESTS else "ignored")

        listed = await self.service.list_groups(
            "agent-1", "zalo_bot", self._trusted()
        )
        self.assertEqual(len(listed["pending"]), MAX_PENDING_GROUP_REQUESTS)
        self.assertEqual(listed["approved"], [])
        self.assertEqual(listed["daily_limit"], DEFAULT_GROUP_DAILY_LIMIT)
        record = self._groups_file()["platforms"]["zalo_bot"]["groups"]["group_0"]
        self.assertEqual(record["status"], "pending")
        self.assertEqual(record["name"], "Group 0")
        self.assertEqual(record["last_sender_name"], "Nam")
        self.assertEqual((self.profile / "zalo" / "groups.json").stat().st_mode & 0o777, 0o600)

    async def test_approve_deny_and_revoke(self):
        await self._approve()
        listed = await self.service.list_groups("agent-1", "zalo_bot", self._trusted())
        self.assertEqual([item["group_id"] for item in listed["approved"]], ["group_1"])
        self.assertGreater(listed["approved"][0]["approved_at"], 0)

        revoked = await self.service.revoke_group(
            "agent-1", "zalo_bot", "group_1", self._trusted()
        )
        self.assertEqual(revoked["status"], "revoked")
        again = await self.service.revoke_group(
            "agent-1", "zalo_bot", "group_1", self._trusted()
        )
        self.assertEqual(again["status"], "not_approved")

        self.service.record_group_seen(
            {
                "platform": "zalo_bot",
                "agent_id": "agent-1",
                "group_id": "group_2",
                "group_name": "Two",
            },
            TOKEN,
        )
        denied = await self.service.deny_group(
            "agent-1", "zalo_bot", "group_2", self._trusted()
        )
        self.assertEqual(denied["status"], "denied")
        with self.assertRaises(ServiceError) as raised:
            await self.service.deny_group(
                "agent-1", "zalo_bot", "group_2", self._trusted()
            )
        self.assertEqual(raised.exception.status, 404)
        self.assertEqual(raised.exception.code, "channel_group_not_found")
        with self.assertRaises(ServiceError) as raised:
            await self.service.approve_group(
                "agent-1", "zalo_bot", "missing", self._trusted()
            )
        self.assertEqual(raised.exception.code, "channel_group_not_found")

    async def test_settings_validation_and_persistence(self):
        result = await self.service.update_group_settings(
            "agent-1", "zalo_bot", 0, self._trusted()
        )
        self.assertEqual(result, {"daily_limit": 0})
        listed = await self.service.list_groups("agent-1", "zalo_bot", self._trusted())
        self.assertEqual(listed["daily_limit"], 0)

        for value in (-1, 1001, True, "20"):
            with self.subTest(value=value):
                with self.assertRaises(ServiceError) as raised:
                    await self.service.update_group_settings(
                        "agent-1", "zalo_bot", value, self._trusted()
                    )
                self.assertEqual(raised.exception.status, 422)
                self.assertEqual(raised.exception.code, "invalid_daily_limit")

        updated = await self.service.update_group_settings(
            "agent-1", "zalo_bot", 7, self._trusted()
        )
        self.assertEqual(updated["daily_limit"], 7)

    async def test_owner_authorization_failures_mirror_pairings(self):
        for trusted in (None, TrustedRequestContext()):
            with self.subTest(trusted=trusted):
                with self.assertRaises(ServiceError) as raised:
                    await self.service.list_groups("agent-1", "zalo_bot", trusted)
                self.assertEqual(raised.exception.status, 401)
                self.assertEqual(raised.exception.code, "trusted_context_required")

        with self.assertRaises(ServiceError) as raised:
            await self.service.list_groups(
                "agent-1", "zalo_bot", self._trusted(subject="someone-else")
            )
        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(raised.exception.code, "channel_owner_mismatch")

        with self.assertRaises(ServiceError) as raised:
            await self.service.list_groups(
                "agent-1", "zalo_bot", self._trusted(organization="org-1")
            )
        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(raised.exception.code, "channel_personal_required")

    async def test_unknown_platform_is_rejected(self):
        with self.assertRaises(ServiceError) as raised:
            await self.service.list_groups("agent-1", "whatsapp", self._trusted())
        self.assertEqual(raised.exception.status, 422)
        self.assertEqual(raised.exception.code, "invalid_platform")

        with self.assertRaises(ServiceError) as raised:
            self.service.record_group_seen(
                {
                    "platform": "whatsapp",
                    "agent_id": "agent-1",
                    "group_id": "123456",
                },
                TOKEN,
            )
        self.assertEqual(raised.exception.code, "invalid_platform")

    async def test_personal_zalo_groups_are_accepted(self):
        config = yaml.safe_load(self.profile.joinpath("config.yaml").read_text())
        config["platforms"]["zalo"]["enabled"] = True
        config["platforms"]["zalo"]["extra"]["dm_policy"] = "pairing"
        self.profile.joinpath("config.yaml").write_text(
            yaml.safe_dump(config), encoding="utf-8"
        )
        self.service.record_group_seen(
            {
                "platform": "zalo",
                "agent_id": "agent-1",
                "group_id": "123456",
                "group_name": "Personal Group",
                "sender_name": "Nam",
            },
            TOKEN,
        )

        listed = await self.service.list_groups("agent-1", "zalo", self._trusted())

        self.assertEqual(listed["platform"], "zalo")
        self.assertEqual([item["group_id"] for item in listed["pending"]], ["123456"])
        approved = await self.service.approve_group(
            "agent-1", "zalo", "123456", self._trusted()
        )
        self.assertEqual(approved["status"], "approved")
        listed = await self.service.list_groups("agent-1", "zalo", self._trusted())
        self.assertEqual([item["group_id"] for item in listed["approved"]], ["123456"])

    async def test_internal_seen_requires_token_and_sanitizes(self):
        for token in (None, "", "wrong"):
            with self.subTest(token=token):
                with self.assertRaises(ServiceError) as raised:
                    self.service.record_group_seen(
                        {
                            "platform": "zalo_bot",
                            "agent_id": "agent-1",
                            "group_id": "group_1",
                        },
                        token,
                    )
                self.assertEqual(raised.exception.status, 401)
                self.assertEqual(raised.exception.code, "channel_unauthorized")

        result = self.service.record_group_seen(
            {
                "platform": "ZALO_BOT",
                "agent_id": "agent-1",
                "group_id": "group_1",
                "group_name": "Line one\nline two  " + "x" * 200,
                "sender_name": "  Nam\n  ",
            },
            TOKEN,
        )
        self.assertEqual(result["status"], "pending")
        entry = self._groups_file()["platforms"]["zalo_bot"]["groups"]["group_1"]
        self.assertLessEqual(len(entry["name"]), 100)
        self.assertNotIn("\n", entry["name"])
        self.assertEqual(entry["last_sender_name"], "Nam")

        with self.assertRaises(ServiceError) as raised:
            self.service.record_group_seen(
                {
                    "platform": "zalo_bot",
                    "agent_id": "agent-1",
                    "group_id": "../escape",
                },
                TOKEN,
            )
        self.assertEqual(raised.exception.code, "invalid_group_id")

    async def test_routes_are_registered_and_validate_bodies(self):
        app = FastAPI()
        with mock.patch.dict(
            os.environ, {"FT_ENABLE_AGENT_CHANNELS": "true", "RUNTIME_INTERNAL_SERVICE_TOKEN": TOKEN}
        ):
            setup_routes(app, APIHandlers(mock.Mock(channels=self.service)))
        paths = {route.path for route in app.routes}
        for suffix in (
            "/groups",
            "/groups/approve",
            "/groups/deny",
            "/groups/revoke",
            "/groups/settings",
        ):
            self.assertIn(
                f"{API_PREFIX}/agents/{{agent_id}}/channels/{{platform}}{suffix}",
                paths,
            )
        self.assertIn(f"{API_PREFIX}/internal/channels/groups/seen", paths)


class ChannelGroupTurnTests(ChannelGroupFixture):
    async def test_unapproved_group_is_refused_and_recorded(self):
        with self.assertRaises(ServiceError) as raised:
            await self.service.run_internal_turn(_turn_body(), TOKEN)
        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(raised.exception.code, "channel_group_not_approved")
        self.assertEqual(self.platform.run_calls, [])
        listed = await self.service.list_groups("agent-1", "zalo_bot", self._trusted())
        self.assertEqual([item["group_id"] for item in listed["pending"]], ["group_1"])
        self.assertEqual(listed["pending"][0]["last_sender_name"], "Nam")

    async def test_approved_group_runs_for_an_unpaired_member(self):
        await self._approve()
        result = await self.service.run_internal_turn(_turn_body(), TOKEN)
        self.assertEqual(result["reply"], "group reply")
        self.assertEqual(len(self.platform.run_calls), 1)
        self.assertEqual(self.platform.run_calls[0][2]["input"], "hello group")
        usage = self._usage_file()["platforms"]["zalo_bot"]
        self.assertEqual(usage["counts"]["group_1"]["sender_1"], 1)
        self.assertEqual((self.profile / "zalo" / "group_usage.json").stat().st_mode & 0o777, 0o600)

    async def test_limit_reached_returns_429_without_starting_a_run(self):
        await self._approve()
        await self.service.update_group_settings("agent-1", "zalo_bot", 1, self._trusted())
        await self.service.run_internal_turn(_turn_body(), TOKEN)

        with self.assertRaises(ServiceError) as raised:
            await self.service.run_internal_turn(
                _turn_body(message_id="message-2"), TOKEN
            )
        self.assertEqual(raised.exception.status, 429)
        self.assertEqual(raised.exception.code, "channel_group_limit_reached")
        self.assertEqual(len(self.platform.run_calls), 1)

    async def test_same_message_id_counts_once_and_retries_allowed(self):
        await self._approve()
        await self.service.update_group_settings("agent-1", "zalo_bot", 1, self._trusted())
        first = await self.service.run_internal_turn(_turn_body(), TOKEN)
        second = await self.service.run_internal_turn(_turn_body(), TOKEN)

        self.assertEqual(first["run_id"], second["run_id"])
        usage = self._usage_file()["platforms"]["zalo_bot"]
        self.assertEqual(usage["counts"]["group_1"]["sender_1"], 1)
        self.assertEqual(usage["message_ids"]["group_1:sender_1"], ["message-1"])

    async def test_dm_paired_sender_still_counts(self):
        await self._approve()
        await self.service.update_group_settings("agent-1", "zalo_bot", 1, self._trusted())
        from gateway.pairing import PairingStore

        store = PairingStore(profile="agent-1")
        code = store.generate_code("zalo_bot", "sender_1", "Nam")
        self.assertIsNotNone(store.approve_code("zalo_bot", code))

        await self.service.run_internal_turn(_turn_body(), TOKEN)
        with self.assertRaises(ServiceError):
            await self.service.run_internal_turn(_turn_body(message_id="message-2"), TOKEN)

        self.assertEqual(len(self.platform.run_calls), 1)
        usage = self._usage_file()["platforms"]["zalo_bot"]
        self.assertEqual(usage["counts"]["group_1"]["sender_1"], 1)

    async def test_day_rollover_resets_counts(self):
        await self._approve()
        await self.service.update_group_settings("agent-1", "zalo_bot", 1, self._trusted())
        with mock.patch("xnobrain.services.channels.time.time", return_value=1_700_000_000):
            await self.service.run_internal_turn(_turn_body(), TOKEN)
            with self.assertRaises(ServiceError):
                await self.service.run_internal_turn(
                    _turn_body(message_id="message-2"), TOKEN
                )
        with mock.patch("xnobrain.services.channels.time.time", return_value=1_700_090_000):
            await self.service.run_internal_turn(
                _turn_body(message_id="message-3"), TOKEN
            )
        usage = self._usage_file()["platforms"]["zalo_bot"]
        self.assertEqual(usage["counts"]["group_1"]["sender_1"], 1)

    async def test_limit_zero_is_unlimited(self):
        await self._approve()
        await self.service.update_group_settings("agent-1", "zalo_bot", 0, self._trusted())
        for index in range(30):
            await self.service.run_internal_turn(
                _turn_body(message_id=f"message-{index}"), TOKEN
            )
        self.assertEqual(len(self.platform.run_calls), 30)

    async def test_approved_group_runs_even_when_dms_are_disabled(self):
        await self._approve()
        config = yaml.safe_load(self.profile.joinpath("config.yaml").read_text())
        config["platforms"]["zalo_bot"]["extra"]["dm_policy"] = "disabled"
        self.profile.joinpath("config.yaml").write_text(
            yaml.safe_dump(config), encoding="utf-8"
        )
        result = await self.service.run_internal_turn(_turn_body(), TOKEN)
        self.assertEqual(result["reply"], "group reply")

    async def test_personal_zalo_group_requires_owner_approval(self):
        config = yaml.safe_load(self.profile.joinpath("config.yaml").read_text())
        config["platforms"]["zalo"]["enabled"] = True
        config["platforms"]["zalo"]["extra"]["dm_policy"] = "pairing"
        self.profile.joinpath("config.yaml").write_text(
            yaml.safe_dump(config), encoding="utf-8"
        )
        body = _turn_body(platform="zalo", thread_id="123456", sender_id="789")

        with self.assertRaises(ServiceError) as raised:
            await self.service.run_internal_turn(body, TOKEN)
        self.assertEqual(raised.exception.code, "channel_group_not_approved")

        await self.service.approve_group("agent-1", "zalo", "123456", self._trusted())
        result = await self.service.run_internal_turn(
            _turn_body(
                platform="zalo",
                thread_id="123456",
                sender_id="789",
                message_id="message-2",
            ),
            TOKEN,
        )
        self.assertEqual(result["reply"], "group reply")


if __name__ == "__main__":
    unittest.main()
