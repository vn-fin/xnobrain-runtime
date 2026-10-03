"""Persistent admission and fencing tests using independent SQLite connections."""

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from xnobrain.repositories.base import StoreError
from xnobrain.repositories.portability_tasks import PortabilityTaskStore


class PortabilityTaskStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = PortabilityTaskStore(self.root)

    def admit(self, store=None, **overrides):
        values = {
            "scope": "workspace",
            "actor": "actor",
            "kind": "IMPORT",
            "key": "request-1",
            "fingerprint": self.store.digest({"archive": "sha"}),
            "inputs": {"upload_id": "upload"},
            "upload_id": "upload",
        }
        values.update(overrides)
        return (store or self.store).admit(**values)

    def test_concurrent_connections_admit_one_task(self):
        stores = [PortabilityTaskStore(self.root) for _ in range(12)]
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(self.admit, stores))
        self.assertEqual(len({row["id"] for row, _ in results}), 1)
        self.assertEqual(sum(created for _, created in results), 1)
        with self.store.transaction() as db:
            for table in ("portability_tasks", "portability_idempotency", "portability_pins"):
                self.assertEqual(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 1)

    def test_restart_and_terminal_replay_preserve_task(self):
        task, _ = self.admit()
        claimed = self.store.claim("worker")
        self.assertTrue(
            self.store.finish(task["id"], "worker", claimed["fence"], result={"mapped": "one"})
        )
        restarted = PortabilityTaskStore(self.root)
        row, created = self.admit(restarted)
        self.assertFalse(created)
        self.assertEqual(row["id"], task["id"])
        self.assertEqual(row["status"], "COMPLETED")
        self.assertEqual(
            restarted.digest({"archive": "sha"}), self.store.digest({"archive": "sha"})
        )

    def test_changed_payload_conflicts_without_second_task(self):
        self.admit()
        with self.assertRaises(StoreError) as caught:
            self.admit(fingerprint=self.store.digest({"archive": "changed"}))
        self.assertEqual(caught.exception.code, "idempotency_conflict")

    def test_input_pin_conflict_rolls_back_task_and_key(self):
        self.admit()
        with self.assertRaises(StoreError) as caught:
            self.admit(key="another")
        self.assertEqual(caught.exception.code, "transfer_in_use")
        with self.store.transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM portability_tasks").fetchone()[0], 1)
            self.assertEqual(
                db.execute("SELECT count(*) FROM portability_idempotency").fetchone()[0], 1
            )

    def test_stale_worker_cannot_finish_or_renew(self):
        task, _ = self.admit()
        old = self.store.claim("old", lease_seconds=-1)
        new = self.store.claim("new")
        self.assertGreater(new["fence"], old["fence"])
        self.assertFalse(self.store.heartbeat(task["id"], "old", old["fence"]))
        self.assertFalse(self.store.finish(task["id"], "old", old["fence"], result={}))
        self.assertTrue(self.store.finish(task["id"], "new", new["fence"], result={}))

    def test_failed_replay_does_not_requeue(self):
        task, _ = self.admit()
        claim = self.store.claim("worker")
        self.store.finish(task["id"], "worker", claim["fence"], error={"code": "invalid_archive"})
        row, created = self.admit()
        self.assertFalse(created)
        self.assertEqual(row["status"], "FAILED")
        self.assertIsNone(self.store.claim("again"))

    def test_foreign_actor_cannot_read_task(self):
        task, _ = self.admit()
        with self.assertRaises(StoreError) as caught:
            self.store.get(task["id"], scope="workspace", actor="other")
        self.assertEqual(caught.exception.status, 404)

    def test_replay_is_allowed_when_queue_full(self):
        self.store.capacity = 1
        first, _ = self.admit()
        replay, created = self.admit()
        self.assertEqual(first["id"], replay["id"])
        self.assertFalse(created)
        with self.assertRaises(StoreError) as caught:
            self.admit(key="second", upload_id="second")
        self.assertEqual(caught.exception.status, 429)

    def test_missing_key_is_rejected(self):
        with self.assertRaises(StoreError) as caught:
            self.admit(key=None)
        self.assertEqual(caught.exception.code, "idempotency_key_required")

    def test_pagination_and_events_are_owner_scoped(self):
        first, _ = self.admit(upload_id="one")
        second, _ = self.admit(key="two", upload_id="two")
        self.admit(key="foreign", actor="foreign", upload_id="three")
        page = self.store.list_tasks(scope="workspace", actor="actor", limit=1)
        self.assertEqual(page["items"][0]["id"], second["id"])
        remaining = self.store.list_tasks(
            scope="workspace", actor="actor", before=page["next_cursor"], limit=1
        )
        self.assertEqual(remaining["items"][0]["id"], first["id"])
        self.assertIsNone(remaining["next_cursor"])
        events = self.store.events(scope="workspace", actor="actor")
        self.assertEqual({event["id"] for event in events}, {first["id"], second["id"]})
        self.assertEqual(
            self.store.events(scope="workspace", actor="actor", after=events[-1]["event_id"]), []
        )
        with self.assertRaises(StoreError):
            self.store.list_tasks(scope="workspace", actor="foreign", before=first["id"])

    def test_recovery_failure_retains_pin(self):
        task, _ = self.admit()
        claimed = self.store.claim("worker")
        with self.store.publication_lock():
            self.assertTrue(self.store.owns_claim(task["id"], "worker", claimed["fence"]))
            self.store.finish(
                task["id"], "worker", claimed["fence"], error={"code": "import_recovery_required"}
            )
        self.assertTrue(self.store.pinned("upload"))
        self.assertFalse(self.store.owns_claim(task["id"], "worker", claimed["fence"]))

    def test_journal_keeps_mapping_across_takeover(self):
        task, _ = self.admit()
        old = self.store.claim("old")
        row = self.store.reserve_target(
            task["id"], "old", old["fence"], kind="PROFILE", source_id="source", target_id="target"
        )
        self.assertEqual(row["phase"], "RESERVED")
        self.store.record_publication(
            task["id"],
            "old",
            old["fence"],
            kind="PROFILE",
            source_id="source",
            digest="content-digest",
            phase="PREPARED",
        )
        with self.store.transaction() as db:
            db.execute("UPDATE portability_tasks SET lease_until=0 WHERE id=?", (task["id"],))
        new = self.store.claim("new")
        recovered = self.store.reserve_target(
            task["id"],
            "new",
            new["fence"],
            kind="PROFILE",
            source_id="source",
            target_id="different",
        )
        self.assertEqual(recovered["target_id"], "target")
        self.assertEqual(recovered["digest"], "content-digest")
        with self.assertRaises(StoreError):
            self.store.record_publication(
                task["id"],
                "old",
                old["fence"],
                kind="PROFILE",
                source_id="source",
                digest="content-digest",
                phase="PUBLISHED",
            )
        self.store.record_publication(
            task["id"],
            "new",
            new["fence"],
            kind="PROFILE",
            source_id="source",
            digest="content-digest",
            phase="PUBLISHED",
        )
        self.assertEqual(self.store.journal(task["id"])[0]["phase"], "PUBLISHED")
        with self.assertRaises(StoreError):
            self.store.record_publication(
                task["id"],
                "new",
                new["fence"],
                kind="PROFILE",
                source_id="source",
                digest="different-content",
                phase="PUBLISHED",
            )

    def test_logical_commit_exposes_all_reserved_resources_together(self):
        task, _ = self.admit()
        claim = self.store.claim("worker")
        for kind in ("PROFILE", "TEAM"):
            self.store.reserve_target(
                task["id"],
                "worker",
                claim["fence"],
                kind=kind,
                source_id="source",
                target_id="target",
            )
            self.assertFalse(self.store.resource_visible(kind, "target"))
        self.assertTrue(self.store.resource_visible("PROFILE", "unrelated"))
        self.store.finish(task["id"], "worker", claim["fence"], result={})
        for kind in ("PROFILE", "TEAM"):
            self.assertTrue(self.store.resource_visible(kind, "target"))

    def test_independent_workers_respect_workspace_concurrency_limit(self):
        first, _ = self.admit()
        self.admit(key="other", upload_id="other")
        claimed = self.store.claim("first")
        self.assertEqual(claimed["id"], first["id"])
        other = PortabilityTaskStore(self.root)
        self.assertIsNone(other.claim("second"))
        self.store.finish(claimed["id"], "first", claimed["fence"], result={})
        self.assertIsNotNone(other.claim("second"))

    def test_retry_is_bounded_and_prohibited_after_publication_intent(self):
        task, _ = self.admit()
        for attempt in range(1, 4):
            claim = self.store.claim("worker")
            self.assertEqual(claim["attempts"], attempt)
            retried = self.store.retry_claim(task["id"], "worker", claim["fence"], delay_seconds=0)
            self.assertEqual(retried, attempt < 3)
        self.store.reserve_target(
            task["id"],
            "worker",
            claim["fence"],
            kind="PROFILE",
            source_id="source",
            target_id="target",
        )
        self.store.record_publication(
            task["id"],
            "worker",
            claim["fence"],
            kind="PROFILE",
            source_id="source",
            digest="digest",
            phase="PREPARED",
        )
        self.assertFalse(
            self.store.retry_claim(task["id"], "worker", claim["fence"], max_attempts=10)
        )

    def test_reader_lease_survives_store_restart_and_expires(self):
        self.assertFalse(self.store.artifact_reader("export"))
        self.store.artifact_reader("export", renew=True)
        other = PortabilityTaskStore(self.root)
        self.assertTrue(other.artifact_reader("export"))
        with other.transaction() as db:
            db.execute("UPDATE portability_download_leases SET expires_at=0")
        self.assertFalse(self.store.artifact_reader("export"))

    def test_future_schema_is_not_silently_downgraded(self):
        with self.store.transaction() as db:
            db.execute("PRAGMA user_version=2")
        with self.assertRaises(StoreError) as caught:
            PortabilityTaskStore(self.root)
        self.assertEqual(caught.exception.code, "task_schema_incompatible")
        with self.store.transaction() as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)

    def test_admission_lock_contention_is_bounded(self):
        import threading
        import time

        locked = threading.Event()
        release = threading.Event()

        def holder():
            with self.store.publication_lock():
                locked.set()
                release.wait(3)

        worker = threading.Thread(target=holder)
        worker.start()
        try:
            self.assertTrue(locked.wait(1))
            started = time.monotonic()
            with self.assertRaises(StoreError) as caught:
                with self.store.publication_lock():
                    self.fail("Concurrent publication lock granted")
            self.assertEqual(caught.exception.code, "task_storage_busy")
            self.assertLess(time.monotonic() - started, 1)
        finally:
            release.set()
            worker.join(2)

    def test_schema_failure_rolls_back_every_table_and_version(self):
        import sqlite3
        from unittest.mock import patch

        connect = sqlite3.connect

        class FailingConnection(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                if "CREATE TABLE IF NOT EXISTS portability_import_journal" in sql:
                    raise sqlite3.OperationalError("injected schema failure")
                return super().execute(sql, parameters)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch(
                    "xnobrain.repositories.portability_tasks.sqlite3.connect",
                    side_effect=lambda *args, **kwargs: connect(
                        *args, **kwargs, factory=FailingConnection
                    ),
                ),
                self.assertRaises(StoreError),
            ):
                PortabilityTaskStore(root)
            with connect(root / "portability.sqlite3") as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 0)
                self.assertEqual(
                    db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), []
                )
            restarted = PortabilityTaskStore(root)
            self.assertEqual(len(restarted.secret), 32)

    def test_concurrent_first_initialization_uses_one_durable_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with ThreadPoolExecutor(max_workers=8) as pool:
                stores = list(pool.map(lambda _: PortabilityTaskStore(root), range(8)))
            self.assertEqual(len({store.secret for store in stores}), 1)
            self.assertEqual(PortabilityTaskStore(root).secret, stores[0].secret)

    def test_takeover_cannot_change_fence_inside_live_publication(self):
        task, _ = self.admit()
        old = self.store.claim("old", lease_seconds=-1)
        other = PortabilityTaskStore(self.root)
        with self.store.publication_lock():
            with self.assertRaises(StoreError) as busy:
                other.claim("replacement")
            self.assertEqual(busy.exception.code, "task_storage_busy")
            self.assertEqual(
                self.store.get(task["id"], scope="workspace", actor="actor")["fence"], old["fence"]
            )
        replacement = other.claim("replacement")
        self.assertEqual(replacement["fence"], old["fence"] + 1)
