"""FT0015 data/execution must participate in the existing forward-only updater."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import threading
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from xnobrain.integrations.runtime_update_storage import RuntimeUpdateStorageError
from xnobrain.repositories import FileRepository, StoreError
from xnobrain.repositories.custom_page_locks import ExecutionLease
from xnobrain.repositories.runtime_update_gate import (
    WorkspaceActivity,
    checkpoint_gate,
    maintenance,
    update_operation,
)
from xnobrain.services.base import ServiceError
from xnobrain.services.custom_page import CustomPageService
from xnobrain.services.runtime_updates import RuntimeUpdateService
from xnobrain.tests.test_custom_page_locks import (
    ProcessFixture,
    hold_lock,
    hold_uncommitted_write,
)
from xnobrain.tests.test_runtime_updates import TARGET, FakeConnector, request
from xnobrain.trusted_context import TrustedRequestContext


def attempt_while_drained(root, channel):
    """A fresh process sees the persisted fence without a Platform update service."""
    files = FileRepository(root, Path(root) / "profiles")
    pages = CustomPageService(
        SimpleNamespace(repository=files, conversation_runs=SimpleNamespace(_active={}))
    )
    owner = TrustedRequestContext("owner", "tenant")
    results = []
    for attempt in (
        lambda: pages.backup("research", owner),
        lambda: ExecutionLease(files.data_dir, "research"),
        lambda: pages.archive(
            "research", {"expected_revision": 1, "confirmation": "ARCHIVE research"}, owner
        ),
    ):
        try:
            attempt()
            results.append("unexpected-success")
        except StoreError as error:
            results.append(error.code)
    results.append(pages.read("research", owner)["active"])
    channel.send(results)
    channel.close()


def hold_checkpoint(root, channel):
    with checkpoint_gate(Path(root)):
        channel.send("checkpoint-held")
        channel.recv()
    channel.send("released")
    channel.close()


class CustomPageUpdateTests(ProcessFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.setup_files()
        self.platform = SimpleNamespace(
            repository=self.files,
            config=SimpleNamespace(root_profile=self.root / "root-profile"),
            organization_connector=FakeConnector(),
            conversation_runs=SimpleNamespace(_active={}, shutdown=AsyncMock()),
            team_runs=SimpleNamespace(_active={}, shutdown=AsyncMock()),
            kanban=SimpleNamespace(
                active_agent_ids=Mock(return_value=set()), cancel_active_tasks_for_update=Mock()
            ),
            cron=SimpleNamespace(active_execution_count=0),
        )
        self.environment = patch.dict(
            "os.environ",
            {
                "RUNTIME_UPDATE_SERVICE_TOKEN": "synthetic-update-token",
                "XNOBRAIN_VERSION": TARGET["version"],
                "XNOBRAIN_SOURCE_COMMIT": TARGET["source_commit"],
                "XNOBRAIN_RUNTIME_DIGEST": TARGET["runtime_digest"],
                "RUNTIME_DATA_SCHEMA": "1",
            },
        )
        self.environment.start()
        self.updates = RuntimeUpdateService(self.platform)

    async def asyncTearDown(self):
        self.environment.stop()
        self.cleanup_files()

    async def call(self, method, **body):
        return await getattr(self.updates, method)(
            request(**body), update_token="synthetic-update-token"
        )

    async def test_foreign_executor_blocks_drain_even_when_cancellation_clears_local_registry(self):
        worker, channel = self.child(hold_lock, "execution")
        self.assertEqual(await asyncio.to_thread(self.receive, channel), "locked")
        with self.assertRaises(ServiceError) as caught:
            await self.call("drain", deadline_seconds=0, cancel_active_at_deadline=True)
        self.assertEqual(caught.exception.code, "runtime_update_drain_timeout")
        self.platform.conversation_runs.shutdown.assert_awaited_once()
        self.assertTrue(self.updates.dispatch_paused)
        self.assertEqual(self.updates._active_counts()["workspace_activity"], 1)
        with self.assertRaises(ServiceError) as blocked:
            await self.call("checkpoint")
        self.assertEqual(blocked.exception.code, "runtime_update_not_drained")
        channel.send("release")
        self.assertEqual(await asyncio.to_thread(self.receive, channel), "released")
        await asyncio.to_thread(worker.join, 5)
        drained = await self.call("drain", deadline_seconds=0)
        self.assertEqual(drained["active"]["workspace_activity"], 0)
        self.assertTrue(drained["drained"])

    async def test_drain_waits_for_transaction_commit_without_claiming_cancellation(self):
        worker, channel = self.child(hold_uncommitted_write)
        self.assertEqual(await asyncio.to_thread(self.receive, channel), "uncommitted")
        draining = asyncio.create_task(self.call("drain", deadline_seconds=5))
        try:
            for _ in range(20):
                if maintenance(self.root).get("dispatch_paused"):
                    break
                await asyncio.sleep(0.01)
            self.assertFalse(draining.done())
            self.assertTrue(maintenance(self.root)["dispatch_paused"])
            channel.send("commit")
            await asyncio.to_thread(worker.join, 5)
            self.assertEqual(worker.exitcode, 0)
            result = await asyncio.wait_for(draining, 5)
            self.assertFalse(result["cancelled_at_deadline"])
            checkpoint = await self.call("checkpoint")
            self.assertIn("data/agent-apps/research/app.sqlite3", checkpoint["sqlite_checkpoints"])
            exported = self.pages.export("research", self.owner)
            self.assertEqual(exported["records"][0]["id"], "uncommitted")
        finally:
            if not draining.done():
                draining.cancel()
                await asyncio.gather(draining, return_exceptions=True)

    async def test_new_process_mutations_and_cached_update_service_observe_persisted_fence(self):
        other = RuntimeUpdateService(self.platform)
        self.assertFalse(other.dispatch_paused)
        await self.call("drain", deadline_seconds=0)
        self.assertTrue(other.dispatch_paused)
        with self.assertRaises(ServiceError):
            other.require_dispatch()
        worker, channel = self.child(attempt_while_drained)
        self.assertEqual(
            await asyncio.to_thread(self.receive, channel),
            [
                "runtime_update_maintenance",
                "runtime_update_maintenance",
                "runtime_update_maintenance",
                1,
            ],
        )
        await asyncio.to_thread(worker.join, 5)
        self.assertEqual(worker.exitcode, 0)
        checkpoint = await self.call("checkpoint")
        verified = await self.call("post_verify", checkpoint_id=checkpoint["checkpoint_id"])
        self.assertTrue(verified["verified"])
        await self.call("resume")
        self.assertFalse(other.dispatch_paused)
        lease = ExecutionLease(self.root, "research")
        lease.close()

    async def test_actual_thread_is_counted_after_awaiting_coroutine_cancels(self):
        entered, leave, done = threading.Event(), threading.Event(), threading.Event()

        def execute():
            lease = ExecutionLease(self.root, "research")
            try:
                entered.set()
                leave.wait(10)
            finally:
                lease.close()
                done.set()

        task = asyncio.create_task(asyncio.to_thread(execute))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 5))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            with self.assertRaises(ServiceError) as caught:
                await self.call("drain", deadline_seconds=0, cancel_active_at_deadline=True)
            self.assertEqual(caught.exception.code, "runtime_update_drain_timeout")
        finally:
            leave.set()
            self.assertTrue(await asyncio.to_thread(done.wait, 5))
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertTrue((await self.call("drain", deadline_seconds=0))["drained"])

    async def test_checkpoint_excludes_only_valid_content_free_locks_and_preserves_app_backup(self):
        self.pages.backup("research", self.owner)
        await self.call("drain", deadline_seconds=0)
        checkpoint = await self.call("checkpoint")
        paths = {item["path"] for item in checkpoint["manifest"]["entries"]}
        self.assertIn("data/agent-apps/research/app.sqlite3", paths)
        self.assertIn("data/agent-apps/research/backup.sqlite3", paths)
        self.assertFalse(any(path.endswith(".lock") for path in paths))
        # A new harmless coordination inode after replacement isn't data drift.
        (self.root / (".custom-page-conversation-" + "a" * 64 + ".lock")).touch()
        (self.root / (".custom-page-schedule-" + "b" * 64 + ".lock")).touch()
        fresh = RuntimeUpdateService(self.platform)
        result = await fresh.post_verify(
            request(checkpoint_id=checkpoint["checkpoint_id"]),
            update_token="synthetic-update-token",
        )
        self.assertTrue(result["data_preserved"])
        # A reserved name cannot hide arbitrary file contents from the manifest.
        for name in (".custom-page-storage.lock", ".custom-page-schedule-" + "b" * 64 + ".lock"):
            with self.subTest(coordination=name):
                path = self.root / name
                path.write_text("must not be excluded", encoding="utf-8")
                try:
                    with self.assertRaises(RuntimeUpdateStorageError) as unsafe:
                        self.updates.storage.manifest()
                    self.assertEqual(unsafe.exception.code, "runtime_update_unsafe_data")
                finally:
                    path.write_text("", encoding="utf-8")

    async def test_checkpoint_excludes_foreign_readers_and_serializes_other_update_commands(self):
        await self.call("drain", deadline_seconds=0)
        worker, channel = self.child(hold_checkpoint)
        self.assertEqual(await asyncio.to_thread(self.receive, channel), "checkpoint-held")
        with self.assertRaises(StoreError):
            self.pages.read("research", self.owner)
        with self.assertRaises(ServiceError):
            await self.call("checkpoint")
        channel.send("release")
        self.assertEqual(await asyncio.to_thread(self.receive, channel), "released")
        await asyncio.to_thread(worker.join, 5)
        with update_operation(self.root):
            with self.assertRaises(ServiceError) as held:
                await RuntimeUpdateService(self.platform).checkpoint(
                    request(), update_token="synthetic-update-token"
                )
            self.assertEqual(held.exception.code, "runtime_update_fence_conflict")
        self.assertTrue((await self.call("checkpoint"))["checkpoint_id"])

    async def test_corrupt_or_unsafe_maintenance_cannot_enable_new_admission(self):
        path = self.updates.repository.maintenance_path
        for raw in ["{}", "[]", "{", '{"dispatch_paused":"false"}', '{"dispatch_paused":true}']:
            path.write_text(raw, encoding="utf-8")
            with self.assertRaises(StoreError):
                ExecutionLease(self.root, "research")
            with self.assertRaises(StoreError):
                _ = self.updates.dispatch_paused
        path.unlink()
        path.symlink_to(self.root / "absent.json")
        with self.assertRaises(StoreError):
            ExecutionLease(self.root, "research")
        path.unlink()
        foreign = self.root / "foreign.json"
        foreign.write_text('{"dispatch_paused":false}', encoding="utf-8")
        os.link(foreign, path)
        with self.assertRaises(StoreError):
            ExecutionLease(self.root, "research")
        self.assertEqual(foreign.read_text(), '{"dispatch_paused":false}')

    async def test_fifo_duplicate_and_oversized_maintenance_are_not_admission(self):
        path = self.updates.repository.maintenance_path
        for contents in [
            '{"dispatch_paused":true,"dispatch_paused":false}',
            '{"dispatch_paused":false,"extra":"' + "x" * 16384 + '"}',
        ]:
            path.write_text(contents, encoding="utf-8")
            with self.assertRaises(StoreError):
                ExecutionLease(self.root, "research")
        path.unlink()
        os.mkfifo(path)
        with self.assertRaises(StoreError) as caught:
            # O_NONBLOCK permits rejecting the special file without hanging.
            ExecutionLease(self.root, "research")
        self.assertEqual(caught.exception.code, "runtime_update_journal_invalid")

    async def test_uncooperative_sqlite_reader_busy_checkpoint_is_not_reported_flushed(self):
        database = self.root / "busy.sqlite3"
        with (
            closing(sqlite3.connect(database)) as writer,
            closing(sqlite3.connect(database)) as reader,
        ):
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("CREATE TABLE rows(value TEXT)")
            writer.execute("INSERT INTO rows VALUES ('before')")
            writer.commit()
            reader.execute("BEGIN")
            reader.execute("SELECT * FROM rows").fetchall()
            writer.execute("INSERT INTO rows VALUES ('after')")
            writer.commit()
            with self.assertRaises(RuntimeUpdateStorageError) as busy:
                await asyncio.to_thread(self.updates.storage.flush_sqlite)
            self.assertEqual(busy.exception.code, "runtime_update_sqlite_checkpoint_failed")
            reader.rollback()
            self.assertIn("data/busy.sqlite3", self.updates.storage.flush_sqlite())

    async def test_existing_activity_cannot_acquire_new_mutation_after_drain(self):
        with WorkspaceActivity(self.root):
            with self.assertRaises(ServiceError):
                await self.call("drain", deadline_seconds=0)
            with self.assertRaises(StoreError) as blocked:
                self.pages.backup("research", self.owner)
            self.assertEqual(blocked.exception.code, "runtime_update_maintenance")
        self.assertTrue((await self.call("drain", deadline_seconds=0))["drained"])

    async def test_cancelled_update_observer_cannot_release_command_before_journal_thread(self):
        started, finish = threading.Event(), threading.Event()
        save = self.updates.repository.save_maintenance

        def delayed(value):
            started.set()
            finish.wait(10)
            return save(value)

        with patch.object(self.updates.repository, "save_maintenance", side_effect=delayed):
            draining = asyncio.create_task(self.call("drain", deadline_seconds=0))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 5))
                draining.cancel()
                await asyncio.sleep(0)
                self.assertFalse(draining.done())
                with self.assertRaises(ServiceError) as blocked:
                    await RuntimeUpdateService(self.platform).drain(
                        request(deadline_seconds=0), update_token="synthetic-update-token"
                    )
                self.assertEqual(blocked.exception.code, "runtime_update_fence_conflict")
            finally:
                finish.set()
                with self.assertRaises(asyncio.CancelledError):
                    await draining
        self.assertTrue(self.updates.dispatch_paused)
        self.assertTrue((await self.call("drain", deadline_seconds=0))["drained"])

    async def test_ui_assistance_staging_obeys_the_same_maintenance_fence(self):
        from xnobrain.repositories.ui_composition import UICompositionRepository

        staged = UICompositionRepository(self.files)
        staged.cancel("tenant\0owner", "uia-before")
        await self.call("drain", deadline_seconds=0)
        with self.assertRaises(StoreError) as blocked:
            staged.cancel("tenant\0owner", "uia-after")
        self.assertEqual(blocked.exception.code, "runtime_update_maintenance")
        self.assertTrue(staged.get("tenant\0owner", "uia-before")["cancelled"])
        checkpoint = await self.call("checkpoint")
        self.assertTrue(
            any("ui-assistance/" in row["path"] for row in checkpoint["manifest"]["entries"])
        )
        self.assertTrue(
            (await self.call("post_verify", checkpoint_id=checkpoint["checkpoint_id"]))["verified"]
        )

    async def test_checkpoint_worker_keeps_exclusivity_after_observer_cancellation(self):
        await self.call("drain", deadline_seconds=0)
        started, finish = threading.Event(), threading.Event()
        original = self.updates.storage.manifest

        def slow_manifest():
            started.set()
            finish.wait(10)
            return original()

        with patch.object(self.updates.storage, "manifest", side_effect=slow_manifest):
            observing = asyncio.create_task(self.call("checkpoint"))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 5))
                observing.cancel()
                await asyncio.sleep(0)
                self.assertFalse(observing.done())
                with self.assertRaises(StoreError):
                    self.pages.read("research", self.owner)
                with self.assertRaises(ServiceError):
                    await RuntimeUpdateService(self.platform).checkpoint(
                        request(), update_token="synthetic-update-token"
                    )
            finally:
                finish.set()
                with self.assertRaises(asyncio.CancelledError):
                    await observing
        # Abandoned observation produced no completed checkpoint receipt.
        self.assertNotIn("checkpoint", self.updates.repository.operation("upd_fixture")["steps"])
        self.assertTrue((await self.call("checkpoint"))["checkpoint_id"])

    async def test_reading_abandoned_run_during_maintenance_does_not_change_manifest(self):
        from xnobrain.services.conversation_runs import ConversationRunService
        from xnobrain.tests.test_conversation_runs import FakeAgents

        run = {
            "id": "run_" + "c" * 32,
            "agent_id": "research",
            "conversation_id": "session",
            "status": "running",
        }
        self.files.put_conversation_run(run)
        await self.call("drain", deadline_seconds=0)
        checkpoint = await self.call("checkpoint")
        runs = ConversationRunService(self.files, FakeAgents())
        self.assertEqual(runs.get_run("research", "session", run["id"])["status"], "running")
        self.assertTrue(
            (await self.call("post_verify", checkpoint_id=checkpoint["checkpoint_id"]))["verified"]
        )
        await self.call("resume")
        self.assertEqual(runs.get_run("research", "session", run["id"])["status"], "failed")
