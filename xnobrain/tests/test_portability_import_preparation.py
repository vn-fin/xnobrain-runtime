"""Preparation reuses validated import code without publishing live resources."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xnobrain.repositories.files import FileRepository
from xnobrain.services.portability import PortabilityService
from xnobrain.services.portability_imports import ImportPreparation
from xnobrain.services.portability_tasks import PortabilityTasks


class ImportPreparationTests(unittest.TestCase):
    def test_private_preparation_survives_restart_without_live_mutation(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("os.environ", {"RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": ""}),
        ):
            root = Path(directory)
            repository = FileRepository(root / "data", root / "profiles")
            portability = PortabilityService(repository, root / "hermes")
            tasks = PortabilityTasks(portability)
            upload = portability.upload_root / "upload"
            upload.mkdir()
            payload = b"archive fixture"
            (upload / "bundle.zip").write_bytes(payload)
            repository.atomic_json(
                upload / "metadata.json",
                {
                    "complete": True,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                },
            )
            task, _ = tasks.create_import(
                {"upload_id": "upload"}, scope="workspace", actor="actor", key="key"
            )
            claim = tasks.store.claim("worker")

            def apply(service, _path, _environment):
                profile = service.repository.profiles_root / "imported"
                profile.mkdir()
                (profile / "config.yaml").write_text("approval_mode: manual\n")
                return {"agent_id_mappings": {"source": "imported"}, "team_id_mappings": {}}

            with patch.object(
                PortabilityService, "apply_file", autospec=True, side_effect=apply
            ) as operation:
                stage, receipt = ImportPreparation(tasks).prepare(claim)
                again, second = ImportPreparation(tasks).prepare(claim)
                self.assertEqual(operation.call_count, 1)
            self.assertEqual(stage, again)
            self.assertEqual(receipt, second)
            self.assertEqual(receipt["task_id"], task["task_id"])
            self.assertTrue((stage / "profiles" / "imported" / "config.yaml").is_file())
            self.assertEqual(list(repository.profiles_root.iterdir()), [])
            self.assertEqual(json.loads((stage / "prepared.json").read_text()), receipt)

            preparation = ImportPreparation(tasks)
            mappings = preparation.reserve_mappings(claim, receipt)
            self.assertEqual(mappings, preparation.reserve_mappings(claim, receipt))
            publication = preparation.prepare_publication(claim, stage, receipt, mappings)
            self.assertEqual(
                publication, preparation.prepare_publication(claim, stage, receipt, mappings)
            )
            target = mappings["agent_id_mappings"]["source"]
            self.assertNotEqual(target, "imported")
            metadata = json.loads((publication / "profiles" / target / "agent.json").read_text())
            self.assertEqual(metadata["profile_name"], target)
            journal = tasks.store.journal(claim["id"])
            self.assertEqual(journal[0]["phase"], "PREPARED")
            self.assertEqual(
                journal[0]["digest"], preparation.content_digest(publication / "profiles" / target)
            )
            self.assertEqual(list(repository.profiles_root.iterdir()), [])

            (publication / "ready.json").unlink()
            rebuilt = preparation.prepare_publication(claim, stage, receipt, mappings)
            self.assertEqual(
                journal[0]["digest"], preparation.content_digest(rebuilt / "profiles" / target)
            )
            # Simulate a worker pause longer than its lease while hashing. It
            # must revalidate authority before the live rename, not only on lock
            # acquisition at the beginning of publication.
            from xnobrain.services.portability_worker import ClaimLostError

            digest = preparation.content_digest

            def expire_during_hash(path):
                result = digest(path)
                with tasks.store.transaction() as db:
                    db.execute(
                        "UPDATE portability_tasks SET lease_until=0 WHERE id=?", (claim["id"],)
                    )
                return result

            with patch.object(preparation, "content_digest", side_effect=expire_during_hash):
                with self.assertRaises(ClaimLostError):
                    preparation.publish(claim, publication)
            self.assertFalse((repository.profiles_root / target).exists())
            self.assertTrue((publication / "profiles" / target).is_dir())
            claim = tasks.store.claim("replacement")
            original = tasks.store.record_publication

            def crash_after_rename(*args, **kwargs):
                if kwargs.get("phase") == "PUBLISHED":
                    raise RuntimeError("simulated process death before receipt")
                return original(*args, **kwargs)

            with patch.object(tasks.store, "record_publication", side_effect=crash_after_rename):
                with self.assertRaises(RuntimeError):
                    preparation.publish(claim, publication)
            self.assertTrue((repository.profiles_root / target).is_dir())
            self.assertEqual(tasks.store.journal(claim["id"])[0]["phase"], "PREPARED")
            preparation.publish(claim, publication)
            self.assertEqual(tasks.store.journal(claim["id"])[0]["phase"], "PUBLISHED")
            self.assertEqual(len(list(repository.profiles_root.iterdir())), 1)
            (repository.profiles_root / target / "config.yaml").write_text("externally changed")
            from xnobrain.repositories.base import StoreError

            with self.assertRaises(StoreError):
                preparation.publish(claim, publication)
            self.assertEqual(
                (repository.profiles_root / target / "config.yaml").read_text(),
                "externally changed",
            )
