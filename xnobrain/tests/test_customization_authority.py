"""Customization never turns legacy/missing tenant metadata into permission."""

import asyncio
import copy
import json
import unittest

from xnobrain.integrations.custom_page_tools import bind_run, install_tools
from xnobrain.repositories import StoreError
from xnobrain.services.base import ServiceError
from xnobrain.tests import test_custom_page_actions as fixtures
from xnobrain.tests import test_ui_composition as layout_fixtures
from xnobrain.trusted_context import TrustedRequestContext


class CustomizationAuthorityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.CustomPageActionTests.asyncSetUp(self)
        self.path = self.files._conversation_context_path(self.profile, "session")
        self.context = {
            **self.files.get_conversation_context(self.profile, "session"),
            "actor_tenant_id": "tenant",
        }
        self.files.atomic_json(self.path, self.context)

    async def asyncTearDown(self):
        await fixtures.CustomPageActionTests.asyncTearDown(self)

    async def test_no_missing_null_foreign_or_wrong_agent_binding_before_spend(self):
        from xnobrain.services.custom_page_schedules import CustomPageScheduleService

        schedule = CustomPageScheduleService(self.service)
        for change in (
            {"actor_tenant_id": None},
            {"actor_tenant_id": ""},
            {"actor_tenant_id": "foreign"},
            {"actor_user_id": "foreign"},
            {"agent_id": "other-agent"},
            {"legacy_backfill": True},
        ):
            with self.subTest(change=change):
                self.files.atomic_json(self.path, {**self.context, **change})
                with self.assertRaises((ServiceError, StoreError)):
                    await self.service.run_action("research", "analyze", self.body, self.trusted)
                with self.assertRaises((ServiceError, StoreError)):
                    await self.platform.start_conversation_run(
                        "research",
                        "session",
                        {"input": "Make a page", "capabilities": ["custom_page"]},
                        self.trusted,
                    )
                with self.assertRaises((ServiceError, StoreError)):
                    schedule.preview(
                        "research",
                        {
                            "action_id": "analyze",
                            "expected_revision": 1,
                            "conversation_id": "session",
                            "schedule": "0 9 * * *",
                            "timezone": "Etc/UTC",
                            "timeout_seconds": 30,
                            "max_runs": 1,
                            "payer_kind": "personal",
                        },
                        self.trusted,
                    )
        missing = dict(self.context)
        missing.pop("actor_tenant_id")
        self.files.atomic_json(self.path, missing)
        with self.assertRaises((ServiceError, StoreError)):
            await self.service.run_action("research", "analyze", self.body, self.trusted)
        self.assertEqual(self.files.get_conversation_context(self.profile, "session"), missing)
        self.assertEqual(self.analytics.calls, [])
        self.assertEqual(self.files.list_conversation_runs("research", "session"), [])

    async def test_late_budget_admission_cannot_dispatch_after_binding_changes(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def budget(_agent):
            entered.set()
            await release.wait()

        self.analytics.require_execution_budget = budget
        starting = asyncio.create_task(
            self.platform.start_conversation_run(
                "research", "session", {"input": "Ordinary request to use page tools"}, self.trusted
            )
        )
        try:
            await asyncio.wait_for(entered.wait(), 2)
            self.files.atomic_json(self.path, {**self.context, "actor_tenant_id": "foreign"})
            release.set()
            with self.assertRaises((ServiceError, StoreError)):
                await starting
            self.assertEqual(self.files.list_conversation_runs("research", "session"), [])
            self.assertFalse(self.agents.received)
        finally:
            release.set()
            if not starting.done():
                starting.cancel()
            await asyncio.gather(starting, return_exceptions=True)

    async def test_each_page_tool_rechecks_bound_tenant_context_and_run_principal(self):
        from types import SimpleNamespace

        from tools.registry import registry

        first = await self.service.run_action("research", "analyze", self.body, self.trusted)
        with bind_run(self.service, "research", "session", first["id"], self.trusted):
            install_tools(SimpleNamespace(tools=[], valid_tool_names=set()), "session")
            self.assertTrue(
                json.loads(registry.dispatch("custom_page_inspect", {}, session_id="session"))[
                    "success"
                ]
            )
            for change in (
                {"actor_tenant_id": None},
                {"actor_user_id": "foreign"},
                {"state": "revoked_read_only"},
                {"id": "replacement-context"},
            ):
                with self.subTest(change=change):
                    self.files.atomic_json(self.path, {**self.context, **change})
                    self.assertFalse(
                        json.loads(
                            registry.dispatch("custom_page_inspect", {}, session_id="session")
                        )["success"]
                    )
                    self.assertFalse(
                        json.loads(
                            registry.dispatch(
                                "custom_page_query", {"query_id": "latest"}, session_id="session"
                            )
                        )["success"]
                    )
                    self.assertFalse(
                        json.loads(
                            registry.dispatch(
                                "custom_page_write",
                                {
                                    "dataset_id": "articles",
                                    "expected_revision": 1,
                                    "idempotency_key": "denied-write",
                                    "records": [
                                        {
                                            "id": "must-not-store",
                                            "values": {"title": "Denied"},
                                            "provenance": {
                                                "kind": "generated",
                                                "source": "Synthetic",
                                                "collected_at": "2026-09-14T00:00:00Z",
                                            },
                                        }
                                    ],
                                },
                                session_id="session",
                            )
                        )["success"]
                    )
            self.files.atomic_json(self.path, self.context)
            altered = copy.deepcopy(first)
            altered["actor_tenant_id"] = "foreign"
            self.files.put_conversation_run(altered)
            self.assertFalse(
                json.loads(registry.dispatch("custom_page_inspect", {}, session_id="session"))[
                    "success"
                ]
            )
        self.assertEqual(self.service.read("research", self.trusted)["active"], 1)
        self.assertEqual(
            self.service.query("research", "latest", {"expected_revision": 1}, self.trusted)[
                "rows"
            ],
            [],
        )

    async def test_foreign_cannot_bind_or_start_signed_run(self):
        first = await self.service.run_action("research", "analyze", self.body, self.trusted)
        with self.assertRaises((ServiceError, StoreError)):
            with bind_run(
                self.service,
                "research",
                "session",
                first["id"],
                TrustedRequestContext("foreign", "tenant"),
            ):
                self.fail("foreign principal bound to owned run")
        with self.assertRaises((ServiceError, StoreError)):
            await self.runs.start_run(
                "research",
                "session",
                {"input": "No inherited permission", "idempotency_key": "foreign-replay"},
                ownership_context=self.platform._personal_context(),
                trusted_context=TrustedRequestContext("foreign", "tenant"),
            )
        self.assertEqual(self.analytics.calls, ["research"])

    async def test_explicit_empty_tenant_is_distinct_from_absent_for_signed_standalone(self):
        self.files.atomic_json(self.path, {**self.context, "actor_tenant_id": ""})
        run = await self.platform.start_conversation_run(
            "research",
            "session",
            {"input": "Standalone ordinary request"},
            TrustedRequestContext("owner", ""),
        )
        self.assertEqual(run.get("actor_tenant_id"), "")
        self.assertEqual(run.get("actor_user_id"), "owner")


class LayoutAuthorityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await layout_fixtures.UICompositionTests.asyncSetUp(self)

    async def asyncTearDown(self):
        await layout_fixtures.UICompositionTests.asyncTearDown(self)

    async def test_legacy_tenant_is_denied_before_assistance_receipt_or_run(self):
        path = self.files._conversation_context_path(self.profile, "session")
        stored = self.files.get_conversation_context(self.profile, "session")
        stored.pop("actor_tenant_id", None)
        self.files.atomic_json(path, stored)
        with self.assertRaises((ServiceError, StoreError)):
            await self.ui.start(self.body, self.trusted)
        self.assertEqual(self.analytics.calls, [])
        self.assertEqual(self.files.list_conversation_runs("research", "session"), [])
        self.assertEqual(self.files.get_conversation_context(self.profile, "session"), stored)

    async def test_layout_tools_recheck_session_after_run_admission(self):
        from tools.registry import registry

        from xnobrain.integrations.ui_composition_tools import bind_run as bind_layout

        first = await self.ui.start(self.body, self.trusted)
        scope = {"id": self.body["assistance_id"], "owner": "tenant\0owner"}
        path = self.files._conversation_context_path(self.profile, "session")
        stored = self.files.get_conversation_context(self.profile, "session")
        with bind_layout(self.ui, "research", "session", first["run_id"], scope):
            self.files.atomic_json(path, {**stored, "actor_tenant_id": None})
            self.assertFalse(
                json.loads(registry.dispatch("ui_layout_catalog", {}, session_id="session"))[
                    "success"
                ]
            )
            self.assertFalse(
                json.loads(
                    registry.dispatch(
                        "ui_layout_propose", {"layout": self.layout}, session_id="session"
                    )
                )["success"]
            )
        self.assertIsNone(
            self.ui.repository.get("tenant\0owner", self.body["assistance_id"])["result"]
        )


class SignedConversationAPITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from xnobrain.tests import test_conversation_ownership as ownership

        ownership.ConversationOwnershipTests.setUp(self)

    def tearDown(self):
        from xnobrain.tests import test_conversation_ownership as ownership

        ownership.ConversationOwnershipTests.tearDown(self)

    async def test_foreign_and_unbound_session_routes_never_read_write_or_backfill(self):
        from unittest.mock import patch

        from httpx import ASGITransport, AsyncClient

        from xnobrain.tests.test_conversation_runs import FakeAnalytics
        from xnobrain.trusted_context import principal_signature

        agent = "big-brother"

        def headers(tenant):
            return {
                "x-xnobrain-verified-subject": "owner",
                "x-xnobrain-verified-tenant": tenant,
                "x-xnobrain-principal-signature": principal_signature(self.token, "owner", tenant),
            }

        session = self.service.create_conversation(
            agent, {"title": "Owned session"}, TrustedRequestContext("owner", "tenant")
        )["id"]
        unbound = self.service.agents.create_conversation(agent, {"title": "Legacy session"})[
            "conversation"
        ]["id"]
        analytics = FakeAnalytics()
        self.service.conversation_runs.analytics = analytics
        root = "/xnobrain/api/runtime/v1/sessions/" + session
        async with AsyncClient(
            transport=ASGITransport(app=self.app), base_url="http://test"
        ) as client:
            allowed = await client.get(root + "/detail?agent=" + agent, headers=headers("tenant"))
            self.assertEqual(allowed.status_code, 200)
            for method, suffix, body in (
                ("GET", "/detail", None),
                ("GET", "/messages", None),
                ("GET", "/usage", None),
                ("GET", "/runs/active", None),
                ("GET", "/runs/run_foreign", None),
                ("GET", "/runs/run_foreign/events", None),
                ("POST", "/runs", {"input": "No execution"}),
                ("POST", "/runs/run_foreign/stop", {}),
                ("POST", "/runs/run_foreign/approval", {"choice": "once"}),
                ("PATCH", "/name", {"title": "Unwanted rename"}),
                ("DELETE", "/delete", None),
            ):
                with self.subTest(route=suffix):
                    reply = await client.request(
                        method,
                        root + suffix + "?agent=" + agent,
                        headers=headers("foreign"),
                        json=body,
                    )
                    self.assertEqual(reply.status_code, 403, reply.text)
                    self.assertNotIn("Owned session", reply.text)
            listed = await client.get(
                "/xnobrain/api/runtime/v1/sessions?agent=" + agent, headers=headers("foreign")
            )
            self.assertEqual(listed.status_code, 200, listed.text)
            self.assertEqual(listed.json()["data"]["conversations"], [])
            for tenant in ("tenant", "foreign"):
                denied = await client.get(
                    "/xnobrain/api/runtime/v1/sessions/" + unbound + "/detail?agent=" + agent,
                    headers=headers(tenant),
                )
                self.assertEqual(denied.status_code, 403, denied.text)
            self.assertIsNone(self.service.repository.get_conversation_context(self.root, unbound))
            with patch.object(self.service.conversation_runs, "start_run") as start:
                denied = await client.post(
                    "/xnobrain/api/runtime/v1/sessions/" + unbound + "/runs?agent=" + agent,
                    headers=headers("tenant"),
                    json={"input": "No backfill"},
                )
                self.assertEqual(denied.status_code, 403)
                start.assert_not_called()
        self.assertEqual(analytics.calls, [])

    async def test_authorized_run_projection_hides_principal_and_foreign_run_approval_is_denied(
        self,
    ):
        from unittest.mock import patch

        from httpx import ASGITransport, AsyncClient

        from xnobrain.tests.test_conversation_runs import FakeAgents, FakeAnalytics
        from xnobrain.trusted_context import principal_signature

        owner = TrustedRequestContext("owner", "tenant")
        first = self.service.create_conversation("big-brother", {"title": "First"}, owner)["id"]
        second = self.service.create_conversation("big-brother", {"title": "Second"}, owner)["id"]
        self.service.conversation_runs.agents = FakeAgents()
        self.service.conversation_runs.analytics = FakeAnalytics()
        headers = {
            "x-xnobrain-verified-subject": owner.subject,
            "x-xnobrain-verified-tenant": owner.tenant_id,
            "x-xnobrain-principal-signature": principal_signature(
                self.token, owner.subject, owner.tenant_id
            ),
        }
        root = "/xnobrain/api/runtime/v1/sessions/"
        try:
            async with AsyncClient(
                transport=ASGITransport(app=self.app), base_url="http://test", headers=headers
            ) as client:
                response = await client.post(
                    root + first + "/runs?agent=big-brother", json={"input": "Synthetic"}
                )
                self.assertEqual(response.status_code, 202, response.text)
                run = response.json()["data"]
                self.assertNotIn("actor_user_id", run)
                self.assertNotIn("actor_tenant_id", run)
                for suffix in ("/runs/" + run["id"], "/runs/active"):
                    result = (
                        await client.get(root + first + suffix + "?agent=big-brother")
                    ).json()["data"]
                    self.assertNotIn("actor_tenant_id", result.get("run", result))
                with patch.object(self.service.agents, "resolve_approval") as approve:
                    result = await client.post(
                        root + second + "/runs/" + run["id"] + "/approval?agent=big-brother",
                        json={"choice": "once"},
                    )
                    self.assertEqual(result.status_code, 404, result.text)
                    approve.assert_not_called()
        finally:
            await self.service.conversation_runs.shutdown()
