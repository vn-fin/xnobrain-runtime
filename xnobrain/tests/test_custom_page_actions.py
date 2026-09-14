"""Named actions use the durable parent runner and real registry, not a page timer."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from xnobrain.integrations.custom_page_tools import bind_run, install_tools
from xnobrain.repositories import FileRepository, StoreError
from xnobrain.services.conversation_runs import ConversationRunService
from xnobrain.services.conversations import ConversationsServiceMixin
from xnobrain.services.custom_page import CustomPageService
from xnobrain.tests.test_conversation_runs import FakeAgents, FakeAnalytics
from xnobrain.tests.test_custom_page import news_manifest
from xnobrain.trusted_context import TrustedRequestContext


class PagePlatform(ConversationsServiceMixin):
    def _agent_profile_path(self, agent_id):
        return self.repository.profiles_root / agent_id


class CustomPageActionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.files = FileRepository(root, root / "profiles")
        self.profile = root / "profiles" / "research"
        self.profile.mkdir()
        self.agents = FakeAgents()
        self.analytics = FakeAnalytics()
        self.runs = ConversationRunService(self.files, self.agents, self.analytics)
        self.platform = PagePlatform()
        self.platform.repository = self.files
        self.platform.agents = self.agents
        self.platform.conversation_runs = self.runs
        self.service = CustomPageService(self.platform)
        self.trusted = TrustedRequestContext("owner", "tenant")
        self.files.create_conversation_context(
            self.profile,
            {
                **self.platform._personal_context(),
                "conversation_id": "session",
                "agent_id": "research",
                "actor_user_id": "owner",
                "actor_tenant_id": "tenant",
            },
        )
        self.manifest = news_manifest()
        self.manifest["datasets"].append(
            {
                "id": "private",
                "label": "Private",
                "fields": [
                    {"id": "secret", "label": "Secret", "type": "string"},
                ],
            }
        )
        self.manifest["queries"].append({"id": "private_data", "dataset": "private"})
        self.manifest["actions"] = [
            {
                "id": "analyze",
                "label": "Analyze articles",
                "kind": "analyze",
                "instruction": "Analyze approved stored articles",
                "datasets": ["articles"],
            }
        ]
        draft = self.service.prepare(
            "research",
            {"expected_revision": 0, "idempotency_key": "prepare-page", "manifest": self.manifest},
            self.trusted,
        )
        self.service.activate(
            "research",
            {
                "expected_revision": 0,
                "revision": 1,
                "digest": draft["digest"],
                "confirmation": "ACTIVATE " + draft["digest"],
            },
            self.trusted,
        )
        self.body = {
            "expected_revision": 1,
            "conversation_id": "session",
            "idempotency_key": "analyze-once",
            "confirmation": "RUN analyze",
            "timeout_seconds": 60,
        }

    async def asyncTearDown(self):
        await self.runs.shutdown()
        self.temp.cleanup()

    async def test_action_denies_a_conversation_bound_to_another_tenant(self):
        stored = self.files.get_conversation_context(self.profile, "session")
        self.files.atomic_json(
            self.files._conversation_context_path(self.profile, "session"),
            {
                **stored,
                "actor_tenant_id": "other-tenant",
            },
        )
        with self.assertRaises(StoreError):
            await self.service.run_action("research", "analyze", self.body, self.trusted)
        self.assertEqual(self.analytics.calls, [])

    async def test_action_single_admission_scoped_tools_and_revision_revocation(self):
        from tools.registry import registry

        first = await self.service.run_action("research", "analyze", self.body, self.trusted)
        replay = await self.service.run_action("research", "analyze", self.body, self.trusted)
        self.assertEqual(first["id"], replay["id"])
        self.assertEqual(self.analytics.calls, ["research"])
        self.assertEqual(first["ownership_context"]["payer_kind"], "personal")
        self.assertEqual(first["custom_page_revision"], 1)
        self.assertEqual(first["custom_page_datasets"], ["articles"])
        self.assertEqual(len(self.files.list_conversation_runs("research", "session")), 1)
        with bind_run(self.service, "research", "session", first["id"], self.trusted):
            model = SimpleNamespace(
                tools=[
                    {"type": "function", "function": {"name": name}}
                    for name in ("terminal", "read_file", "delegate_task", "cronjob", "web_search")
                ],
                valid_tool_names={
                    "terminal",
                    "read_file",
                    "delegate_task",
                    "cronjob",
                    "web_search",
                },
            )
            install_tools(model, "session")
            self.assertEqual(
                model.valid_tool_names,
                {"web_search", "custom_page_inspect", "custom_page_query", "custom_page_write"},
            )
            self.assertEqual(model.max_iterations, 20)

            def call(name, body):
                return json.loads(registry.dispatch(name, body, session_id="session"))

            write = {
                "dataset_id": "articles",
                "expected_revision": 1,
                "idempotency_key": "scoped-write",
                "records": [
                    {
                        "id": "source-1",
                        "values": {"title": "Approved analysis"},
                        "provenance": {
                            "kind": "generated",
                            "source": "approved source",
                            "run_id": "forged",
                            "collected_at": "2026-09-13T00:00:00Z",
                        },
                    }
                ],
            }
            self.assertTrue(call("custom_page_write", write)["success"])
            result = call("custom_page_query", {"query_id": "latest"})
            self.assertEqual(result["data"]["rows"][0]["provenance"]["run_id"], first["id"])
            self.assertFalse(call("custom_page_query", {"query_id": "private_data"})["success"])
            self.assertFalse(
                call("custom_page_write", {**write, "dataset_id": "private"})["success"]
            )
            self.assertFalse(call("custom_page_prepare", {})["success"])
            updated = self.service.prepare(
                "research",
                {
                    "manifest": self.manifest,
                    "expected_revision": 1,
                    "idempotency_key": "next-page-draft",
                },
                self.trusted,
            )
            self.service.activate(
                "research",
                {
                    "expected_revision": 1,
                    "revision": updated["revision"],
                    "digest": updated["digest"],
                    "confirmation": "ACTIVATE " + updated["digest"],
                },
                self.trusted,
            )
            self.assertFalse(call("custom_page_query", {"query_id": "latest"})["success"])
            self.assertFalse(
                call("custom_page_write", {**write, "expected_revision": 2})["success"]
            )
        self.agents.release.set()
        await asyncio.wait_for(self.agents.finished.wait(), timeout=2)
        self.assertEqual(self.analytics.calls, ["research"])

    async def test_action_rejects_foreign_org_and_unconfirmed_requests_without_budget(self):
        for trusted in (
            TrustedRequestContext("other", "tenant"),
            TrustedRequestContext("owner", "tenant", "org"),
        ):
            with self.assertRaises(StoreError):
                await self.service.run_action("research", "analyze", self.body, trusted)
        with self.assertRaises(StoreError):
            await self.service.run_action(
                "research", "analyze", {**self.body, "confirmation": "yes"}, self.trusted
            )
        self.files.create_conversation_context(
            self.profile,
            {
                **self.platform._personal_context(),
                "conversation_id": "foreign",
                "agent_id": "research",
                "actor_user_id": "other",
            },
        )
        with self.assertRaises(StoreError):
            await self.service.run_action(
                "research", "analyze", {**self.body, "conversation_id": "foreign"}, self.trusted
            )
        self.assertEqual(self.analytics.calls, [])

    async def test_archive_during_budget_admission_denies_late_action_without_run(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def wait_budget(_agent):
            entered.set()
            await release.wait()

        self.analytics.require_execution_budget = wait_budget
        starting = asyncio.create_task(
            self.service.run_action("research", "analyze", self.body, self.trusted)
        )
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            self.service.archive(
                "research",
                {"expected_revision": 1, "confirmation": "ARCHIVE research"},
                self.trusted,
            )
            release.set()
            with self.assertRaisesRegex(StoreError, "revision conflict"):
                await starting
            self.assertFalse(self.runs._active)
            self.assertFalse(self.agents.received)
            self.assertEqual(self.files.list_conversation_runs("research", "session"), [])
        finally:
            release.set()
            if not starting.done():
                starting.cancel()
                await asyncio.gather(starting, return_exceptions=True)

    async def test_action_deadline_is_parent_owned_and_does_not_replay(self):
        body = {**self.body, "timeout_seconds": 10}
        first = await self.service.run_action("research", "analyze", body, self.trusted)
        entry = self.runs._active[first["id"]]
        await asyncio.wait_for(asyncio.shield(entry.task), 12)
        self.assertEqual(
            self.runs.get_run("research", "session", first["id"])["status"], "timed_out"
        )
        self.assertTrue(self.agents.cancelled.is_set())
        replay = await self.service.run_action("research", "analyze", body, self.trusted)
        self.assertEqual(replay["id"], first["id"])
        self.assertEqual(replay["status"], "timed_out")
        self.assertEqual(self.analytics.calls, ["research"])
