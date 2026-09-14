"""Retain an app after explicit writer removal without opening its authority."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from xnobrain.handlers import APIHandlers
from xnobrain.repositories import FileRepository, StoreError
from xnobrain.routes import setup_routes
from xnobrain.services.agents import AgentsServiceMixin
from xnobrain.services.base import ServiceError
from xnobrain.services.custom_page import CustomPageService
from xnobrain.tests.test_custom_page import news_manifest
from xnobrain.trusted_context import TrustedRequestContext, principal_signature


class RetentionFixture:
    def setup_fixture(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.files = FileRepository(self.root, self.root / "profiles")
        (self.files.profiles_root / "research").mkdir()
        self.platform = SimpleNamespace(
            repository=self.files,
            conversation_runs=SimpleNamespace(_active={}),
            kanban=SimpleNamespace(delete_assignee_tasks=Mock(return_value=0)),
            agents=SimpleNamespace(sync_profiles_registry=Mock()),
            _cache=SimpleNamespace(invalidate=Mock()),
            _is_big_brother=lambda agent: agent == "big-brother",
        )
        self.platform.delete_agent = lambda agent, **kwargs: AgentsServiceMixin.delete_agent(
            self.platform, agent, **kwargs
        )
        self.service = CustomPageService(self.platform)
        self.platform.custom_page = self.service
        self.owner = TrustedRequestContext("owner", "tenant")

    def prepare(self):
        return self.service.prepare(
            "research",
            {
                "manifest": news_manifest(),
                "expected_revision": 0,
                "idempotency_key": "retain-prepare",
            },
            self.owner,
        )

    def archive(self, revision=0):
        return self.service.archive(
            "research",
            {
                "expected_revision": revision,
                "confirmation": "ARCHIVE research",
            },
            self.owner,
        )

    def remove(self, revision=0, owner=None):
        return self.service.remove_agent(
            "research",
            {
                "expected_revision": revision,
                "confirmation": "REMOVE AGENT research",
            },
            owner or self.owner,
        )


class RetentionTests(RetentionFixture, unittest.TestCase):
    def setUp(self):
        self.setup_fixture()

    def tearDown(self):
        self.temporary.cleanup()

    def test_keep_archived_draft_after_removal_and_reconstruct_read_export_delete(self):
        self.assertIsNone(self.service.removal_check("research", self.owner)["app"])
        self.prepare()
        self.assertEqual(
            self.service.removal_check("research", self.owner)["app"]["status"], "draft"
        )
        with self.assertRaises(ServiceError):
            self.platform.delete_agent("research")
        with self.assertRaises(StoreError):
            self.remove()
        self.archive()
        first = self.remove()
        self.assertTrue(first["app_retained"])
        self.assertFalse(self.files.profile_path("research").exists())
        self.assertEqual(self.remove(), first)
        self.assertEqual(self.platform.kanban.delete_assignee_tasks.call_count, 1)
        restarted = CustomPageService(self.platform)
        self.assertEqual(restarted.read("research", self.owner)["status"], "archived")
        caps = restarted.capabilities("research", self.owner)
        self.assertIn("export", caps["operations"])
        self.assertIn("delete", caps["operations"])
        self.assertNotIn("prepare", caps["operations"])
        self.assertNotIn("action", caps["operations"])
        self.assertFalse(caps["scheduled_updates"])

        self.assertEqual(restarted.retained(self.owner)["items"][0]["title"], "News Monitor")
        self.assertEqual(len(restarted.export("research", self.owner)["revisions"]), 1)
        with self.assertRaises(StoreError):
            self.prepare()
        restarted.delete(
            "research", {"expected_revision": 0, "confirmation": "DELETE research"}, self.owner
        )
        self.assertEqual(restarted.retained(self.owner)["items"], [])

    def test_retained_active_rows_still_queryable_but_never_writable(self):
        draft = self.prepare()
        self.service.activate(
            "research",
            {
                "revision": 1,
                "expected_revision": 0,
                "digest": draft["digest"],
                "confirmation": "ACTIVATE " + draft["digest"],
            },
            self.owner,
        )
        self.service.write(
            "research",
            "articles",
            {
                "expected_revision": 1,
                "idempotency_key": "write-before-remove",
                "records": [
                    {
                        "id": "one",
                        "values": {"title": "Stored source"},
                        "provenance": {
                            "kind": "source",
                            "source": "owner-entered",
                            "collected_at": "2026-09-13T00:00:00Z",
                        },
                    }
                ],
            },
            self.owner,
        )
        self.archive(1)
        self.remove(1)
        rows = self.service.query("research", "latest", {"expected_revision": 1}, self.owner)
        self.assertEqual(rows["rows"][0]["values"]["title"], "Stored source")
        self.assertTrue(rows["archived"])
        with self.assertRaises(StoreError):
            self.service.write("research", "articles", {}, self.owner)
        with self.assertRaises(StoreError):
            self.service.activate("research", {}, self.owner)

    def test_plain_removal_checks_work_and_removal_preflight_rejects_damaged_data(self):
        self.platform.conversation_runs._active["run"] = SimpleNamespace(agent_id="research")
        with self.assertRaises(ServiceError):
            self.platform.delete_agent("research")
        self.platform.conversation_runs._active.clear()
        path = self.root / "agent-apps" / "research"
        path.mkdir(parents=True)
        (path / "backup.sqlite3").write_bytes(b"retained-backup")
        with self.assertRaises(StoreError):
            self.service.removal_check("research", self.owner)
        with self.assertRaises(ServiceError):
            self.platform.delete_agent("research")
        self.assertTrue(self.files.profile_path("research").is_dir())

    def test_retained_catalog_does_not_publish_deletion_cleanup_tombstones(self):
        self.prepare()
        self.archive()
        self.remove()
        app = self.root / "agent-apps" / "research"
        app.rename(app.parent / "deleted-cleanup-pending")
        self.assertEqual(self.service.retained(self.owner)["items"], [])

    def test_foreign_organization_and_tenant_cannot_read_or_remove_retained_app(self):
        self.prepare()
        self.archive()
        for owner in [
            TrustedRequestContext("foreign", "tenant"),
            TrustedRequestContext("owner", "foreign"),
        ]:
            with self.assertRaises(StoreError):
                self.remove(owner=owner)
        self.remove()
        for owner in [
            TrustedRequestContext("foreign", "tenant"),
            TrustedRequestContext("owner", "foreign"),
        ]:
            self.assertEqual(self.service.retained(owner)["items"], [])
            with self.assertRaises(StoreError):
                self.service.export("research", owner)
        with self.assertRaises(StoreError):
            self.service.retained(TrustedRequestContext("owner", "tenant", "org"))

    def test_jobs_stale_revision_and_wrong_consent_preserve_profile(self):
        self.prepare()
        self.archive()
        self.platform.conversation_runs._active["active"] = SimpleNamespace(agent_id="research")
        with self.assertRaises(StoreError):
            self.remove()
        self.platform.conversation_runs._active.clear()
        with self.assertRaises(StoreError):
            self.remove(1)
        with self.assertRaises(StoreError):
            self.service.remove_agent(
                "research", {"expected_revision": 0, "confirmation": "DELETE research"}, self.owner
            )
        self.assertTrue(self.files.profile_path("research").is_dir())

    def test_replayed_removal_does_not_delete_a_replacement_profile(self):
        self.prepare()
        self.archive()
        self.remove()
        self.files.profile_path("research").mkdir()
        with self.assertRaisesRegex(StoreError, "agent replaced"):
            self.remove()
        self.assertTrue(self.files.profile_path("research").is_dir())
        self.assertEqual(self.platform.kanban.delete_assignee_tasks.call_count, 1)

    def test_interrupted_removal_fails_closed_and_incomplete_app_blocks_plain_delete(self):
        self.prepare()
        self.archive()
        with patch.object(self.platform, "delete_agent", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                self.remove()
        with self.assertRaisesRegex(StoreError, "removal interrupted"):
            self.remove()
        self.assertTrue(self.files.profile_path("research").is_dir())
        (self.root / "agent-apps" / "research" / "app.sqlite3").unlink()
        (self.root / "agent-apps" / "research" / "backup.sqlite3").touch()
        with self.assertRaises(ServiceError):
            self.platform.delete_agent("research")

    def test_retained_catalog_is_paginated_and_checks_owner_before_manifest(self):
        self.prepare()
        self.archive()
        self.remove()
        second = "z-second"
        self.files.profile_path(second).mkdir()
        self.service.prepare(
            second,
            {
                "manifest": news_manifest(),
                "expected_revision": 0,
                "idempotency_key": "second-prepare",
            },
            self.owner,
        )
        self.service.archive(
            second, {"expected_revision": 0, "confirmation": f"ARCHIVE {second}"}, self.owner
        )
        self.service.remove_agent(
            second, {"expected_revision": 0, "confirmation": f"REMOVE AGENT {second}"}, self.owner
        )
        first = self.service.retained(self.owner, 0, 1)
        self.assertEqual(first["next_offset"], 1)
        self.assertEqual(first["items"][0]["agent_id"], "research")
        self.assertEqual(self.service.retained(self.owner, 1, 1)["items"][0]["agent_id"], second)
        self.assertIsNone(self.service.retained(self.owner, 1, 1)["next_offset"])
        for offset, limit in [(-1, 1), (1001, 1), (0, 101)]:
            with self.assertRaises(StoreError):
                self.service.retained(self.owner, offset, limit)

    def test_removed_profile_cannot_be_recreated_by_late_draft_admission(self):
        self.files.profile_path("research").rmdir()
        with self.assertRaises(StoreError):
            self.service.repository.prepare(
                "research",
                "tenant\0owner",
                {
                    "manifest": news_manifest(),
                    "expected_revision": 0,
                    "idempotency_key": "stale-prepare",
                },
            )
        self.assertFalse((self.root / "agent-apps" / "research").exists())


class RetentionAPITests(RetentionFixture, unittest.IsolatedAsyncioTestCase):
    async def test_signed_lifecycle_route_and_private_retained_catalog(self):
        self.setup_fixture()
        try:
            app = FastAPI()
            setup_routes(app, APIHandlers(self.platform))
            base = "/xnobrain/api/runtime/v1/agents/research/custom-page"
            catalog = "/xnobrain/api/runtime/v1/custom-pages/retained"
            with patch.dict("os.environ", {"RUNTIME_INTERNAL_SERVICE_TOKEN": "synthetic"}):
                headers = {
                    "x-xnobrain-verified-subject": "owner",
                    "x-xnobrain-principal-signature": principal_signature("synthetic", "owner"),
                }
                self.owner = TrustedRequestContext("owner", "")
                self.prepare()
                self.archive()
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    self.assertEqual((await client.get(catalog)).status_code, 401)
                    self.assertEqual(
                        (await client.get(base + "/removal-check", headers=headers)).status_code,
                        200,
                    )
                    removed = await client.post(
                        base + "/remove-agent",
                        headers=headers,
                        json={"expected_revision": 0, "confirmation": "REMOVE AGENT research"},
                    )
                    self.assertEqual(removed.status_code, 200, removed.text)
                    retained = await client.get(catalog, headers=headers)
                    self.assertEqual(retained.status_code, 200, retained.text)
                    self.assertEqual(retained.headers["cache-control"], "private, no-store")
                    self.assertEqual(retained.json()["data"]["items"][0]["agent_id"], "research")
                    self.assertEqual(
                        (await client.get(catalog + "?limit=1000", headers=headers)).status_code,
                        422,
                    )
                    self.assertEqual((await client.get(base, headers=headers)).status_code, 200)
                    foreign_headers = {
                        "x-xnobrain-verified-subject": "foreign",
                        "x-xnobrain-principal-signature": principal_signature(
                            "synthetic", "foreign"
                        ),
                    }
                    foreign_list = await client.get(catalog, headers=foreign_headers)
                    self.assertEqual(foreign_list.json()["data"]["items"], [])
                    denied = await client.get(base + "/export", headers=foreign_headers)
                    self.assertEqual(denied.status_code, 404)
                    self.assertEqual(denied.headers["cache-control"], "private, no-store")
                    invalid = await client.post(
                        base + "/remove-agent",
                        headers=headers,
                        json={
                            "expected_revision": 0,
                            "confirmation": "REMOVE AGENT research",
                            "force": True,
                        },
                    )
                    self.assertEqual(invalid.status_code, 422)
        finally:
            self.temporary.cleanup()
