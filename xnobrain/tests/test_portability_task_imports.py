"""Import acceptance persists one input reference, never public credentials."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xnobrain.repositories.base import StoreError
from xnobrain.repositories.files import FileRepository
from xnobrain.services.portability import PortabilityService
from xnobrain.services.portability_tasks import PortabilityTasks


class ImportAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        environment = patch.dict(os.environ, {"RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": ""})
        environment.start()
        self.addCleanup(environment.stop)
        root = Path(self.temporary.name)
        self.repository = FileRepository(root / "data", root / "profiles")
        self.portability = PortabilityService(self.repository, root / "hermes")
        self.tasks = PortabilityTasks(self.portability)
        self.upload = self.portability.upload_root / "upload"
        self.upload.mkdir()
        (self.upload / "bundle.zip").write_bytes(b"fixture")
        self.repository.atomic_json(
            self.upload / "metadata.json",
            {
                "complete": True,
                "sha256": "archive-digest",
            },
        )

    def submit(self, **body):
        return self.tasks.create_import(
            {"upload_id": "upload", **body}, scope="workspace", actor="actor", key="key"
        )

    def test_duplicate_admission_keeps_one_private_environment(self):
        task, created = self.submit(environment={"SERVICE_TOKEN": "test-secret"})
        replay, repeated = self.submit(environment={"SERVICE_TOKEN": "test-secret"})
        self.assertTrue(created)
        self.assertFalse(repeated)
        self.assertEqual(task["task_id"], replay["task_id"])
        self.assertNotIn("test-secret", json.dumps(task))
        row = self.tasks.store.get(task["task_id"], scope="workspace", actor="actor")
        self.assertNotIn("test-secret", json.dumps(row))
        secrets = list((self.repository.data_dir / "portability-inputs").iterdir())
        self.assertEqual(len(secrets), 1)
        self.assertEqual(secrets[0].stat().st_mode & 0o777, 0o600)
        self.assertTrue(self.tasks.store.pinned("upload"))

    def test_replay_does_not_require_deleted_upload(self):
        task, _ = self.submit()
        (self.upload / "bundle.zip").unlink()
        (self.upload / "metadata.json").unlink()
        self.upload.rmdir()
        replay, created = self.submit()
        self.assertFalse(created)
        self.assertEqual(replay["task_id"], task["task_id"])

    def test_environment_change_conflicts(self):
        self.submit(environment={"TOKEN": "one"})
        with self.assertRaises(StoreError) as caught:
            self.submit(environment={"TOKEN": "two"})
        self.assertEqual(caught.exception.code, "idempotency_conflict")

    def test_legacy_delete_and_expiry_respect_pin(self):
        self.submit()
        with self.assertRaises(StoreError) as caught:
            self.portability.delete_transfer("upload", "upload")
        self.assertEqual(caught.exception.code, "transfer_in_use")
        os.utime(self.upload, (1, 1))
        self.portability._cleanup_transfers()
        self.assertTrue((self.upload / "bundle.zip").is_file())

    def test_pinned_upload_cannot_be_mutated_or_synchronously_applied(self):
        self.submit()
        for operation in (
            lambda: self.portability.put_upload_part("upload", 0, b"replacement"),
            lambda: self.portability.apply_upload("upload", {}),
        ):
            with self.assertRaises(StoreError) as caught:
                operation()
            self.assertEqual(caught.exception.code, "transfer_in_use")
        self.assertEqual((self.upload / "bundle.zip").read_bytes(), b"fixture")

    def test_replay_rejects_replaced_archive_metadata(self):
        self.submit()
        self.repository.atomic_json(
            self.upload / "metadata.json",
            {
                "complete": True,
                "sha256": "different-archive-digest",
            },
        )
        with self.assertRaises(StoreError) as caught:
            self.submit()
        self.assertEqual(caught.exception.code, "idempotency_conflict")

    def test_safe_failed_import_cleanup_erases_environment_but_retains_replay(self):
        task, _ = self.submit(environment={"TOKEN": "private-fixture"})
        claim = self.tasks.store.claim("worker")
        self.tasks.store.finish(
            claim["id"],
            "worker",
            claim["fence"],
            error={"code": "import_failed", "message": "Import failed", "retryable": False},
        )
        stage = self.repository.data_dir / "portability-imports" / task["task_id"]
        stage.mkdir(parents=True)
        (stage / "scratch").write_text("private scratch")
        self.tasks.cleanup_terminal_inputs()
        self.assertFalse(stage.exists())
        self.assertEqual(list((self.repository.data_dir / "portability-inputs").iterdir()), [])
        self.assertFalse(self.tasks.store.pinned("upload"))
        replay, created = self.submit(environment={"TOKEN": "private-fixture"})
        self.assertFalse(created)
        self.assertEqual(replay["task_id"], task["task_id"])
        self.assertEqual(replay["status"], "FAILED")
