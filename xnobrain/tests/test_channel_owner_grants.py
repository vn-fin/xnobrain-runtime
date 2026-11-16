"""Owner-bound channel MCP grants: bindings, caching, and degradation."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import httpx
import yaml

from xnobrain.integrations import AgentAPIError, control_mcp_grants, workload_identity
from xnobrain.integrations.control_mcp_grants import WorkspaceMcpGrantClient
from xnobrain.repositories.base import StoreError
from xnobrain.repositories.files import FileRepository
from xnobrain.services.base import ServiceError
from xnobrain.services.channels import ChannelsService
from xnobrain.services.conversation_authority import (
    HostAuthority,
    host_authority_from_data,
)
from xnobrain.services.conversation_runs import ConversationRunService
from xnobrain.trusted_context import TrustedRequestContext

TOKEN = "channel-grant-test-token"
GRANT_PATH = "/xnobrain/api/control/internal/v1/workspace-mcp-grants"


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
    def __init__(self):
        self.conversations: set[str] = set()

    def get_conversation(self, agent_id, conversation_id):
        if conversation_id not in self.conversations:
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
        for event in ():
            yield event

    def get_run(self, agent_id, conversation_id, run_id):
        return dict(self.records[run_id])


class _Platform:
    """Host surface with explicit mcp_grant capture and no network."""

    def __init__(self, repository, profile, root):
        self.repository = repository
        self.profile = profile
        self.config = SimpleNamespace(root_profile=root)
        self.agents = _FakeAgents()
        self.conversation_runs = _FakeConversationRuns()
        self.run_calls: list[dict] = []
        self.mcp_grants: list[str | None] = []

    def _conversation_profile(self, agent_id):
        if agent_id != "agent-1":
            raise ServiceError("agent profile not found", status=404, code="not_found")
        return self.profile

    def _snapshot_agent(self, agent_id, kind, target, content):
        return self.repository.snapshot(agent_id, kind, target, content)

    def create_conversation(self, agent_id, body, trusted_context=None, *, host_authority=None):
        conversation_id = "conv_" + uuid.uuid4().hex
        self.agents.conversations.add(conversation_id)
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
        self.run_calls.append(dict(body))
        self.mcp_grants.append(mcp_grant)
        run_id = "run_" + uuid.uuid4().hex
        record = {
            "id": run_id,
            "status": "completed",
            "output": "channel reply",
            "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
        }
        self.conversation_runs.records[run_id] = record
        return record

    def resolve_approval(self, run_id, body):
        return {"run_id": run_id, "resolved": True}


class _GrantSource:
    def __init__(self, token="", error=None):
        self.token = token
        self.error = error
        self.calls: list[tuple] = []

    async def grant(self, scope, *, agent_id, platform, user_id, tenant_id, now=None):
        self.calls.append((scope, agent_id, platform, user_id, tenant_id))
        if self.error is not None:
            raise self.error
        return self.token


class _ChannelCase(unittest.IsolatedAsyncioTestCase):
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
                        "zalo": {"enabled": True, "extra": {"dm_policy": "pairing"}},
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
                "HERMES_ROOT_PROFILE": str(self.root_profile),
                "DATA_DIR": str(root / "data"),
                "RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": "",
                "ZALO_ALLOW_ALL_USERS": "",
                "ZALO_BOT_ALLOW_ALL_USERS": "",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        control_mcp_grants._CACHE.clear()
        self.repository = FileRepository(
            root / "data", self.profiles, root_profile=self.root_profile
        )
        self.platform = _Platform(self.repository, self.profile, self.root_profile)
        self.service = ChannelsService(self.platform)

    @property
    def owner_path(self) -> Path:
        return self.profile / ".xnobrain" / "channels" / "owner.json"


class ChannelOwnerBindingTests(_ChannelCase):
    def _trusted(self, subject="user-1", tenant="personal-user-1", organization=""):
        return TrustedRequestContext(
            subject=subject, tenant_id=tenant, organization_id=organization
        )

    def test_only_a_verified_subject_writes_the_binding(self):
        self.service.set_channel_enabled("agent-1", True)
        self.assertFalse(self.owner_path.exists())

        self.service.set_channel_enabled("agent-1", True, trusted=self._trusted())
        self.assertTrue(self.owner_path.is_file())
        self.assertEqual(self.owner_path.stat().st_mode & 0o777, 0o600)
        record = json.loads(self.owner_path.read_text(encoding="utf-8"))
        self.assertEqual(
            sorted(record),
            ["agent_id", "bound_at", "organization_id", "tenant_id", "user_id"],
        )
        self.assertEqual(record["user_id"], "user-1")
        self.assertEqual(record["tenant_id"], "personal-user-1")
        self.assertEqual(record["agent_id"], "agent-1")
        self.assertIsInstance(record["bound_at"], float)
        self.assertNotIn(TOKEN, self.owner_path.read_text(encoding="utf-8"))

    def test_verified_patch_ignores_owner_fields_in_the_body(self):
        self.service.patch_channel(
            "agent-1",
            {
                "dm_policy": "allowlist",
                "user_id": "attacker",
                "tenant_id": "attacker-tenant",
                "owner": "attacker",
            },
            trusted=self._trusted(subject="owner", tenant="personal-owner"),
        )
        record = json.loads(self.owner_path.read_text(encoding="utf-8"))
        self.assertEqual(record["user_id"], "owner")
        self.assertEqual(record["tenant_id"], "personal-owner")
        self.assertEqual(record["agent_id"], "agent-1")

    def test_disable_and_logout_remove_the_binding(self):
        trusted = self._trusted()
        self.service.set_channel_enabled("agent-1", True, trusted=trusted)
        self.assertTrue(self.owner_path.exists())
        self.service.set_zalo_bot_enabled("agent-1", False)
        self.assertTrue(self.owner_path.exists())
        self.service.set_channel_enabled("agent-1", False)
        self.assertFalse(self.owner_path.exists())

        self.service.set_zalo_bot_enabled("agent-1", True, trusted=trusted)
        self.assertTrue(self.owner_path.exists())
        self.service.logout_zalo_bot("agent-1")
        self.assertFalse(self.owner_path.exists())

        self.service.set_channel_enabled("agent-1", True, trusted=trusted)
        self.assertTrue(self.owner_path.exists())
        self.service.logout_channel("agent-1")
        self.assertTrue(self.owner_path.exists())
        self.service.set_channel_enabled("agent-1", False)
        self.assertFalse(self.owner_path.exists())

    def test_existing_binding_for_another_user_fails_closed(self):
        self.service.repository.write_owner(
            self.profile,
            {
                "user_id": "first",
                "tenant_id": "personal-first",
                "organization_id": "",
                "agent_id": "agent-1",
                "bound_at": 1,
            },
        )
        with self.assertRaises(ServiceError) as raised:
            self.service.patch_channel(
                "agent-1",
                {"dm_policy": "allowlist"},
                trusted=self._trusted(subject="second", tenant="personal-second"),
            )
        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(raised.exception.code, "channel_owner_mismatch")
        record = json.loads(self.owner_path.read_text(encoding="utf-8"))
        self.assertEqual(record["user_id"], "first")

    def test_profile_owner_marker_mismatch_fails_before_any_binding(self):
        self.profile.joinpath(".community-profile-owner.json").write_text(
            json.dumps({"subject": "other", "tenant_id": "personal-other", "organization_id": ""}),
            encoding="utf-8",
        )
        with self.assertRaises(ServiceError) as raised:
            self.service.set_channel_enabled("agent-1", True, trusted=self._trusted())
        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(raised.exception.code, "permission_denied")
        self.assertFalse(self.owner_path.exists())

        self.profile.joinpath(".community-profile-owner.json").write_text(
            json.dumps(
                {
                    "subject": "user-1",
                    "tenant_id": "personal-user-1",
                    "organization_id": "",
                }
            ),
            encoding="utf-8",
        )
        self.service.set_channel_enabled("agent-1", True, trusted=self._trusted())
        self.assertTrue(self.owner_path.is_file())

    def test_owner_is_preserved_while_either_channel_remains_enabled(self):
        trusted = self._trusted()
        self.service.set_channel_enabled("agent-1", True, trusted=trusted)
        self.service.set_zalo_bot_enabled("agent-1", True, trusted=trusted)
        self.assertTrue(self.owner_path.is_file())

        self.service.set_channel_enabled("agent-1", False)
        self.assertTrue(self.owner_path.is_file())

        self.service.set_zalo_bot_enabled("agent-1", False)
        self.assertFalse(self.owner_path.exists())

    def test_logout_preserves_owner_for_the_other_enabled_channel(self):
        trusted = self._trusted()
        self.service.set_channel_enabled("agent-1", True, trusted=trusted)
        self.service.set_zalo_bot_enabled("agent-1", True, trusted=trusted)

        self.service.logout_zalo_bot("agent-1")
        self.assertTrue(self.owner_path.is_file())

        self.service.set_channel_enabled("agent-1", False)
        self.assertFalse(self.owner_path.exists())

    def test_connect_target_is_read_only_and_owner_bound(self):
        trusted = self._trusted()
        self.service.set_channel_enabled("agent-1", True, trusted=trusted)
        config_before = self.profile.joinpath("config.yaml").read_bytes()
        owner_before = self.owner_path.read_bytes()

        self.assertEqual(
            self.service.connect_target("agent-1", trusted=trusted),
            self.profile,
        )
        with self.assertRaises(ServiceError) as raised:
            self.service.connect_target("agent-1", trusted=None)
        self.assertEqual(raised.exception.status, 401)
        with self.assertRaises(ServiceError) as raised:
            self.service.connect_target("agent-1", trusted=self._trusted(subject="someone-else"))
        self.assertEqual(raised.exception.code, "channel_owner_mismatch")
        with self.assertRaises(ServiceError) as raised:
            self.service.connect_target("agent-1", trusted=self._trusted(organization="org-1"))
        self.assertEqual(raised.exception.code, "channel_personal_required")

        authority = HostAuthority(
            subject="user-1",
            tenant_id="personal-user-1",
            organization_id="",
            agent_id="agent-1",
            platform="zalo",
        )
        self.assertEqual(
            self.service.connect_target("agent-1", host_authority=authority),
            self.profile,
        )
        variants = (
            HostAuthority(
                subject="user-1",
                tenant_id="personal-user-1",
                agent_id="someone-else",
                platform="zalo",
            ),
            HostAuthority(
                subject="user-1",
                tenant_id="personal-user-1",
                agent_id="agent-1",
                platform="discord",
            ),
            HostAuthority(
                subject="someone-else",
                tenant_id="personal-user-1",
                agent_id="agent-1",
                platform="zalo",
            ),
            HostAuthority(
                subject="user-1",
                tenant_id="",
                agent_id="agent-1",
                platform="zalo",
            ),
            HostAuthority(
                subject="user-1",
                tenant_id="personal-user-1",
                organization_id="org-1",
                agent_id="agent-1",
                platform="zalo",
            ),
        )
        for variant in variants:
            with self.subTest(variant=variant):
                with self.assertRaises(ServiceError):
                    self.service.connect_target(
                        "agent-1",
                        host_authority=variant,
                    )
        for invalid in (
            {
                "subject": "user-1",
                "tenant_id": "personal-user-1",
                "agent_id": "agent-1",
                "platform": "zalo",
            },
            SimpleNamespace(
                subject="user-1",
                tenant_id="personal-user-1",
                organization_id="",
                agent_id="agent-1",
                platform="zalo",
            ),
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ServiceError):
                    self.service.connect_target("agent-1", host_authority=invalid)
        self.assertEqual(self.profile.joinpath("config.yaml").read_bytes(), config_before)
        self.assertEqual(self.owner_path.read_bytes(), owner_before)

    def test_first_web_connect_needs_no_channel_state_or_binding(self):
        self.profile.joinpath(".community-profile-owner.json").write_text(
            json.dumps(
                {
                    "subject": "user-1",
                    "tenant_id": "personal-user-1",
                    "organization_id": "",
                }
            ),
            encoding="utf-8",
        )
        config = yaml.safe_load(self.profile.joinpath("config.yaml").read_text())
        config["platforms"]["zalo"]["enabled"] = False
        config["platforms"]["zalo_bot"]["enabled"] = False
        self.profile.joinpath("config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        config_before = self.profile.joinpath("config.yaml").read_bytes()
        env_path = self.profile / ".env"
        env_before = env_path.read_bytes() if env_path.exists() else None
        self.assertFalse(self.owner_path.exists())

        self.assertEqual(
            self.service.connect_target("agent-1", trusted=self._trusted()),
            self.profile,
        )
        self.assertFalse(self.owner_path.exists())
        self.assertEqual(self.profile.joinpath("config.yaml").read_bytes(), config_before)
        self.assertEqual(env_path.read_bytes() if env_path.exists() else None, env_before)

        for trusted in (
            self._trusted(subject="someone-else"),
            self._trusted(tenant="other-tenant"),
            self._trusted(organization="org-1"),
        ):
            with self.subTest(trusted=trusted):
                with self.assertRaises(ServiceError):
                    self.service.connect_target("agent-1", trusted=trusted)

        with self.assertRaises(ServiceError) as raised:
            self.service.connect_target("agent-1", trusted=None)
        self.assertEqual(raised.exception.code, "trusted_context_required")
        with self.assertRaises(ServiceError):
            self.service.connect_target(
                "agent-1", trusted={"subject": "user-1", "tenant_id": "personal-user-1"}
            )

    def test_connect_target_respects_channels_feature(self):
        with mock.patch.dict(os.environ, {"FT_ENABLE_AGENT_CHANNELS": "false"}):
            with self.assertRaises(ServiceError) as raised:
                self.service.connect_target("agent-1", trusted=self._trusted())
        self.assertEqual(raised.exception.code, "feature_disabled")

    def test_symlinked_owner_storage_is_rejected(self):
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        self.profile.joinpath(".xnobrain").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(StoreError):
            self.service.repository.write_owner(
                self.profile,
                {
                    "user_id": "user-1",
                    "tenant_id": "",
                    "organization_id": "",
                    "agent_id": "agent-1",
                    "bound_at": 1,
                },
            )
        self.assertFalse((outside / "channels").exists())


class WorkspaceMcpGrantTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        control_mcp_grants._CACHE.clear()
        self.addCleanup(control_mcp_grants._CACHE.clear)
        self.environment = mock.patch.dict(
            os.environ,
            {
                "RUNTIME_CONTROL_URL": "https://control.test",
                "RUNTIME_WORKSPACE_ID": "workspace-1",
                "RUNTIME_INTERNAL_SERVICE_TOKEN": "workspace-secret",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.identity_patch = mock.patch.object(
            workload_identity,
            "DEFAULT_IDENTITY_PATH",
            str(Path(tempfile.gettempdir()) / "absent-workload-identity.json"),
        )
        self.identity_patch.start()
        self.addCleanup(self.identity_patch.stop)

    def _client(self, responder):
        http = httpx.AsyncClient(transport=httpx.MockTransport(responder), follow_redirects=False)
        self.addAsyncCleanup(http.aclose)
        return WorkspaceMcpGrantClient(http)

    @staticmethod
    def _response(
        grant="grant-one",
        *,
        expires_at=None,
        user_id="user-1",
        tenant_id="personal-user-1",
        envelope=False,
    ):
        expiry = expires_at or (datetime.now(timezone.utc) + timedelta(minutes=30))
        body = {
            "grant": grant,
            "expires_at": expiry.isoformat().replace("+00:00", "Z"),
            "user_id": user_id,
            "tenant_id": tenant_id,
        }
        return httpx.Response(200, json={"success": True, "data": body} if envelope else body)

    async def test_request_uses_the_workspace_bearer_and_audit_body(self):
        seen = []

        def respond(request):
            seen.append(request)
            return self._response()

        client = self._client(respond)
        token = await client.grant(
            "profile",
            agent_id="agent-1",
            platform="zalo_bot",
            user_id="user-1",
            tenant_id="personal-user-1",
        )
        self.assertEqual(token, "grant-one")
        self.assertEqual(len(seen), 1)
        request = seen[0]
        self.assertEqual(request.url.path, GRANT_PATH)
        self.assertEqual(request.headers["Authorization"], "Bearer workspace-secret")
        self.assertEqual(request.headers["X-XNOBrain-Workspace-ID"], "workspace-1")
        self.assertEqual(
            json.loads(request.content),
            {"agent_id": "agent-1", "platform": "zalo_bot"},
        )

    async def test_cached_grant_refreshes_two_minutes_before_expiry(self):
        base = time.time()
        calls = []

        def respond(_request):
            calls.append(1)
            expiry = datetime.fromtimestamp(base + 1800 * len(calls), timezone.utc)
            return self._response(grant=f"grant-{len(calls)}", expires_at=expiry)

        client = self._client(respond)
        kwargs = {
            "agent_id": "agent-1",
            "platform": "zalo",
            "user_id": "user-1",
            "tenant_id": "personal-user-1",
        }
        first = await client.grant("profile", now=base, **kwargs)
        cached = await client.grant("profile", now=base + 60, **kwargs)
        refreshed = await client.grant("profile", now=base + 1700, **kwargs)
        again = await client.grant("profile", now=base + 1710, **kwargs)
        self.assertEqual(
            (first, cached, refreshed, again),
            (
                "grant-1",
                "grant-1",
                "grant-2",
                "grant-2",
            ),
        )
        self.assertEqual(len(calls), 2)

    async def test_identity_mismatch_is_never_used_or_cached(self):
        for wrong in ({"user_id": "other-user"}, {"tenant_id": "other-tenant"}):
            with self.subTest(wrong=wrong):
                calls = []

                def respond(_request, _wrong=wrong):
                    calls.append(1)
                    return self._response(**_wrong)

                client = self._client(respond)
                kwargs = {
                    "agent_id": "agent-1",
                    "platform": "zalo",
                    "user_id": "user-1",
                    "tenant_id": "personal-user-1",
                }
                self.assertEqual(await client.grant("profile", **kwargs), "")
                self.assertEqual(await client.grant("profile", **kwargs), "")
                self.assertEqual(len(calls), 2)
                self.assertEqual(control_mcp_grants._CACHE, {})

    async def test_cache_is_bound_to_the_profile_identity(self):
        calls = []

        def respond(request):
            calls.append(1)
            return self._response(grant=f"grant-{len(calls)}", user_id="user-2")

        client = self._client(respond)
        self.assertEqual(
            await client.grant(
                "profile",
                agent_id="agent-1",
                platform="zalo",
                user_id="user-1",
                tenant_id="personal-user-1",
            ),
            "",
        )
        self.assertEqual(
            await client.grant(
                "profile",
                agent_id="agent-1",
                platform="zalo",
                user_id="user-2",
                tenant_id="personal-user-1",
            ),
            "grant-2",
        )
        self.assertEqual(len(calls), 2)

    async def test_expired_denied_or_unreachable_responses_degrade(self):
        base = time.time()

        def denied(_request):
            return httpx.Response(403, json={"code": "workspace_not_allowed"})

        def expired(_request):
            return self._response(expires_at=datetime.fromtimestamp(base - 10, timezone.utc))

        def unreachable(_request):
            raise httpx.ConnectError("private control host")

        kwargs = {
            "agent_id": "agent-1",
            "platform": "zalo",
            "user_id": "user-1",
            "tenant_id": "personal-user-1",
        }
        self.assertEqual(await self._client(denied).grant("profile", **kwargs), "")
        self.assertEqual(await self._client(expired).grant("profile", **kwargs), "")
        self.assertEqual(await self._client(unreachable).grant("profile", **kwargs), "")

    async def test_missing_runtime_credentials_never_make_a_request(self):
        seen = []

        def respond(request):
            seen.append(request)
            return self._response()

        with mock.patch.dict(os.environ, {}, clear=True):
            token = await self._client(respond).grant(
                "profile",
                agent_id="agent-1",
                platform="zalo",
                user_id="user-1",
                tenant_id="personal-user-1",
            )
        self.assertEqual(token, "")
        self.assertEqual(seen, [])

    async def test_unexpired_grant_survives_a_failed_refresh(self):
        base = time.time()
        state = {"fail": False}

        def respond(_request):
            if state["fail"]:
                return httpx.Response(503, json={"code": "grants_not_ready"})
            return self._response(
                grant="grant-one",
                expires_at=datetime.fromtimestamp(base + 1800, timezone.utc),
            )

        client = self._client(respond)
        kwargs = {
            "agent_id": "agent-1",
            "platform": "zalo",
            "user_id": "user-1",
            "tenant_id": "personal-user-1",
        }
        self.assertEqual(await client.grant("profile", now=base, **kwargs), "grant-one")
        state["fail"] = True
        self.assertEqual(await client.grant("profile", now=base + 1700, **kwargs), "grant-one")
        self.assertEqual(await client.grant("profile", now=base + 1801, **kwargs), "")

    async def test_control_data_envelope_is_accepted(self):
        def respond(_request):
            return self._response(envelope=True)

        token = await self._client(respond).grant(
            "profile",
            agent_id="agent-1",
            platform="zalo",
            user_id="user-1",
            tenant_id="personal-user-1",
        )
        self.assertEqual(token, "grant-one")

    async def test_failure_logging_never_contains_the_bearer_or_grant(self):
        records: list[str] = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        logger = logging.getLogger("xnobrain.integrations.control_mcp_grants")
        handler = Capture()
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)

        def denied(_request):
            return httpx.Response(503, json={"grant": "leaked-grant"})

        await self._client(denied).grant(
            "profile",
            agent_id="agent-1",
            platform="zalo",
            user_id="user-1",
            tenant_id="personal-user-1",
        )
        self.assertTrue(records)
        for message in records:
            self.assertNotIn("workspace-secret", message)
            self.assertNotIn("leaked-grant", message)


class ChannelTurnGrantTests(_ChannelCase):
    def setUp(self):
        super().setUp()
        self.grants = _GrantSource(token="channel-grant-token")
        self.service.mcp_grants = self.grants

    def _bind(self):
        self.service.set_channel_enabled(
            "agent-1",
            True,
            trusted=TrustedRequestContext(
                subject="user-1", tenant_id="personal-user-1", organization_id=""
            ),
        )

    async def test_bound_channel_passes_a_host_owned_grant(self):
        self._bind()
        result = await self.service.run_internal_turn(_body(), TOKEN)

        self.assertEqual(result["reply"], "channel reply")
        self.assertEqual(self.platform.mcp_grants, ["channel-grant-token"])
        self.assertEqual(len(self.grants.calls), 1)
        scope, agent_id, platform, user_id, tenant_id = self.grants.calls[0]
        self.assertEqual(
            (agent_id, platform, user_id, tenant_id),
            (
                "agent-1",
                "zalo",
                "user-1",
                "personal-user-1",
            ),
        )
        self.assertTrue(scope.endswith("agent-1"))
        for run_body in self.platform.run_calls:
            self.assertNotIn("_mcp_grant", run_body)
            self.assertNotIn("channel-grant-token", json.dumps(run_body))
        self.assertNotIn("channel-grant-token", self.owner_path.read_text(encoding="utf-8"))

    async def test_unbound_channel_is_refused_before_any_run(self):
        with self.assertRaises(ServiceError) as raised:
            await self.service.run_internal_turn(_body(), TOKEN)

        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(raised.exception.code, "channel_owner_unavailable")
        self.assertEqual(self.platform.run_calls, [])
        self.assertEqual(self.grants.calls, [])

    async def test_binding_for_another_agent_is_refused(self):
        self.service.repository.write_owner(
            self.profile,
            {
                "user_id": "user-1",
                "tenant_id": "personal-user-1",
                "organization_id": "",
                "agent_id": "someone-else",
                "bound_at": 1,
            },
        )
        with self.assertRaises(ServiceError) as raised:
            await self.service.run_internal_turn(_body(), TOKEN)

        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(raised.exception.code, "channel_owner_unavailable")
        self.assertEqual(self.platform.run_calls, [])

    async def test_mismatched_thread_binding_rotates_to_a_fresh_conversation(self):
        self._bind()
        stale_id = "conv_stale_platform"
        self.platform.agents.conversations.add(stale_id)
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
                "conversation_id": stale_id,
                "agent_id": "agent-1",
                "actor_user_id": "user-1",
                "actor_tenant_id": "personal-user-1",
                "executor_workspace_id": None,
                "created_at": "2026-01-01T00:00:00Z",
                "legacy_backfill": False,
                "channel_platform": "zalo_bot",
            },
        )
        self.service.repository.write_thread(
            self.profile,
            "zalo",
            "123456",
            agent_id="agent-1",
            conversation_id=stale_id,
            title="stale",
            sender_id="789",
            timestamp=1,
        )

        result = await self.service.run_internal_turn(_body(), TOKEN)

        self.assertNotEqual(result["conversation_id"], stale_id)
        mapping = self.service.repository.threads(self.profile)["zalo"]["123456"]
        self.assertEqual(mapping["conversation_id"], result["conversation_id"])
        stale = self.repository.get_conversation_context(self.profile, stale_id)
        self.assertEqual(stale["channel_platform"], "zalo_bot")
        self.assertEqual(stale["actor_user_id"], "user-1")

    def test_owner_bound_channel_conversation_remains_web_visible(self):
        from xnobrain.services.conversation_authority import require_binding

        conversation_id = "conv_web_visible"
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
                "agent_id": "agent-1",
                "actor_user_id": "user-1",
                "actor_tenant_id": "personal-user-1",
                "executor_workspace_id": None,
                "created_at": "2026-01-01T00:00:00Z",
                "legacy_backfill": False,
                "channel_platform": "zalo",
            },
        )

        stored = require_binding(
            self.repository,
            "agent-1",
            conversation_id,
            TrustedRequestContext(subject="user-1", tenant_id="personal-user-1"),
        )
        self.assertEqual(stored["channel_platform"], "zalo")

    async def test_grant_failure_or_expired_scope_refuses_before_tools(self):
        self._bind()
        self.service.mcp_grants = _GrantSource(error=RuntimeError("private detail"))
        with self.assertRaises(ServiceError) as raised:
            await self.service.run_internal_turn(_body(), TOKEN)
        self.assertEqual(raised.exception.status, 503)
        self.assertEqual(raised.exception.code, "channel_mcp_grant_unavailable")
        self.assertEqual(self.platform.run_calls, [])

        self.service.mcp_grants = _GrantSource(token="")
        with self.assertRaises(ServiceError) as raised:
            await self.service.run_internal_turn(_body(message_id="message-2"), TOKEN)
        self.assertEqual(raised.exception.status, 503)
        self.assertEqual(raised.exception.code, "channel_mcp_grant_unavailable")
        self.assertEqual(self.platform.run_calls, [])


class _FakeActivity:
    payloads: list[dict] = []

    async def run(self, function, *args):
        _FakeActivity.payloads.append(dict(args[1]))


class _FakeLease:
    def __init__(self, *_args, **_kwargs):
        self.activity = _FakeActivity()

    def close(self):
        return None


class _RunRepositoryAgents:
    def get_conversation(self, agent_id, conversation_id):
        return {"id": conversation_id}


class ConversationRunGrantPayloadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _FakeActivity.payloads = []
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.profiles = root / "profiles"
        (self.profiles / "agent-1").mkdir(parents=True)
        self.environment = mock.patch.dict(
            os.environ,
            {
                "RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": "",
                "FT_ENABLE_CONVERSATION_REASONING_EFFORT": "false",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.repository = FileRepository(root / "data", self.profiles)
        self.service = ConversationRunService(self.repository, _RunRepositoryAgents(), None)

    async def test_explicit_grant_reaches_the_payload_but_never_the_run_record(self):
        with (
            mock.patch("xnobrain.repositories.custom_page_locks.ExecutionLease", _FakeLease),
            mock.patch("xnobrain.repositories.custom_page_locks.ConversationLease", _FakeLease),
        ):
            record = await self.service.start_run(
                "agent-1",
                "conv-1",
                {"input": "hello"},
                mcp_grant="channel-grant-token",
            )
            await asyncio.sleep(0)

        self.assertEqual(_FakeActivity.payloads[-1]["_mcp_grant"], "channel-grant-token")
        self.assertNotIn("_mcp_grant", record)
        self.assertNotIn("channel-grant-token", json.dumps(record))
        persisted = self.repository.get_conversation_run("agent-1", "conv-1", record["id"])
        self.assertNotIn("channel-grant-token", json.dumps(persisted))

    async def test_host_authority_queue_data_round_trips_strictly(self):
        from dataclasses import asdict

        authority = HostAuthority(
            subject="user-1",
            tenant_id="personal-user-1",
            agent_id="agent-1",
            platform="zalo",
        )
        self.assertEqual(host_authority_from_data(asdict(authority)), authority)
        for invalid in (
            {**asdict(authority), "extra": "value"},
            {**asdict(authority), "subject": ""},
            {**asdict(authority), "agent_id": ""},
            {**asdict(authority), "platform": ""},
            {"subject": "user-1"},
            "not-a-mapping",
        ):
            with self.subTest(invalid=invalid):
                self.assertIsNone(host_authority_from_data(invalid))

    async def test_caller_cannot_spoof_channel_host_authority(self):
        with (
            mock.patch("xnobrain.repositories.custom_page_locks.ExecutionLease", _FakeLease),
            mock.patch("xnobrain.repositories.custom_page_locks.ConversationLease", _FakeLease),
        ):
            await self.service.start_run(
                "agent-1",
                "conv-1",
                {
                    "input": "hello",
                    "_channel_host_authority": {
                        "subject": "attacker",
                        "tenant_id": "attacker-tenant",
                        "agent_id": "agent-1",
                        "platform": "zalo",
                    },
                    "_channel_host_authority_data": {
                        "subject": "attacker",
                        "tenant_id": "attacker-tenant",
                        "organization_id": "",
                        "agent_id": "agent-1",
                        "platform": "zalo",
                    },
                },
            )
            await asyncio.sleep(0)

        payload = _FakeActivity.payloads[-1]
        self.assertNotIn("_channel_host_authority", payload)
        self.assertNotIn("_channel_host_authority_data", payload)

    async def test_host_authority_reaches_payload_but_not_run_record(self):
        profile = self.profiles / "agent-1"
        self.repository.create_conversation_context(
            profile,
            {
                "schema_version": 1,
                "id": "personal",
                "owner_kind": "personal",
                "organization_id": None,
                "payer_kind": "personal",
                "sponsor_grant_id": None,
                "state": "active",
                "conversation_id": "conv-1",
                "agent_id": "agent-1",
                "actor_user_id": "user-1",
                "actor_tenant_id": "personal-user-1",
                "executor_workspace_id": None,
                "created_at": "2026-01-01T00:00:00Z",
                "legacy_backfill": False,
                "channel_platform": "zalo",
            },
        )
        authority = HostAuthority(
            subject="user-1",
            tenant_id="personal-user-1",
            agent_id="agent-1",
            platform="zalo",
        )
        with (
            mock.patch("xnobrain.repositories.custom_page_locks.ExecutionLease", _FakeLease),
            mock.patch("xnobrain.repositories.custom_page_locks.ConversationLease", _FakeLease),
        ):
            record = await self.service.start_run(
                "agent-1",
                "conv-1",
                {"input": "hello"},
                host_authority=authority,
            )
            await asyncio.sleep(0)

        payload = _FakeActivity.payloads[-1]
        self.assertIsInstance(payload["_channel_host_authority"], HostAuthority)
        self.assertEqual(
            payload["_channel_host_authority"],
            authority,
        )
        self.assertNotIn("_channel_host_authority", record)
        self.assertNotIn("_channel_host_authority_data", record)


if __name__ == "__main__":
    unittest.main()
