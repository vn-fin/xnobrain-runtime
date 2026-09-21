"""Worker lifecycle tests with real persistent claims and blocked archive work."""

import asyncio
import tempfile
import threading
import unittest
from pathlib import Path

from xnobrain.repositories.portability_tasks import PortabilityTaskStore
from xnobrain.services.portability_worker import PortabilityWorker


class PortabilityWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = PortabilityTaskStore(Path(self.directory.name))

    def admit(self, kind="EXPORT"):
        task, _ = self.store.admit(
            scope="workspace",
            actor="actor",
            kind=kind,
            key="key",
            fingerprint="fingerprint",
            inputs={},
            upload_id="upload" if kind == "IMPORT" else None,
        )
        return task

    async def wait_status(self, task, expected):
        async with asyncio.timeout(5):
            while True:
                row = self.store.get(task["id"], scope="workspace", actor="actor")
                if row["status"] == expected:
                    return row
                await asyncio.sleep(0.01)

    async def test_blocked_execution_does_not_block_event_loop_and_shutdown_drains(self):
        task = self.admit()
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)

        def execute(claim):
            entered.set()
            release.wait(5)
            return {"export_id": claim["id"]}

        worker = PortabilityWorker(self.store, execute, lease_seconds=0.3, poll_seconds=0.01)
        await worker.start()
        await worker.start()  # Lifecycle start is idempotent.
        self.assertTrue(await asyncio.to_thread(entered.wait, 2))
        await asyncio.sleep(0.45)  # More than one lease, renewed while blocked.
        self.assertIsNone(self.store.claim("competitor"))
        stopping = asyncio.create_task(worker.shutdown())
        await asyncio.sleep(0.02)
        self.assertFalse(stopping.done())
        release.set()
        await asyncio.wait_for(stopping, 3)
        await self.wait_status(task, "COMPLETED")

    async def test_prepublication_import_failure_releases_pin_and_keeps_receipt(self):
        task = self.admit("IMPORT")

        def execute(_claim):
            raise ValueError("private user content must never escape")

        worker = PortabilityWorker(self.store, execute, poll_seconds=0.01)
        await worker.start()
        try:
            row = await self.wait_status(task, "FAILED")
            self.assertNotIn("private user", row["error_json"])
            self.assertIn("import_failed", row["error_json"])
            self.assertFalse(self.store.pinned("upload"))
            replay = self.admit("IMPORT")
            self.assertEqual(replay["id"], task["id"])
            self.assertEqual(replay["status"], "FAILED")
        finally:
            await worker.shutdown()

    async def test_failed_maintenance_does_not_starve_queued_work(self):
        task = self.admit()
        calls = []

        def maintenance():
            calls.append(True)
            raise OSError("private storage path")

        worker = PortabilityWorker(
            self.store,
            lambda claim: {"export_id": claim["id"]},
            poll_seconds=0.01,
            maintenance=maintenance,
        )
        with self.assertLogs("xnobrain.services.portability_worker", level="WARNING") as logs:
            await worker.start()
            try:
                await self.wait_status(task, "COMPLETED")
                await asyncio.sleep(0.05)
                self.assertEqual(len(calls), 1)
            finally:
                await worker.shutdown()
        self.assertNotIn("private storage path", " ".join(logs.output))

    async def test_post_intent_import_failure_preserves_pin_and_evidence(self):
        task = self.admit("IMPORT")

        def execute(claim):
            self.store.reserve_target(
                claim["id"],
                claim["lease_owner"],
                claim["fence"],
                kind="PROFILE",
                source_id="source",
                target_id="reserved",
            )
            self.store.record_publication(
                claim["id"],
                claim["lease_owner"],
                claim["fence"],
                kind="PROFILE",
                source_id="source",
                digest="digest",
                phase="PREPARED",
            )
            raise OSError("private target content")

        worker = PortabilityWorker(self.store, execute, poll_seconds=0.01)
        await worker.start()
        try:
            row = await self.wait_status(task, "FAILED")
            self.assertIn("import_recovery_required", row["error_json"])
            self.assertNotIn("private target", row["error_json"])
            self.assertTrue(self.store.pinned("upload"))
            self.assertFalse(self.store.resource_visible("PROFILE", "reserved"))
        finally:
            await worker.shutdown()
