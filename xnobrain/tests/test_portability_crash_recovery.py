"""Import recovery across abrupt process death using real ZIPs and durable state."""

import multiprocessing
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xnobrain.repositories.files import FileRepository
from xnobrain.repositories.portability_publication import rename_without_replace
from xnobrain.services.portability import PortabilityService
from xnobrain.services.portability_tasks import PortabilityTasks
from xnobrain.services.portability_worker import ClaimLostError


def crash_import(root_string, boundary):
    """Exit without finally/rollback handlers at a publication/commit boundary."""
    root = Path(root_string)
    repository = FileRepository(root / "data", root / "profiles")
    tasks = PortabilityTasks(PortabilityService(repository, root / "hermes"))
    claim = tasks.store.claim("crashing-worker")
    original_record = tasks.store.record_publication

    def rename(source, target):
        rename_without_replace(source, target)
        resource = "team" if target.suffix == ".yaml" else "profile"
        if boundary == f"{resource}-rename":
            os._exit(73)

    def record(*args, **kwargs):
        original_record(*args, **kwargs)
        resource = "team" if kwargs["kind"] == "TEAM" else "profile"
        if kwargs["phase"] == "PUBLISHED" and boundary == f"{resource}-receipt":
            os._exit(73)

    with (
        patch("xnobrain.repositories.portability_publication.rename_without_replace", rename),
        patch.object(tasks.store, "record_publication", record),
    ):
        result = tasks.execute(claim)
        if boundary == "before-commit":
            os._exit(73)
        tasks.store.finish(claim["id"], "crashing-worker", claim["fence"], result=result)
        if boundary == "after-commit":
            os._exit(73)
    os._exit(74)


class ImportCrashRecoveryTests(unittest.TestCase):
    def test_restart_recovers_same_mappings_at_every_publication_boundary(self):
        for boundary in (
            "profile-rename",
            "profile-receipt",
            "team-rename",
            "team-receipt",
            "before-commit",
            "after-commit",
        ):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repository = FileRepository(root / "data", root / "profiles")
                for name in ("leader", "member"):
                    profile = repository.profiles_root / name
                    profile.mkdir()
                    (profile / "config.yaml").write_text("model: {}\n")
                    (profile / "agent.json").write_text('{"name":"' + name + '"}')
                repository.put_team(
                    {
                        "id": "team",
                        "name": "Crash test",
                        "orchestrator_id": "leader",
                        "synthesis_agent_id": "leader",
                        "members": [{"agent_id": "member"}],
                        "workflow": [{"id": "step", "agent_id": "member"}],
                    }
                )
                portability = PortabilityService(repository, root / "hermes")
                payload, _ = portability.export({"team_ids": ["team"]})
                upload = portability.start_upload({"filename": "team.zip", "size": len(payload)})
                portability.put_upload_part(upload["upload_id"], 0, payload)
                portability.complete_upload(upload["upload_id"], {})
                tasks = PortabilityTasks(portability)
                repository.portability_task_store = tasks.store
                body = {"upload_id": upload["upload_id"]}
                accepted, _ = tasks.create_import(
                    body, scope="workspace", actor="actor", key="once"
                )
                task_id = accepted["task_id"]
                process = multiprocessing.get_context("spawn").Process(
                    target=crash_import,
                    args=(str(root), boundary),
                )
                process.start()
                try:
                    process.join(20)
                    self.assertEqual(process.exitcode, 73)
                finally:
                    if process.is_alive():
                        process.kill()
                        process.join(5)
                    process.close()

                journal = tasks.store.journal(task_id)
                self.assertEqual(len(journal), 3)
                if boundary != "after-commit":
                    for entry in journal:
                        self.assertFalse(
                            tasks.store.resource_visible(entry["resource_kind"], entry["target_id"])
                        )
                    with tasks.store.transaction() as db:
                        db.execute(
                            "UPDATE portability_tasks SET lease_until=0 WHERE id=?", (task_id,)
                        )
                    recovered = PortabilityTasks(PortabilityService(repository, root / "hermes"))
                    claim = recovered.store.claim("replacement")
                    self.assertEqual(claim["id"], task_id)
                    self.assertEqual(claim["fence"], 2)
                    # An old, still-alive worker has no publication authority.
                    with self.assertRaises(ClaimLostError):
                        recovered._require_claim(
                            {**claim, "lease_owner": "crashing-worker", "fence": 1}
                        )
                    report = recovered.execute(claim)
                    self.assertTrue(
                        recovered.store.finish(task_id, "replacement", 2, result=report)
                    )
                replay, created = tasks.create_import(
                    body, scope="workspace", actor="actor", key="once"
                )
                self.assertFalse(created)
                self.assertEqual(replay["status"], "COMPLETED")
                report = replay["result"]
                self.assertEqual(
                    report["agent_id_mappings"],
                    {
                        row["source_id"]: row["target_id"]
                        for row in journal
                        if row["resource_kind"] == "PROFILE"
                    },
                )
                self.assertEqual(
                    report["team_id_mappings"],
                    {
                        row["source_id"]: row["target_id"]
                        for row in journal
                        if row["resource_kind"] == "TEAM"
                    },
                )
                self.assertEqual(len(list(repository.profiles_root.iterdir())), 4)
                self.assertEqual(len(repository.list_teams()), 2)
                imported_team = repository.get_team(report["team_id_mappings"]["team"])
                self.assertEqual(
                    imported_team["members"][0]["agent_id"], report["agent_id_mappings"]["member"]
                )
                self.assertFalse(tasks.store.pinned(upload["upload_id"]))
                with tasks.store.transaction() as db:
                    terminal_events = db.execute(
                        """SELECT count(*) FROM portability_events e JOIN portability_tasks t
                        ON t.id=e.task_id AND t.revision=e.revision
                        WHERE t.id=? AND t.status='COMPLETED'""",
                        (task_id,),
                    ).fetchone()[0]
                self.assertEqual(terminal_events, 1)
                tasks.cleanup_terminal_inputs()
                self.assertEqual(
                    tasks.create_import(body, scope="workspace", actor="actor", key="once")[0][
                        "result"
                    ],
                    report,
                )
