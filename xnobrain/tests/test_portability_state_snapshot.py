"""SQLite/WAL consistency of portable profile exports."""

import sqlite3
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from xnobrain.repositories.base import StoreError
from xnobrain.repositories.files import FileRepository
from xnobrain.services.portability import PortabilityService


class StateSnapshotTests(unittest.TestCase):
    def test_export_captures_committed_wal_and_import_keeps_history(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("os.environ", {"RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": ""}),
        ):
            root = Path(directory)
            repository = FileRepository(root / "data", root / "profiles")
            profile = repository.profile_path("source")
            profile.mkdir(parents=True)
            (profile / "config.yaml").write_text("model: {}\n", encoding="utf-8")
            with sqlite3.connect(profile / "state.db") as writer:
                writer.execute("PRAGMA journal_mode=WAL")
                writer.execute("CREATE TABLE history (message TEXT)")
                writer.execute("INSERT INTO history VALUES ('committed WAL history')")
                writer.commit()
                portability = PortabilityService(repository, root / "hermes")
                payload, _ = portability.export({"agent_ids": ["source"]})
            with ZipFile(BytesIO(payload)) as archive:
                self.assertIn("profiles/source/state.db", archive.namelist())
                self.assertNotIn("profiles/source/state.db-wal", archive.namelist())
                snapshot = root / "snapshot.db"
                snapshot.write_bytes(archive.read("profiles/source/state.db"))
            with sqlite3.connect(snapshot) as connection:
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone(), ("ok",))
                self.assertEqual(
                    connection.execute("SELECT message FROM history").fetchone(),
                    ("committed WAL history",),
                )
            report = portability.apply(payload)
            with sqlite3.connect(
                repository.profile_path(report["agent_id_mappings"]["source"]) / "state.db"
            ) as clone:
                self.assertEqual(
                    clone.execute("SELECT message FROM history").fetchone(),
                    ("committed WAL history",),
                )

    def test_export_rejects_known_secret_in_sqlite_without_corrupting_source(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("os.environ", {"RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": ""}),
        ):
            root = Path(directory)
            repository = FileRepository(root / "data", root / "profiles")
            profile = repository.profile_path("source")
            profile.mkdir(parents=True)
            (profile / "config.yaml").write_text("model: {}\n", encoding="utf-8")
            (profile / ".env").write_text("API_KEY=synthetic-database-secret\n")
            with sqlite3.connect(profile / "state.db") as connection:
                connection.execute("CREATE TABLE history (message TEXT)")
                connection.execute("INSERT INTO history VALUES ('synthetic-database-secret')")
            portability = PortabilityService(repository, root / "hermes")
            with self.assertRaises(StoreError):
                portability.export({"agent_ids": ["source"]})
            with sqlite3.connect(profile / "state.db") as connection:
                self.assertEqual(
                    connection.execute("SELECT message FROM history").fetchone(),
                    ("synthetic-database-secret",),
                )

    def test_export_rejects_invalid_sqlite_state_instead_of_packaging_it(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("os.environ", {"RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": ""}),
        ):
            root = Path(directory)
            repository = FileRepository(root / "data", root / "profiles")
            profile = repository.profile_path("source")
            profile.mkdir(parents=True)
            (profile / "config.yaml").write_text("model: {}\n", encoding="utf-8")
            (profile / "state.db").write_bytes(b"not a SQLite database")
            portability = PortabilityService(repository, root / "hermes")
            with self.assertRaises(StoreError):
                portability.export({"agent_ids": ["source"]})
            self.assertEqual((profile / "state.db").read_bytes(), b"not a SQLite database")

    def test_export_rejects_broken_foreign_key_state(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("os.environ", {"RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": ""}),
        ):
            root = Path(directory)
            repository = FileRepository(root / "data", root / "profiles")
            profile = repository.profile_path("source")
            profile.mkdir(parents=True)
            (profile / "config.yaml").write_text("model: {}\n", encoding="utf-8")
            with sqlite3.connect(profile / "state.db") as connection:
                connection.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
                connection.execute("CREATE TABLE child (parent_id INTEGER REFERENCES parent(id))")
                connection.execute("INSERT INTO child VALUES (123)")
            portability = PortabilityService(repository, root / "hermes")
            with self.assertRaises(StoreError):
                portability.export({"agent_ids": ["source"]})

    def test_import_rebinds_router_endpoint_to_receiver(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("os.environ", {"RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": ""}),
        ):
            root = Path(directory)
            repository = FileRepository(root / "data", root / "profiles")
            profile = repository.profile_path("source")
            profile.mkdir(parents=True)
            (profile / "config.yaml").write_text(
                "model:\n  default: auto\n  base_url: https://publisher.invalid/v1\n",
                encoding="utf-8",
            )
            portability = PortabilityService(repository, root / "hermes")
            payload, _ = portability.export({"agent_ids": ["source"]})
            report = portability.apply(payload)
            imported = repository.profile_path(report["agent_id_mappings"]["source"])
            import yaml

            from xnobrain.integrations.llm_router_support import LLM_ROUTER_API_BASE_URL

            config = yaml.safe_load((imported / "config.yaml").read_text())
            self.assertEqual(config["model"]["base_url"], LLM_ROUTER_API_BASE_URL)
            self.assertEqual(config["providers"]["xnobrain"]["api"], LLM_ROUTER_API_BASE_URL)
            self.assertNotIn("publisher.invalid", (imported / "config.yaml").read_text())

    def test_full_profile_archive_keeps_agent_guidance_skills_memory_and_workspace(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("os.environ", {"RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": ""}),
        ):
            root = Path(directory)
            repository = FileRepository(root / "data", root / "profiles")
            profile = repository.profile_path("source")
            profile.mkdir(parents=True)
            (profile / "config.yaml").write_text("model: {}\n", encoding="utf-8")
            files = {
                "HERMES.md": "Agent identity\n",
                "workspace/AGENTS.md": "Agent instructions\n",
                "workspace/notes.txt": "Workspace note\n",
                "skills/research/SKILL.md": "Research skill\n",
                "memories/MEMORY.md": "Remember this\n",
            }
            for name, content in files.items():
                source = profile / name
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text(content, encoding="utf-8")
            with sqlite3.connect(profile / "state.db") as database:
                database.execute("CREATE TABLE history (message TEXT)")
                database.execute("INSERT INTO history VALUES ('Conversation history')")
            portability = PortabilityService(repository, root / "hermes")
            payload, _ = portability.export({"agent_ids": ["source"]})
            with ZipFile(BytesIO(payload)) as archive:
                for name in files:
                    self.assertEqual(archive.read(f"profiles/source/{name}"), files[name].encode())
            result = portability.apply(payload)
            imported = repository.profile_path(result["agent_id_mappings"]["source"])
            for name, content in files.items():
                self.assertEqual((imported / name).read_text(encoding="utf-8"), content)
            with sqlite3.connect(imported / "state.db") as database:
                self.assertEqual(
                    database.execute("SELECT message FROM history").fetchone(),
                    ("Conversation history",),
                )
