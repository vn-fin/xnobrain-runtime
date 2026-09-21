"""Export task admission and publication integration against real archive code."""

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xnobrain.repositories.files import FileRepository
from xnobrain.services.portability import PortabilityService
from xnobrain.services.portability_tasks import PortabilityTasks


class SnapshotTasksTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.repository = FileRepository(root / "data", root / "profiles")
        self.portability = PortabilityService(self.repository, root / "hermes")
        self.tasks = PortabilityTasks(self.portability)

    def test_acceptance_does_not_generate_archive(self):
        with patch.object(
            self.portability, "_export_to_path", side_effect=AssertionError("request blocked")
        ):
            task, created = self.tasks.create_export(
                {"agent_ids": ["profile"]}, scope="workspace", actor="actor", key="create"
            )
            replay, replay_created = self.tasks.create_export(
                {"agent_ids": ["profile"]}, scope="workspace", actor="actor", key="create"
            )
        self.assertTrue(created)
        self.assertFalse(replay_created)
        self.assertEqual(task["task_id"], replay["task_id"])
        self.assertEqual(task["status"], "PENDING")
        self.assertNotIn("input_json", task)

    def test_published_archive_is_recovered_without_regeneration(self):
        def generate(_body, target):
            target.write_bytes(b"synthetic archive")
            return {
                "filename": "profile.zip",
                "size": 17,
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }

        task, _ = self.tasks.create_export(
            {"agent_ids": ["profile"]}, scope="workspace", actor="actor"
        )
        claim = self.tasks.store.claim("worker")
        with patch.object(
            self.portability, "_export_to_path", side_effect=generate
        ) as generate_mock:
            result = self.tasks.execute(claim)
            recovered = self.tasks.execute(claim)
            self.assertEqual(generate_mock.call_count, 1)
        self.assertEqual(result, recovered)
        self.tasks.store.finish(claim["id"], "worker", claim["fence"], result=result)
        projection = self.tasks.get(task["task_id"], scope="workspace", actor="actor")
        self.assertEqual(projection["status"], "COMPLETED")
        self.assertTrue(projection["result"]["artifact_available"])
        self.assertNotIn("expires_at_epoch", projection["result"])

    def test_authorized_download_and_explicit_delete_preserve_receipt(self):
        def generate(_body, target):
            target.write_bytes(b"snapshot bytes")
            return {
                "filename": "profile.zip",
                "size": 14,
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }

        task, _ = self.tasks.create_export(
            {"agent_ids": ["profile"]}, scope="workspace", actor="actor", key="download"
        )
        claim = self.tasks.store.claim("worker")
        with patch.object(self.portability, "_export_to_path", side_effect=generate):
            result = self.tasks.execute(claim)
        self.tasks.store.finish(claim["id"], "worker", claim["fence"], result=result)
        payload, _ = self.tasks.read_part(task["export_id"], 0, scope="workspace", actor="actor")
        self.assertEqual(payload, b"snapshot bytes")
        from xnobrain.repositories.base import StoreError

        with self.assertRaises(StoreError) as caught:
            self.tasks.read_part(task["export_id"], 0, scope="workspace", actor="foreign")
        self.assertEqual(caught.exception.status, 404)
        with self.assertRaises(StoreError) as busy:
            self.tasks.delete_export(task["export_id"], scope="workspace", actor="actor")
        self.assertEqual(busy.exception.code, "transfer_in_use")
        with self.tasks.store.transaction() as db:
            db.execute("UPDATE portability_download_leases SET expires_at=0")
        self.tasks.delete_export(task["export_id"], scope="workspace", actor="actor")
        with self.assertRaises(StoreError) as caught:
            self.tasks.read_part(task["export_id"], 0, scope="workspace", actor="actor")
        self.assertEqual(caught.exception.status, 410)
        replay, created = self.tasks.create_export(
            {"agent_ids": ["profile"]}, scope="workspace", actor="actor", key="download"
        )
        self.assertFalse(created)
        self.assertEqual(replay["status"], "COMPLETED")
        self.assertFalse(replay["result"]["artifact_available"])

    def test_low_disk_rejects_before_admission(self):
        from types import SimpleNamespace

        from xnobrain.repositories.base import StoreError

        with patch("shutil.disk_usage", return_value=SimpleNamespace(free=0)):
            with self.assertRaises(StoreError) as caught:
                self.tasks.create_export(
                    {"agent_ids": ["profile"]}, scope="workspace", actor="actor"
                )
        self.assertEqual(caught.exception.code, "task_storage_quota")
        self.assertEqual(self.tasks.list(scope="workspace", actor="actor")["items"], [])

    def test_replay_survives_storage_pressure(self):
        from types import SimpleNamespace

        first, _ = self.tasks.create_export(
            {"agent_ids": ["profile"]}, scope="workspace", actor="actor", key="same"
        )
        with patch("shutil.disk_usage", return_value=SimpleNamespace(free=0)):
            repeated, created = self.tasks.create_export(
                {"agent_ids": ["profile"]}, scope="workspace", actor="actor", key="same"
            )
        self.assertFalse(created)
        self.assertEqual(first["task_id"], repeated["task_id"])

    def test_delete_detaches_before_slow_reclamation_and_can_resume(self):
        import shutil

        from xnobrain.repositories.portability_tasks import PortabilityTaskStore

        task, _ = self.tasks.create_export(
            {"agent_ids": ["profile"]}, scope="workspace", actor="actor", key="detach"
        )
        claim = self.tasks.store.claim("worker")
        directory = self.tasks.artifacts / task["export_id"]
        directory.mkdir()
        (directory / "bundle.zip").write_bytes(b"snapshot")
        self.tasks.store.finish(
            claim["id"],
            "worker",
            claim["fence"],
            result={"export_id": task["export_id"], "expires_at_epoch": 9999999999},
        )
        other = PortabilityTaskStore(self.tasks.store.path.parent)
        real_remove = shutil.rmtree

        def interrupted_remove(path):
            self.assertFalse(directory.exists())
            with other.publication_lock():
                pass
            raise OSError("simulated reclamation interruption")

        with patch("shutil.rmtree", side_effect=interrupted_remove):
            with self.assertRaises(OSError):
                self.tasks.delete_export(task["export_id"], scope="workspace", actor="actor")
        detached = self.tasks.artifacts / f".deleted-{task['export_id']}"
        self.assertTrue(detached.exists())
        with patch("shutil.rmtree", wraps=real_remove):
            self.tasks.delete_export(task["export_id"], scope="workspace", actor="actor")
        self.assertFalse(detached.exists())
        replay, created = self.tasks.create_export(
            {"agent_ids": ["profile"]}, scope="workspace", actor="actor", key="detach"
        )
        self.assertFalse(created)
        self.assertFalse(replay["result"]["artifact_available"])
