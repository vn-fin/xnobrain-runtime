"""Cross-process lifecycle drills using real kernel locks, SQLite and run services."""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from xnobrain.models.custom_page import PreparePage
from xnobrain.repositories import FileRepository, StoreError
from xnobrain.repositories.custom_page import CustomPageRepository
from xnobrain.repositories.custom_page_locks import (
    ConversationLease,
    ExecutionLease,
    execution_active,
    execution_name,
    lifecycle_gate,
)
from xnobrain.services.base import ServiceError
from xnobrain.services.conversation_runs import ConversationRunService
from xnobrain.services.custom_page import CustomPageService
from xnobrain.tests.test_conversation_runs import FakeAgents, FakeAnalytics
from xnobrain.tests.test_custom_page import news_manifest
from xnobrain.trusted_context import TrustedRequestContext


def hold_lock(root, mode, connection):
    """New spawned interpreter: no inherited Python lock or active-run dict."""
    root = Path(root)
    files = FileRepository(root, root / "profiles")
    if mode == "storage":
        with CustomPageRepository(files).lock():
            connection.send("locked")
            connection.recv()
    elif mode == "lifecycle":
        with lifecycle_gate(root, "research"):
            connection.send("locked")
            connection.recv()
    else:
        lease = ExecutionLease(root, "research")
        try:
            connection.send("locked")
            connection.recv()
        finally:
            lease.close()
    connection.send("released")
    connection.close()


def prepare_in_process(root, connection):
    files = FileRepository(root, Path(root) / "profiles")
    pages = CustomPageRepository(files)
    connection.send("ready")
    connection.recv()
    body = PreparePage.model_validate(
        {"manifest": news_manifest(), "expected_revision": 0, "idempotency_key": "process-race"}
    ).model_dump()
    try:
        result = pages.prepare("research", "tenant\0owner", body)
        connection.send(("ok", result["revision"]))
    except StoreError as error:
        connection.send(("error", error.code))
    connection.close()


def hold_uncommitted_write(root, connection):
    files = FileRepository(root, Path(root) / "profiles")
    with CustomPageRepository(files).database("research", "tenant\0owner", write=True) as db:
        db.execute(
            "INSERT INTO records VALUES ('articles','uncommitted',?,?,'2026-09-13T00:00:00Z')",
            (
                '{"title":"uncommitted"}',
                '{"kind":"source","source":"synthetic test","run_id":"","collected_at":"2026-09-13T00:00:00Z"}',
            ),
        )
        connection.send("uncommitted")
        connection.recv()


def hold_run(root, connection):
    async def execute():
        files = FileRepository(root, Path(root) / "profiles")
        agents = FakeAgents()
        runs = ConversationRunService(files, agents, FakeAnalytics())
        record = await runs.start_run(
            "research", "session", {"input": "Synthetic", "idempotency_key": "foreign-run"}
        )
        await asyncio.sleep(0)
        connection.send(record)
        await asyncio.to_thread(connection.recv)
        agents.release.set()
        await runs._active[record["id"]].task
        await asyncio.sleep(0)
        connection.send("completed")
        await runs.shutdown()

    asyncio.run(execute())
    connection.close()


class ProcessFixture:
    def setup_files(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.files = FileRepository(self.root, self.root / "profiles")
        (self.root / "profiles" / "research").mkdir()
        self.children = []
        self.connections = []
        self.owner = TrustedRequestContext("owner", "tenant")
        self.pages = CustomPageService(
            SimpleNamespace(repository=self.files, conversation_runs=SimpleNamespace(_active={}))
        )
        draft = self.pages.prepare(
            "research",
            {
                "manifest": news_manifest(),
                "expected_revision": 0,
                "idempotency_key": "initial-draft",
            },
            self.owner,
        )
        self.pages.activate(
            "research",
            {
                "revision": 1,
                "expected_revision": 0,
                "digest": draft["digest"],
                "confirmation": "ACTIVATE " + draft["digest"],
            },
            self.owner,
        )

    def child(self, function, *args):
        ctx = multiprocessing.get_context("spawn")
        parent, child = ctx.Pipe()
        worker = ctx.Process(target=function, args=(str(self.root), *args, child))
        worker.start()
        child.close()
        self.children.append(worker)
        self.connections.append(parent)
        return worker, parent

    def receive(self, connection):
        self.assertTrue(connection.poll(10), "Child did not reach expected synchronization point")
        return connection.recv()

    def cleanup_files(self):
        for worker in self.children:
            if worker.is_alive():
                worker.terminate()
            worker.join(5)
            if worker.is_alive():
                worker.kill()
                worker.join(5)
        for connection in self.connections:
            connection.close()
        self.temp.cleanup()


class CrossProcessPageTests(ProcessFixture, unittest.TestCase):
    def setUp(self):
        self.setup_files()

    def tearDown(self):
        self.cleanup_files()

    def test_foreign_execution_blocks_archive_migration_delete_and_releases_on_exit(self):
        worker, channel = self.child(hold_lock, "execution")
        self.assertEqual(self.receive(channel), "locked")
        manifest = news_manifest()
        manifest["datasets"][0]["fields"].pop()  # Optional score field, unused in queries.
        draft = self.pages.prepare(
            "research",
            {"manifest": manifest, "expected_revision": 1, "idempotency_key": "drop-score"},
            self.owner,
        )
        plan = self.pages.migration_plan("research", draft["revision"], self.owner)
        body = {key: value for key, value in plan.items() if key != "affected_records"}
        body["confirmation"] = "MIGRATE " + plan["plan_digest"]
        for operation in [
            lambda: self.pages.archive(
                "research", {"expected_revision": 1, "confirmation": "ARCHIVE research"}, self.owner
            ),
            lambda: self.pages.migrate("research", body, self.owner),
            lambda: self.pages.delete(
                "research", {"expected_revision": 1, "confirmation": "DELETE research"}, self.owner
            ),
        ]:
            with self.assertRaises(StoreError) as caught:
                operation()
            self.assertEqual(caught.exception.code, "custom_page_jobs_active")
        self.assertEqual(self.pages.read("research", self.owner)["active"], 1)
        # Kernel release on actual process death, not a stale timer/PID assumption.
        worker.terminate()
        worker.join(5)
        self.pages.archive(
            "research", {"expected_revision": 1, "confirmation": "ARCHIVE research"}, self.owner
        )
        self.assertEqual(self.pages.read("research", self.owner)["status"], "archived")

    def test_storage_contention_is_bounded_and_does_not_open_partial_backup_or_delete(self):
        worker, channel = self.child(hold_lock, "storage")
        self.assertEqual(self.receive(channel), "locked")
        started = time.monotonic()
        with self.assertRaises(StoreError) as caught:
            self.pages.backup("research", self.owner)
        self.assertEqual(caught.exception.code, "custom_page_storage_busy")
        self.assertLess(time.monotonic() - started, 3)
        self.assertFalse((self.root / "agent-apps/research/backup.sqlite3").exists())
        channel.send("release")
        self.assertEqual(self.receive(channel), "released")
        worker.join(5)
        backup = self.pages.backup("research", self.owner)
        self.assertGreater(backup["bytes"], 0)
        connection = sqlite3.connect(self.root / "agent-apps/research/backup.sqlite3")
        try:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            connection.close()

    def test_two_processes_create_one_schema_and_replay_the_same_receipt(self):
        workers = [self.child(prepare_in_process) for _ in range(2)]
        # Fresh profile/app so expected_revision=0 is meaningful.
        for name in ("app.sqlite3", "app.sqlite3-wal", "app.sqlite3-shm"):
            (self.root / "agent-apps/research" / name).unlink(missing_ok=True)
        for _, channel in workers:
            self.assertEqual(self.receive(channel), "ready")
        for _, channel in workers:
            channel.send("go")
        for worker, channel in workers:
            self.assertEqual(self.receive(channel), ("ok", 1))
            worker.join(5)
            self.assertEqual(worker.exitcode, 0)
        self.assertEqual(len(self.pages.read("research", self.owner)["revisions"]), 1)

    def test_process_death_rolls_back_an_uncommitted_write_before_backup(self):
        worker, channel = self.child(hold_uncommitted_write)
        self.assertEqual(self.receive(channel), "uncommitted")
        worker.terminate()
        worker.join(5)
        self.assertIsNotNone(worker.exitcode)
        result = self.pages.query("research", "count", {"expected_revision": 1}, self.owner)
        self.assertEqual(result["rows"], [{"count": 0}])
        self.assertGreater(self.pages.backup("research", self.owner)["bytes"], 0)

    def test_lifecycle_is_reentrant_but_separate_shared_leases_conflict(self):
        with lifecycle_gate(self.root, "research"), lifecycle_gate(self.root, "research"):
            with self.assertRaises(StoreError):
                ExecutionLease(self.root, "research")
        first = ExecutionLease(self.root, "research")
        second = ExecutionLease(self.root, "research")
        first.close()
        self.assertTrue(execution_active(self.root, "research"))
        second.close()
        self.assertFalse(execution_active(self.root, "research"))
        conversation = ConversationLease(self.root, "research", "one")
        try:
            with self.assertRaises(StoreError):
                ConversationLease(self.root, "research", "one")
        finally:
            conversation.close()

    def test_rejects_symlink_and_hardlinked_lock_files_and_does_not_leak_descriptors(self):
        path = self.root / execution_name("research")
        target = self.root / "foreign"
        target.write_text("not a lock", encoding="utf-8")
        path.unlink(missing_ok=True)
        path.symlink_to(target)
        before = len(os.listdir("/proc/self/fd"))
        for _ in range(10):
            with self.assertRaises(StoreError):
                ExecutionLease(self.root, "research")
        self.assertEqual(len(os.listdir("/proc/self/fd")), before)
        path.unlink()
        os.link(target, path)
        with self.assertRaises(StoreError):
            ExecutionLease(self.root, "research")
        self.assertEqual(target.read_text(), "not a lock")


class RunLifecycleTests(ProcessFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.setup_files()
        self.agents = FakeAgents()
        self.runs = ConversationRunService(self.files, self.agents, FakeAnalytics())

    async def asyncTearDown(self):
        await self.runs.shutdown()
        self.cleanup_files()

    async def test_foreign_active_run_is_not_healed_or_cancelled_by_another_process(self):
        worker, channel = self.child(hold_run)
        record = await asyncio.to_thread(self.receive, channel)
        observed = self.runs.get_run("research", "session", record["id"])
        self.assertEqual(observed["status"], "running")
        with self.assertRaises(ServiceError) as caught:
            await self.runs.cancel_run("research", "session", record["id"])
        self.assertEqual(caught.exception.code, "run_executor_unavailable")
        with self.assertRaises(ServiceError):
            await self.runs.start_run("research", "session", {"input": "Duplicate work"})
        self.assertFalse(self.agents.received)
        channel.send("finish")
        self.assertEqual(await asyncio.to_thread(self.receive, channel), "completed")
        worker.join(5)
        self.assertEqual(
            self.runs.get_run("research", "session", record["id"])["status"], "completed"
        )

    async def test_actual_process_death_allows_repair_without_replaying_execution(self):
        worker, channel = self.child(hold_run)
        record = await asyncio.to_thread(self.receive, channel)
        self.assertEqual(
            self.runs.get_run("research", "session", record["id"])["status"], "running"
        )
        worker.terminate()
        await asyncio.to_thread(worker.join, 5)
        self.assertFalse(worker.is_alive())
        observed = self.runs.get_run("research", "session", record["id"])
        self.assertEqual(observed["status"], "failed")
        replay = await self.runs.start_run(
            "research", "session", {"input": "Synthetic", "idempotency_key": "foreign-run"}
        )
        self.assertEqual(replay["id"], record["id"])
        self.assertEqual(replay["status"], "failed")
        self.assertFalse(self.agents.received)

    async def test_budget_wait_then_removed_profile_cannot_recreate_run_files(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def budget(_agent):
            entered.set()
            await release.wait()

        self.runs.analytics.require_execution_budget = budget
        starting = asyncio.create_task(
            self.runs.start_run("research", "session", {"input": "Synthetic"})
        )
        await entered.wait()
        with lifecycle_gate(self.root, "research"):
            self.files.hard_delete_profile("research")
        release.set()
        with self.assertRaises(ServiceError) as caught:
            await starting
        self.assertEqual(caught.exception.code, "agent_not_found")
        self.assertFalse(self.files.profile_path("research").exists())
        self.assertFalse(execution_active(self.root, "research"))

    async def test_late_executor_lease_blocks_lifecycle_even_after_run_cancellation(self):
        record = await self.runs.start_run("research", "session", {"input": "Synthetic"})
        executor = ExecutionLease(self.root, "research")
        try:
            await asyncio.sleep(0)
            await self.runs.cancel_run("research", "session", record["id"])
            await asyncio.sleep(0)
            self.assertFalse(self.runs._active)
            with self.assertRaisesRegex(StoreError, "jobs active"):
                self.pages.archive(
                    "research",
                    {"expected_revision": 1, "confirmation": "ARCHIVE research"},
                    self.owner,
                )
        finally:
            executor.close()
        self.pages.archive(
            "research", {"expected_revision": 1, "confirmation": "ARCHIVE research"}, self.owner
        )


class NativeThreadLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_scoped_adapter_keeps_gate_until_cancelled_executor_thread_exits(self):
        from xnobrain.integrations.hermes import AgentManager

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("os.environ", {"RUNTIME_ACCOUNTING_MODE": "legacy"}),
        ):
            root = Path(directory)
            manager = AgentManager(
                root_profile=root / "root",
                profiles_root=root / "profiles",
                legacy_agents_root=root / "legacy",
            )
            manager.create_agent({"name": "news"})
            conversation = manager.create_conversation("news", {"title": "Synthetic"})[
                "conversation"
            ]["id"]
            files = FileRepository(root / "data", root / "profiles")
            pages = CustomPageService(SimpleNamespace(repository=files))
            manager.custom_page_service = pages
            entered, released = threading.Event(), threading.Event()
            worker_done = threading.Event()
            probe = []

            class FakeAdapter:
                def __init__(self, _config):
                    self._session_db = None

                async def _conversation_history_for_session(self, _session):
                    return []

                async def _run_agent(self, **_kwargs):
                    def run():
                        try:
                            with self._profile_scope("news"):
                                probe.append(execution_active(files.data_dir, "news"))
                                entered.set()
                                released.wait(10)
                        finally:
                            worker_done.set()
                        return {"final_response": "synthetic", "messages": []}, {}

                    return await asyncio.to_thread(run)

            prepared = manager._prepare_chat_command(
                "news",
                {"message": "Synthetic", "conversation_id": conversation},
                require_conversation=True,
            )
            with patch("gateway.platforms.api_server.APIServerAdapter", FakeAdapter):
                task = asyncio.create_task(
                    manager._run_session_agent(
                        prepared,
                        run_id="run_" + "a" * 32,
                        stream_delta_callback=lambda *_: None,
                        tool_progress_callback=lambda *_a, **_k: None,
                        approval_notify_callback=lambda *_: None,
                        agent_ref=[None],
                    )
                )
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                    self.assertEqual(probe, [True])
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    self.assertTrue(execution_active(files.data_dir, "news"))
                    with self.assertRaises(StoreError):
                        with lifecycle_gate(files.data_dir, "news"):
                            self.fail("cancelled thread was incorrectly considered stopped")
                finally:
                    released.set()
                    await asyncio.to_thread(worker_done.wait, 5)
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                self.assertFalse(execution_active(files.data_dir, "news"))
