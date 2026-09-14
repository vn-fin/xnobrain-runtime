"""Exact recovery of interrupted removal with real SQLite and profile lifecycle."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from xnobrain.repositories import StoreError
from xnobrain.services.custom_page import CustomPageService
from xnobrain.tests.test_custom_page_retention import RetentionFixture
from xnobrain.trusted_context import TrustedRequestContext


def process_recover(root, selected, output):
    from pathlib import Path

    from xnobrain.repositories import FileRepository
    from xnobrain.repositories.custom_page import CustomPageRepository

    files = FileRepository(root, Path(root) / "profiles")
    pages = CustomPageRepository(files)
    try:
        result = pages.recover_removal(
            "research",
            "tenant\0owner",
            selected,
            lambda: files.hard_delete_profile("research"),
            lambda: None,
        )
        output.send(result)
    except StoreError as error:
        output.send({"error": error.code})
    finally:
        output.close()


class RemovalRecoveryTests(RetentionFixture, unittest.TestCase):
    def setUp(self):
        self.setup_fixture()
        self.prepare()
        self.archive()

    def tearDown(self):
        self.temporary.cleanup()

    def interrupt(self):
        with patch.object(self.platform, "delete_agent", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                self.remove()

    def selected(self):
        return self.service.removal_recovery("research", self.owner)

    def recover(self, selected=None):
        selected = selected or self.selected()
        return self.service.recover_removal(
            "research",
            {
                "operation_id": selected["operation_id"],
                "expected_revision": selected["expected_revision"],
                "digest": selected["digest"],
                "confirmation": "RECOVER REMOVAL " + selected["digest"],
            },
            self.owner,
        )

    def test_explicit_recovery_after_failure_and_service_restart_retains_data(self):
        self.assertEqual(self.selected()["state"], "none")
        self.interrupt()
        selected = self.selected()
        self.assertEqual(selected["state"], "recoverable")
        self.assertEqual(selected["profile_state"], "original")
        self.assertEqual(selected["next_action"], "remove_original")
        self.assertNotIn("profile_inode", selected)
        self.assertTrue(self.files.profile_path("research").is_dir())
        self.service = CustomPageService(self.platform)
        result = self.recover(selected)
        self.assertTrue(result["app_retained"])
        self.assertEqual(self.recover(selected), result)
        self.assertFalse(self.files.profile_path("research").exists())
        self.assertEqual(self.selected()["state"], "complete")
        self.assertEqual(len(self.service.export("research", self.owner)["revisions"]), 1)
        self.assertEqual(self.platform.kanban.delete_assignee_tasks.call_count, 1)

    def test_original_plan_cannot_remove_replacement(self):
        self.interrupt()
        selected = self.selected()
        profile = self.files.profile_path("research")
        profile.rename(profile.with_name("original-profile"))
        profile.mkdir()
        (profile / "keep.txt").write_text("replacement")
        self.assertEqual(self.selected()["state"], "blocked")
        self.assertEqual(self.selected()["profile_state"], "replaced")
        with self.assertRaises(StoreError):
            self.recover(selected)
        self.assertEqual((profile / "keep.txt").read_text(), "replacement")

    def test_missing_profile_requires_new_finalize_plan_and_repeats_registry_cleanup(self):
        self.interrupt()
        original = self.selected()
        self.files.hard_delete_profile("research")
        final = self.selected()
        self.assertEqual(final["next_action"], "finalize")
        self.assertNotEqual(original["digest"], final["digest"])
        with self.assertRaises(StoreError):
            self.recover(original)
        with patch.object(
            self.platform.agents,
            "sync_profiles_registry",
            side_effect=OSError("registry unavailable"),
        ):
            with self.assertRaises(OSError):
                self.recover(final)
        self.assertEqual(self.selected()["state"], "recoverable")
        self.recover(final)
        self.platform._cache.invalidate.assert_called_with("agents")
        self.assertEqual(self.platform.kanban.delete_assignee_tasks.call_count, 0)

    def test_legacy_pending_journal_blocks_without_guessing_identity(self):
        with self.service.repository.database("research", "tenant\0owner", write=True) as db:
            db.execute("CREATE TABLE agent_removal(singleton INTEGER PRIMARY KEY,state TEXT)")
            db.execute("INSERT INTO agent_removal VALUES(1,'pending')")
        self.assertEqual(self.selected()["state"], "blocked")
        self.assertIsNone(self.selected()["digest"])
        self.assertTrue(self.files.profile_path("research").is_dir())

    def test_foreign_owner_bad_consent_and_active_jobs_deny_recovery(self):
        from types import SimpleNamespace

        self.interrupt()
        selected = self.selected()
        with self.assertRaises(StoreError):
            self.service.removal_recovery("research", TrustedRequestContext("other", "tenant"))
        with self.assertRaises(StoreError):
            self.service.recover_removal(
                "research",
                {
                    "operation_id": selected["operation_id"],
                    "expected_revision": 0,
                    "digest": selected["digest"],
                    "confirmation": "REMOVE AGENT research",
                },
                self.owner,
            )
        self.platform.conversation_runs._active["run"] = SimpleNamespace(agent_id="research")
        with self.assertRaises(StoreError):
            self.recover(selected)
        self.assertTrue(self.files.profile_path("research").exists())

    def test_independent_process_recovers_and_parent_replay_cannot_delete_again(self):
        import multiprocessing

        self.interrupt()
        selected = self.selected()
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(target=process_recover, args=(str(self.root), selected, child))
        process.start()
        child.close()
        try:
            self.assertTrue(parent.poll(15), "recovery process timed out")
            result = parent.recv()
            self.assertTrue(result.get("app_retained"), result)
            process.join(15)
            self.assertEqual(process.exitcode, 0)
            self.assertEqual(self.recover(selected), result)
            self.assertEqual(self.platform.kanban.delete_assignee_tasks.call_count, 0)
        finally:
            if process.is_alive():
                process.terminate()
                process.join(5)
            parent.close()

    def test_live_execution_blocks_recovery_across_lock_instances(self):
        from xnobrain.repositories.custom_page_locks import ExecutionLease

        self.interrupt()
        selected = self.selected()
        lease = ExecutionLease(self.root, "research")
        try:
            with self.assertRaisesRegex(StoreError, "jobs active"):
                self.recover(selected)
        finally:
            lease.close()
        self.assertTrue(self.files.profile_path("research").is_dir())


class RemovalRecoveryHTTPTests(RetentionFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.setup_fixture()
        self.prepare()
        self.archive()
        with patch.object(self.platform, "delete_agent", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                self.remove()

    async def asyncTearDown(self):
        self.temporary.cleanup()

    async def test_signed_plan_and_exact_recovery_routes(self):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from xnobrain.handlers import APIHandlers
        from xnobrain.routes import setup_routes
        from xnobrain.trusted_context import principal_signature

        app = FastAPI()
        setup_routes(app, APIHandlers(self.platform))
        root = "/xnobrain/api/runtime/v1/agents/research/custom-page"
        headers = {
            "x-xnobrain-verified-subject": "owner",
            "x-xnobrain-verified-tenant": "tenant",
            "x-xnobrain-principal-signature": principal_signature("synthetic", "owner", "tenant"),
        }
        with patch.dict("os.environ", {"RUNTIME_INTERNAL_SERVICE_TOKEN": "synthetic"}):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                denied = await client.get(root + "/removal-recovery")
                self.assertEqual(denied.status_code, 401)
                response = await client.get(root + "/removal-recovery", headers=headers)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.headers["cache-control"], "private, no-store")
                plan = response.json()["data"]
                body = {key: plan[key] for key in ("operation_id", "expected_revision", "digest")}
                body["confirmation"] = "RECOVER REMOVAL " + plan["digest"]
                rejected = await client.post(
                    root + "/remove-agent/recover",
                    json={**body, "profile_path": "/other"},
                    headers=headers,
                )
                self.assertEqual(rejected.status_code, 422)
                completed = await client.post(
                    root + "/remove-agent/recover", json=body, headers=headers
                )
                self.assertEqual(completed.status_code, 200, completed.text)
                replay = await client.post(
                    root + "/remove-agent/recover", json=body, headers=headers
                )
                self.assertEqual(replay.json()["data"], completed.json()["data"])
                stored = await client.get(root, headers=headers)
                self.assertEqual(stored.json()["data"]["status"], "archived")
                self.assertIn(
                    root.replace("research", "{agent_id}") + "/remove-agent/recover",
                    app.openapi()["paths"],
                )
