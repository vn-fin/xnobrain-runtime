"""Slow part I/O must not monopolize snapshot publication or lose upload fencing."""

import hashlib
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from xnobrain.repositories.base import StoreError
from xnobrain.repositories.files import FileRepository
from xnobrain.services.portability import UPLOAD_CHUNK_SIZE, PortabilityService


class UploadConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {"RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": ""}))
        root = Path(self.temporary)
        self.repository = FileRepository(root / "data", root / "profiles")
        self.service = PortabilityService(self.repository, root / "hermes")

    def upload(self, size):
        return self.service.start_upload({"size": size})["upload_id"]

    def assert_busy(self, operation):
        with self.assertRaises(StoreError) as caught:
            operation()
        self.assertEqual(caught.exception.code, "task_storage_busy")
        self.assertEqual(caught.exception.status, 429)

    def test_four_parts_fsync_concurrently_without_blocking_publication(self):
        upload_id = self.upload(4 * UPLOAD_CHUNK_SIZE)
        arrived = threading.Barrier(5, timeout=5)
        release = threading.Event()
        original_fsync = os.fsync

        def slow_fsync(descriptor):
            if stat.S_ISREG(os.fstat(descriptor).st_mode):
                arrived.wait()
                if not release.wait(5):
                    raise TimeoutError("part write did not resume")
            return original_fsync(descriptor)

        chunks = [bytes([number]) * UPLOAD_CHUNK_SIZE for number in range(4)]
        with ThreadPoolExecutor(max_workers=4) as pool, patch("os.fsync", slow_fsync):
            futures = [
                pool.submit(self.service.put_upload_part, upload_id, number, chunk)
                for number, chunk in enumerate(chunks)
            ]
            try:
                arrived.wait()
                # All writes remain in flight beyond the old 100 ms timeout.
                with self.service.task_store.publication_lock():
                    self.assertFalse(self.service.task_store.pinned(upload_id))
                self.assert_busy(lambda: self.service.complete_upload(upload_id, {}))
                self.assert_busy(lambda: self.service.delete_transfer("upload", upload_id))
                directory = self.service.upload_root / upload_id
                os.utime(directory, (1, 1))
                self.service._cleanup_transfers()
                self.assertTrue(directory.exists())
            finally:
                release.set()
            results = [future.result(timeout=5) for future in futures]
        for number, result in enumerate(results):
            self.assertEqual(result["sha256"], hashlib.sha256(chunks[number]).hexdigest())
        with patch.object(self.service, "dry_run_file", return_value={}):
            complete = self.service.complete_upload(upload_id, {})
        self.assertTrue(complete["complete"])
        self.assertEqual((directory / "bundle.zip").read_bytes(), b"".join(chunks))
        self.assertEqual(self.service.complete_upload(upload_id, {}), complete)
        with self.assertRaises(StoreError) as caught:
            self.service.put_upload_part(upload_id, 0, chunks[0])
        self.assertEqual(caught.exception.code, "upload_complete")

    def test_preview_blocks_same_upload_but_other_upload_can_write(self):
        first = self.upload(1)
        second = self.upload(1)
        self.service.put_upload_part(first, 0, b"a")
        entered = threading.Event()
        release = threading.Event()

        def slow_preview(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("preview did not resume")
            return {}

        with (
            ThreadPoolExecutor(max_workers=1) as pool,
            patch.object(self.service, "dry_run_file", slow_preview),
        ):
            future = pool.submit(self.service.complete_upload, first, {})
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(self.service.put_upload_part(second, 0, b"b")["size"], 1)
                self.assert_busy(lambda: self.service.put_upload_part(first, 0, b"c"))
                self.assert_busy(lambda: self.service.apply_upload(first, {}))
                self.assert_busy(lambda: self.service.delete_transfer("upload", first))
            finally:
                release.set()
            self.assertTrue(future.result(timeout=5)["complete"])

    def test_replayed_part_remains_atomic_and_failure_releases_lock(self):
        upload_id = self.upload(1)
        directory = self.service.upload_root / upload_id
        self.service.put_upload_part(upload_id, 0, b"a")
        with patch("os.fsync", side_effect=OSError("simulated storage failure")):
            with self.assertRaises(OSError):
                self.service.put_upload_part(upload_id, 0, b"b")
        self.assertEqual((directory / "parts" / "00000000.part").read_bytes(), b"a")
        self.assertEqual(len(list((directory / "parts").iterdir())), 1)
        replay = self.service.put_upload_part(upload_id, 0, b"a")
        self.assertEqual(replay["sha256"], hashlib.sha256(b"a").hexdigest())
        self.service.delete_transfer("upload", upload_id)
        with self.assertRaises(StoreError) as caught:
            self.service.put_upload_part(upload_id, 0, b"a")
        self.assertEqual(caught.exception.status, 404)
        self.assertFalse(directory.exists())

    def test_directory_lock_excludes_other_process_and_releases_on_exit(self):
        upload_id = self.upload(1)
        directory = self.service.upload_root / upload_id
        script = """
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit(23)
"""
        with self.service._upload_access(upload_id, shared=True):
            result = subprocess.run([sys.executable, "-c", script, str(directory)], check=False)
            self.assertEqual(result.returncode, 23)
        result = subprocess.run([sys.executable, "-c", script, str(directory)], check=False)
        self.assertEqual(result.returncode, 0)
        # The child exited without explicitly unlocking; no stale lock survives.
        self.assertEqual(self.service.put_upload_part(upload_id, 0, b"a")["size"], 1)

    def test_completed_upload_replay_preserves_import_pin(self):
        upload_id = self.upload(1)
        self.service.put_upload_part(upload_id, 0, b"a")
        with patch.object(self.service, "dry_run_file", return_value={}):
            receipt = self.service.complete_upload(upload_id, {})
        self.service.task_store.admit(
            scope="workspace",
            actor="actor",
            kind="IMPORT",
            key="request",
            fingerprint="fixture",
            inputs={"upload_id": upload_id},
            upload_id=upload_id,
        )
        self.assertEqual(self.service.complete_upload(upload_id, {}), receipt)
        self.assertTrue(self.service.task_store.pinned(upload_id))
        with self.assertRaises(StoreError) as caught:
            self.service.put_upload_part(upload_id, 0, b"b")
        self.assertEqual(caught.exception.code, "transfer_in_use")
