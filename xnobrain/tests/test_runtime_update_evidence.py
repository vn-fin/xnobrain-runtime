"""Regression evidence for source restart without weakening durable data checks."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from xnobrain.integrations.runtime_update_storage import (
    RuntimeUpdateStorage,
    RuntimeUpdateStorageError,
)
from xnobrain.repositories.runtime_update_gate import activity_present
from xnobrain.services.runtime_shutdown import shutdown_stage
from xnobrain.services.runtime_update_startup import finish_startup


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        anchor = Path(temporary.name).resolve()
        self.data, self.profiles, self.root = (
            anchor / name for name in ("data", "profiles", "root")
        )
        for path in (self.data, self.profiles, self.root):
            path.mkdir()
        env = patch.dict(os.environ, {"RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": ""})
        env.start()
        self.addCleanup(env.stop)
        self.storage = RuntimeUpdateStorage(self.data, self.profiles, self.root)

    def database(self, *, root=False):
        path = (self.root if root else self.data) / "state.db"
        connection = sqlite3.connect(path)
        self.addCleanup(connection.close)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, content BLOB)")
        connection.execute("INSERT INTO messages VALUES (1, ?)", (b"private\x00data",))
        connection.commit()
        return path, connection

    def test_wal_commit_is_verified_not_ignored(self):
        _, connection = self.database()
        before = self.storage.manifest()
        connection.execute("UPDATE messages SET content='changed' WHERE id=1")
        connection.commit()
        self.assertFalse(self.storage.verify(before)["preserved"])
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.assertFalse(self.storage.verify(before)["preserved"])

    def test_checkpoint_vacuum_and_shm_churn_preserve_logical_data(self):
        path, connection = self.database()
        before = self.storage.manifest()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("VACUUM")
        self.assertTrue(self.storage.verify(before)["preserved"])
        connection.close()
        self.assertFalse(Path(str(path) + "-shm").exists())
        self.assertTrue(self.storage.verify(before)["preserved"])

    def test_schema_and_metadata_changes_fail(self):
        _, connection = self.database()
        before = self.storage.manifest()
        connection.execute("PRAGMA user_version=2")
        self.assertFalse(self.storage.verify(before)["preserved"])
        connection.execute("PRAGMA user_version=0")
        connection.execute("CREATE TABLE unexpected(value TEXT)")
        self.assertFalse(self.storage.verify(before)["preserved"])

    def test_reviewed_fts_marker_only_and_only_in_hermes_state(self):
        _, connection = self.database(root=True)
        connection.execute("CREATE TABLE state_meta(key TEXT PRIMARY KEY, value TEXT)")
        connection.commit()
        before = self.storage.manifest()
        connection.execute("INSERT INTO state_meta VALUES ('fts_storage_version','1')")
        connection.commit()
        self.assertTrue(self.storage.verify(before)["preserved"])
        after = self.storage.manifest()
        connection.execute("UPDATE state_meta SET value='2'")
        connection.commit()
        self.assertFalse(self.storage.verify(before)["preserved"])
        self.assertFalse(self.storage.verify(after)["preserved"])
        connection.execute("DELETE FROM state_meta")
        connection.commit()
        self.assertFalse(self.storage.verify(after)["preserved"])

    def test_arbitrary_database_metadata_is_not_migration(self):
        _, connection = self.database()
        connection.execute("CREATE TABLE state_meta(key TEXT PRIMARY KEY, value TEXT)")
        connection.commit()
        before = self.storage.manifest()
        connection.execute("INSERT INTO state_meta VALUES ('fts_storage_version','1')")
        connection.commit()
        self.assertFalse(self.storage.verify(before)["preserved"])

    def test_log_prefix_is_preserved_but_truncation_or_rewrite_fails(self):
        log = self.profiles / "agent" / "logs" / "agent.log"
        log.parent.mkdir(parents=True)
        log.write_bytes(b"old log\n")
        before = self.storage.manifest()
        log.write_bytes(b"old log\nnew boot\n")
        result = self.storage.verify(before)
        self.assertTrue(result["preserved"])
        self.assertEqual(result["changes"]["log_appends"], 1)
        log.write_bytes(b"changed\nnew boot\n")
        self.assertFalse(self.storage.verify(before)["preserved"])
        log.write_bytes(b"old")
        self.assertFalse(self.storage.verify(before)["preserved"])

    def test_new_files_orphan_wal_and_user_logs_are_not_ignored(self):
        path = self.data / "user.log"
        path.write_bytes(b"old")
        before = self.storage.manifest()
        path.write_bytes(b"old new")
        self.assertFalse(self.storage.verify(before)["preserved"])
        path.write_bytes(b"old")
        (self.data / "orphan.db-wal").write_bytes(b"durable")
        self.assertFalse(self.storage.verify(before)["preserved"])

    def test_profile_venv_links_preserved_and_retarget_rejected(self):
        venv = self.profiles / "agent" / "workspace" / ".venv"
        (venv / "bin").mkdir(parents=True)
        (venv / "lib").mkdir()
        (venv / "lib64").symlink_to("lib", target_is_directory=True)
        python = venv / "bin" / "python"
        python.symlink_to("/opt/xnobrain/.tools/uv-python/cpython-3.11/bin/python3.11")
        before = self.storage.manifest()
        self.assertTrue(self.storage.verify(before)["preserved"])
        python.unlink()
        python.symlink_to("/opt/xnobrain/.tools/uv-python/other/bin/python3.11")
        self.assertFalse(self.storage.verify(before)["preserved"])
        python.unlink()
        python.symlink_to("/etc/shadow")
        with self.assertRaises(RuntimeUpdateStorageError):
            self.storage.manifest()

    def test_symlink_cycles_and_cross_profile_links_fail_closed(self):
        profile = self.profiles / "agent"
        profile.mkdir()
        link = profile / "escape"
        link.symlink_to("../other")
        with self.assertRaises(RuntimeUpdateStorageError):
            self.storage.manifest()
        link.unlink()
        link.symlink_to("escape")
        with self.assertRaises(RuntimeUpdateStorageError):
            self.storage.manifest()

    def test_legacy_checkpoints_remain_strict_and_are_never_rebased(self):
        path = self.root / "logs" / "agent.log"
        path.parent.mkdir()
        path.write_bytes(b"old")
        before = self.storage.manifest(version=1)
        self.assertNotIn("version", before)
        self.assertTrue(self.storage.verify(before)["preserved"])
        path.write_bytes(b"old new")
        self.assertFalse(self.storage.verify(before)["preserved"])

    def test_permissions_still_verified_for_database(self):
        path, _ = self.database()
        path.chmod(0o600)
        before = self.storage.manifest()
        path.chmod(0o644)
        self.assertFalse(self.storage.verify(before)["preserved"])

    def test_corrupt_sqlite_and_sidecar_symlink_fail_closed(self):
        path, connection = self.database()
        connection.close()
        baseline = self.storage.manifest()
        sidecar = Path(str(path) + "-wal")
        sidecar.unlink(missing_ok=True)
        sidecar.symlink_to(self.data / "other")
        with self.assertRaises(RuntimeUpdateStorageError):
            self.storage.verify(baseline)
        sidecar.unlink()
        path.write_bytes(b"SQLite format 3\x00" + b"corrupt" * 100)
        with self.assertRaises(RuntimeUpdateStorageError) as raised:
            self.storage.verify(baseline)
        self.assertEqual(raised.exception.code, "runtime_update_sqlite_verify_failed")

    def test_snapshot_includes_same_value_row_duplicates(self):
        _, connection = self.database()
        connection.execute("CREATE TABLE duplicates(value)")
        connection.execute("INSERT INTO duplicates VALUES ('same'), ('same')")
        connection.commit()
        baseline = self.storage.manifest()
        connection.execute("DELETE FROM duplicates WHERE rowid=1")
        connection.commit()
        self.assertFalse(self.storage.verify(baseline)["preserved"])


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_repairs_retain_activity_until_completion(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entered, release = asyncio.Event(), asyncio.Event()

            async def repair():
                entered.set()
                await release.wait()

            platform = SimpleNamespace(
                repository=SimpleNamespace(data_dir=root),
                runtime_updates=SimpleNamespace(dispatch_paused=False),
                ensure_default_agent=repair,
            )
            task = asyncio.create_task(finish_startup(platform))
            try:
                await asyncio.wait_for(entered.wait(), 1)
                self.assertTrue(activity_present(root))
                release.set()
                await asyncio.wait_for(task, 1)
                self.assertFalse(activity_present(root))
            finally:
                task.cancel()

    async def test_shutdown_observer_reports_stage_without_cancelling_data_work(self):
        with self.assertLogs("xnobrain.services.runtime_shutdown", level="WARNING") as logs:
            async with shutdown_stage("portability", warn_after=0.001):
                await asyncio.sleep(0.02)
        self.assertEqual(logs.records[0].shutdown_stage, "portability")
        self.assertGreater(logs.records[0].elapsed_seconds, 0)
        self.assertFalse(
            any(task.get_name() == "xnobrain-shutdown-observer" for task in asyncio.all_tasks())
        )

    async def test_bootstrap_waits_for_verified_release_and_is_cancellable(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        platform = SimpleNamespace(
            repository=SimpleNamespace(data_dir=Path(temporary.name)),
            runtime_updates=SimpleNamespace(dispatch_paused=True),
            config=SimpleNamespace(ensure_write_approval_defaults=Mock()),
            _ensure_existing_write_approval_defaults=Mock(),
            ensure_default_agent=AsyncMock(),
        )
        task = asyncio.create_task(finish_startup(platform, deferred=True))
        await asyncio.sleep(0.01)
        platform.ensure_default_agent.assert_not_awaited()
        platform.config.ensure_write_approval_defaults.assert_not_called()
        platform.runtime_updates.dispatch_paused = False
        await asyncio.wait_for(task, timeout=1)
        platform.ensure_default_agent.assert_awaited_once()
        platform.config.ensure_write_approval_defaults.assert_called_once()
        platform.runtime_updates.dispatch_paused = True
        task = asyncio.create_task(finish_startup(platform, deferred=True))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        platform.ensure_default_agent.assert_awaited_once()
