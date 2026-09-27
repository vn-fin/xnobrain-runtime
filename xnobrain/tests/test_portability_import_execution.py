"""Real ZIP export/upload/import execution through the durable task worker."""

import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xnobrain.repositories.files import FileRepository
from xnobrain.services.portability import PortabilityService
from xnobrain.services.portability_tasks import PortabilityTasks


class ImportExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_bundle_import_completes_once_and_replays_report(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("os.environ", {"RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": ""}),
        ):
            root = Path(directory)
            repository = FileRepository(root / "data", root / "profiles")
            profile = repository.profiles_root / "source"
            profile.mkdir()
            (profile / "config.yaml").write_text("model:\n  default: test\n")
            (profile / "agent.json").write_text('{"name":"source","display_name":"Source"}')
            (profile / "workspace").mkdir()
            (profile / "workspace" / "note.txt").write_text("portable content")
            portability = PortabilityService(repository, root / "hermes")
            payload, _ = portability.export({"agent_ids": ["source"]})
            upload = portability.start_upload({"filename": "snapshot.zip", "size": len(payload)})
            portability.put_upload_part(upload["upload_id"], 0, payload)
            portability.complete_upload(
                upload["upload_id"], {"sha256": hashlib.sha256(payload).hexdigest()}
            )
            completed = []
            tasks = PortabilityTasks(
                portability, on_import_completed=lambda: completed.append(True)
            )
            repository.portability_task_store = tasks.store
            body = {"upload_id": upload["upload_id"]}
            task, _ = tasks.create_import(body, scope="workspace", actor="actor", key="once")
            await tasks.worker.start()
            try:
                async with asyncio.timeout(10):
                    while True:
                        current = tasks.get(task["task_id"], scope="workspace", actor="actor")
                        if current["status"] in {"COMPLETED", "FAILED"}:
                            break
                        await asyncio.sleep(0.01)
                self.assertEqual(current["status"], "COMPLETED", current)
                async with asyncio.timeout(2):
                    while not completed:
                        await asyncio.sleep(0.01)
                self.assertEqual(completed, [True])
                target = current["result"]["agent_id_mappings"]["source"]
                self.assertEqual(
                    (repository.profile_path(target) / "workspace" / "note.txt").read_text(),
                    "portable content",
                )
                replay, created = tasks.create_import(
                    body, scope="workspace", actor="actor", key="once"
                )
                self.assertFalse(created)
                self.assertEqual(replay["result"], current["result"])
                self.assertEqual(len(list(repository.profiles_root.iterdir())), 2)
                tasks.cleanup_terminal_inputs()
                self.assertEqual(list((repository.data_dir / "portability-inputs").iterdir()), [])
                self.assertFalse(
                    (repository.data_dir / "portability-imports" / task["task_id"]).exists()
                )
                after_cleanup, created = tasks.create_import(
                    body, scope="workspace", actor="actor", key="once"
                )
                self.assertFalse(created)
                self.assertEqual(after_cleanup["result"], current["result"])
                self.assertEqual(
                    (repository.profile_path(target) / "workspace" / "note.txt").read_text(),
                    "portable content",
                )
            finally:
                await tasks.worker.shutdown()

    async def test_real_team_bundle_remaps_every_profile_reference(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("os.environ", {"RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": ""}),
        ):
            root = Path(directory)
            repository = FileRepository(root / "data", root / "profiles")
            for name in ("leader", "member"):
                profile = repository.profiles_root / name
                profile.mkdir()
                (profile / "config.yaml").write_text("model: {}\n")
                (profile / "agent.json").write_text('{"name":"' + name + '"}')
                (profile / "workspace").mkdir()
            repository.put_team(
                {
                    "id": "team",
                    "name": "Test team",
                    "orchestrator_id": "leader",
                    "synthesis_agent_id": "leader",
                    "members": [{"agent_id": "member", "enabled": True}],
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
            tasks.create_import(
                {"upload_id": upload["upload_id"]}, scope="workspace", actor="actor", key="team"
            )
            claim = tasks.store.claim("worker")
            report = await asyncio.to_thread(tasks.execute, claim)
            team_id = report["team_id_mappings"]["team"]
            from xnobrain.repositories.base import StoreError

            with self.assertRaises(StoreError):
                repository.get_team(team_id)
            self.assertEqual(len(repository.list_teams()), 1)
            hidden_profile = report["agent_id_mappings"]["leader"]
            for identifier in (team_id, f" {team_id} "):
                with self.assertRaises(StoreError):
                    repository.get_team(identifier)
                with self.assertRaises(StoreError):
                    repository.put_team({"id": identifier, "name": "Overwrite"})
                with self.assertRaises(StoreError):
                    repository.delete_team(identifier)
            with self.assertRaises(StoreError):
                repository.live_profile_path(hidden_profile)
            with self.assertRaises(StoreError):
                repository.profile_path(hidden_profile)
            tasks.store.finish(claim["id"], "worker", claim["fence"], result=report)
            team = repository.get_team(team_id)
            self.assertEqual(team["orchestrator_id"], report["agent_id_mappings"]["leader"])
            self.assertEqual(team["synthesis_agent_id"], report["agent_id_mappings"]["leader"])
            self.assertEqual(team["members"][0]["agent_id"], report["agent_id_mappings"]["member"])
            self.assertEqual(team["workflow"][0]["agent_id"], report["agent_id_mappings"]["member"])
            self.assertEqual(len(repository.list_teams()), 2)
            from io import BytesIO
            from zipfile import ZipFile

            exported, _ = portability.export({"team_ids": [team_id]})
            with ZipFile(BytesIO(exported)) as archive:
                self.assertFalse(
                    any(name.endswith(".portability-owner.json") for name in archive.namelist())
                )
                self.assertNotIn(b"_portability_task_id", archive.read(f"teams/{team_id}.yaml"))
                self.assertFalse(any("portability.sqlite3" in name for name in archive.namelist()))
