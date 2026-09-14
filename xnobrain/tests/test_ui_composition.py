"""Scoped layout-only engine tools with real durable runs and no model calls."""

import asyncio
import copy
import json
import unittest
from types import SimpleNamespace

from xnobrain.integrations.ui_composition_tools import bind_run, install_tools
from xnobrain.repositories import StoreError
from xnobrain.services.ui_composition import UICompositionService
from xnobrain.tests import test_custom_page_actions as fixtures
from xnobrain.trusted_context import TrustedRequestContext


class UICompositionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.CustomPageActionTests.asyncSetUp(self)
        self.ui = UICompositionService(self.platform)
        self.layout = {
            "schema_version": 1,
            "name": "Research",
            "preset": "two-column",
            "widgets": [{"id": "agents", "kind": "agents", "slot": "dashboard"}],
        }
        self.body = {
            "schema_version": 1,
            "assistance_id": "uia_synthetic",
            "layout_id": "uil_owned",
            "base_revision": 1,
            "context": "personal",
            "agent_id": "research",
            "conversation_id": "session",
            "request": "Make a research layout",
            "timeout_seconds": 30,
            "catalog": [{"kind": "agents", "slots": ["dashboard"], "version": 1}],
            "layout": self.layout,
        }

    async def asyncTearDown(self):
        await fixtures.CustomPageActionTests.asyncTearDown(self)

    async def test_one_parent_stages_layout_without_apply_or_ambient_tools(self):
        from tools.registry import registry

        first = await self.ui.start(self.body, self.trusted)
        replay = await self.ui.start(self.body, self.trusted)
        self.assertEqual(replay["run_id"], first["run_id"])
        self.assertEqual(self.analytics.calls, ["research"])
        self.assertIsNone(first["layout"])
        scope = {"id": self.body["assistance_id"], "owner": "tenant\0owner"}
        with bind_run(self.ui, "research", "session", first["run_id"], scope):
            agent = SimpleNamespace(
                tools=[{"function": {"name": "terminal"}}], valid_tool_names={"terminal"}
            )
            install_tools(agent, "session")
            self.assertEqual(agent.valid_tool_names, {"ui_layout_catalog", "ui_layout_propose"})
            self.assertEqual(agent.max_iterations, 10)
            catalog = json.loads(registry.dispatch("ui_layout_catalog", {}, session_id="session"))
            self.assertEqual(catalog["data"]["layout_id"], "uil_owned")
            proposal = copy.deepcopy(self.layout)
            proposal["name"] = "Focused research"
            proposal["accent"] = "violet"
            proposal["inspector_position"] = "left"
            proposal["inspector_height"] = 300
            self.assertEqual(catalog["data"]["inspector_positions"], ["right", "left", "bottom"])
            proposal["navigation"] = {"order": ["skills"], "hidden": ["cron"]}
            proposal["default_page"] = "skills"
            self.assertIn("skills", catalog["data"]["navigation_options"])
            self.assertEqual(
                catalog["data"]["accent_options"], ["default", "teal", "blue", "violet"]
            )
            result = json.loads(
                registry.dispatch("ui_layout_propose", {"layout": proposal}, session_id="session")
            )
            self.assertTrue(result["success"])
            self.assertTrue(result["data"]["approval_required"])
            self.assertIsNone(self.ui.read("uia_synthetic", self.trusted)["layout"])
            unsafe = {**proposal, "script": "window.fetch"}
            self.assertFalse(
                json.loads(
                    registry.dispatch("ui_layout_propose", {"layout": unsafe}, session_id="session")
                )["success"]
            )
            self.assertIn(
                "error", json.loads(registry.dispatch("ui_layout_catalog", {}, session_id="other"))
            )
            self.agents.release.set()
            await asyncio.wait_for(self.agents.finished.wait(), 2)
            self.assertEqual(
                self.ui.read("uia_synthetic", self.trusted)["layout"]["name"], "Focused research"
            )
            self.assertFalse(
                json.loads(registry.dispatch("ui_layout_catalog", {}, session_id="session"))[
                    "success"
                ]
            )
        self.assertIn(
            "error", json.loads(registry.dispatch("ui_layout_catalog", {}, session_id="session"))
        )
        restarted = UICompositionService(self.platform)
        self.assertEqual(restarted.read("uia_synthetic", self.trusted)["layout"], proposal)
        self.assertEqual(self.service.read("research", self.trusted)["active"], 1)

    async def test_foreign_context_conflicting_retry_and_cancellation(self):
        with self.assertRaises(StoreError):
            await self.ui.start(self.body, TrustedRequestContext("foreign", "tenant"))
        with self.assertRaises(StoreError):
            await self.ui.start({**self.body, "context": "org_other"}, self.trusted)
        self.assertEqual(self.analytics.calls, [])
        await self.ui.start(self.body, self.trusted)
        with self.assertRaises(StoreError):
            await self.ui.start({**self.body, "request": "different"}, self.trusted)
        stopped = await self.ui.cancel("uia_synthetic", self.trusted)
        self.assertEqual(stopped["status"], "cancelled")
        with self.assertRaises(StoreError):
            await self.ui.start(self.body, self.trusted)
        self.assertEqual(self.analytics.calls, ["research"])

    async def test_signed_facade_routes_and_body_rejections(self):
        from unittest.mock import patch

        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from xnobrain.handlers import APIHandlers
        from xnobrain.routes import setup_routes
        from xnobrain.trusted_context import principal_signature

        self.platform.ui_composition = self.ui
        self.platform.custom_page = self.service
        app = FastAPI()
        setup_routes(app, APIHandlers(self.platform))
        headers = {
            "x-xnobrain-verified-subject": "owner",
            "x-xnobrain-verified-tenant": "tenant",
            "x-xnobrain-principal-signature": principal_signature("synthetic", "owner", "tenant"),
        }
        with patch.dict("os.environ", {"RUNTIME_INTERNAL_SERVICE_TOKEN": "synthetic"}):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                root = "/xnobrain/api/runtime/v1/ui-assistance"
                self.assertEqual((await client.post(root, json=self.body)).status_code, 401)
                self.assertEqual(
                    (
                        await client.post(
                            root, json={**self.body, "code": "fetch('private')"}, headers=headers
                        )
                    ).status_code,
                    422,
                )
                started = await client.post(root, json=self.body, headers=headers)
                self.assertEqual(started.status_code, 202, started.text)
                self.assertEqual(started.headers["cache-control"], "private, no-store")
                read = await client.get(root + "/uia_synthetic", headers=headers)
                self.assertEqual(read.status_code, 200, read.text)
                self.assertEqual(read.headers["cache-control"], "private, no-store")
                self.assertEqual(self.analytics.calls, ["research"])
                stopped = await client.post(root + "/uia_synthetic/cancel", headers=headers)
                self.assertEqual(stopped.status_code, 200, stopped.text)
                self.assertEqual(stopped.json()["data"]["status"], "cancelled")

    async def test_cancellation_during_admission_does_not_start_parent(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def budget(_agent):
            entered.set()
            await release.wait()

        self.analytics.require_execution_budget = budget
        pending = asyncio.create_task(self.ui.start(self.body, self.trusted))
        await entered.wait()
        result = await self.ui.cancel("uia_synthetic", self.trusted)
        self.assertEqual(result["status"], "cancelled")
        release.set()
        with self.assertRaises(StoreError):
            await pending
        self.assertEqual(self.files.list_conversation_runs("research", "session"), [])

    async def test_cancel_before_delayed_start_leaves_no_paid_run(self):
        cancelled = await self.ui.cancel(self.body["assistance_id"], self.trusted)
        self.assertEqual(cancelled["status"], "cancelled")
        with self.assertRaises(StoreError):
            await self.ui.start(self.body, self.trusted)
        self.assertEqual(self.analytics.calls, [])

    async def test_layout_deadline_stops_existing_run_without_resubmission(self):
        started = await self.ui.start({**self.body, "timeout_seconds": 10}, self.trusted)
        run_id = started["run_id"]
        entry = self.runs._active[run_id]
        await asyncio.wait_for(asyncio.shield(entry.task), 12)
        observed = self.ui.read(self.body["assistance_id"], self.trusted)
        self.assertEqual(observed["status"], "timed_out")
        self.assertTrue(self.agents.cancelled.is_set())
        self.assertEqual(self.analytics.calls, ["research"])
        record = self.files.get_conversation_run("research", "session", run_id)
        self.assertEqual(record["execution_budget"]["turn_limit"], 10)

    async def test_organization_context_preserves_bound_payer_and_denies_other_scope(self):
        profile_context = self.files._conversation_context_path(self.profile, "session")
        current = self.files.get_conversation_context(self.profile, "session")
        self.files.atomic_json(
            profile_context,
            {
                **current,
                "owner_kind": "organization",
                "organization_id": "org_one",
                "payer_kind": "organization_sponsor",
                "sponsor_grant_id": "sponsor_one",
            },
        )
        with self.assertRaises(StoreError):
            await self.ui.start(self.body, self.trusted)
        started = await self.ui.start({**self.body, "context": "org_one"}, self.trusted)
        self.assertEqual(started["payer_kind"], "organization_sponsor")
        await self.ui.cancel(self.body["assistance_id"], self.trusted)

    async def test_tenant_bound_session_cannot_stage_for_another_tenant(self):
        stored = self.files.get_conversation_context(self.profile, "session")
        self.files.atomic_json(
            self.files._conversation_context_path(self.profile, "session"),
            {
                **stored,
                "actor_tenant_id": "tenant",
            },
        )
        with self.assertRaises(StoreError):
            await self.ui.start(self.body, TrustedRequestContext("owner", "other-tenant"))
        self.assertEqual(self.analytics.calls, [])

    async def test_accents_are_closed_before_any_run(self):
        for accent in ("", "#fff", "url(https://foreign.test)", "RED", None, {}, []):
            with self.subTest(accent=accent), self.assertRaises(StoreError):
                await self.ui.start(
                    {**self.body, "layout": {**self.layout, "accent": accent}}, self.trusted
                )
        self.assertEqual(self.analytics.calls, [])
        for accent in ("default", "teal", "blue", "violet"):
            self.ui.validate_layout({**self.layout, "accent": accent}, self.body["catalog"])

    async def test_navigation_is_declarative_bounded_and_non_executing(self):
        layout = {
            **self.layout,
            "navigation": {"order": ["skills", "kanban"], "hidden": ["cron"]},
            "default_page": "skills",
        }
        self.ui.validate_layout(layout, self.body["catalog"])
        for navigation in (
            None,
            {},
            {"order": [], "hidden": None},
            {"order": ["files", "files"], "hidden": []},
            {"order": [], "hidden": ["home"]},
            {"order": [], "hidden": ["settings"]},
            {"order": ["https://foreign.test"], "hidden": []},
            {"order": [], "hidden": [], "role": "org_owner"},
        ):
            with self.subTest(navigation=navigation), self.assertRaises(StoreError):
                self.ui.validate_layout({**layout, "navigation": navigation}, self.body["catalog"])
        for page in (None, "", {}, [], "/agents/foreign", "javascript:alert(1)", "payer"):
            with self.subTest(page=page), self.assertRaises(StoreError):
                self.ui.validate_layout({**layout, "default_page": page}, self.body["catalog"])
        self.assertEqual(self.analytics.calls, [])

    async def test_panel_placement_cannot_hide_shell_or_inject_styles(self):
        for position in ("right", "left", "bottom"):
            self.ui.validate_layout(
                {**self.layout, "inspector_position": position, "inspector_height": 280},
                self.body["catalog"],
            )
        for fields in (
            {"inspector_position": None},
            {"inspector_position": "hidden"},
            {"inspector_position": {}},
            {"inspector_position": "url(https://bad.test)"},
            {"inspector_height": None},
            {"inspector_height": "280"},
            {"inspector_height": 280.5},
            {"inspector_height": True},
            {"inspector_height": 0},
            {"inspector_height": 481},
        ):
            with self.subTest(fields=fields), self.assertRaises(StoreError):
                self.ui.validate_layout({**self.layout, **fields}, self.body["catalog"])
        self.assertEqual(self.analytics.calls, [])
