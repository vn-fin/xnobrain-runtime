"""SQLite/WAL consistency of portable profile exports."""

import sqlite3
import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from xnobrain.repositories.base import StoreError
from xnobrain.repositories.files import FileRepository
from xnobrain.services.portability import PortabilityService
from xnobrain.trusted_context import TrustedRequestContext

PUBLISHER = TrustedRequestContext(subject="publisher", tenant_id="tenant", organization_id="")
RECEIVER = "consumer\x00tenant\x00"


def shareable_profile(repository: FileRepository, profile: Path) -> None:
    (profile / "HERMES.md").write_text("Agent guidance\n", encoding="utf-8")
    (profile / "workspace").mkdir(exist_ok=True)
    (profile / "workspace" / "AGENTS.md").write_text("Workspace guidance\n", encoding="utf-8")
    repository.atomic_json(
        profile / ".community-profile-owner.json", PortabilityService.owner_record(PUBLISHER)
    )


class StateSnapshotTests(unittest.TestCase):
    def test_root_coordinator_cannot_be_shared_even_with_matching_owner(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("os.environ", {"RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": ""}),
        ):
            root = Path(directory)
            repository = FileRepository(root / "data", root / "profiles")
            profile = repository.profile_path("big-brother")
            profile.mkdir(parents=True)
            shareable_profile(repository, profile)
            portability = PortabilityService(repository, root / "hermes")
            with self.assertRaises(StoreError) as denied:
                portability.export_shareable(
                    "big-brother", root / "private.zip", owner=PUBLISHER
                )
            self.assertEqual(denied.exception.status, 403)
            self.assertFalse((root / "private.zip").exists())

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
            shareable_profile(repository, profile)
            (profile / "config.yaml").write_text(
                "model:\n  default: auto\n  base_url: https://publisher.invalid/v1\n"
                "  assignment_id: publisher-assignment\n",
                encoding="utf-8",
            )
            with sqlite3.connect(profile / "state.db") as database:
                database.execute("CREATE TABLE history (message TEXT)")
            portability = PortabilityService(repository, root / "hermes")
            target = root / "shareable.zip"
            portability.export_shareable("source", target, owner=PUBLISHER)
            with ZipFile(target) as archive:
                exported = archive.read("profiles/source/config.yaml").decode()
                self.assertNotIn("publisher.invalid", exported)
                self.assertNotIn("publisher-assignment", exported)
            with patch(
                "xnobrain.integrations.llm_router_support.LLM_ROUTER_API_BASE_URL",
                "https://receiver.invalid/v1",
            ):
                report = portability.apply_shareable_file(
                    target, operation_id="clone-rebind", digest=portability._hash_file(target),
                    recipient=RECEIVER,
                )
            imported = repository.profile_path(report["agent_id_mappings"]["source"])
            import yaml

            config = yaml.safe_load((imported / "config.yaml").read_text())
            self.assertEqual(config["model"]["base_url"], "https://receiver.invalid/v1")
            self.assertEqual(config["providers"]["xnobrain"]["api"], "https://receiver.invalid/v1")
            self.assertNotIn("assignment_id", config["model"])
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

    def test_shareable_export_preserves_unfiltered_history_not_source_authority(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("os.environ", {"RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": ""}),
        ):
            root = Path(directory)
            repository = FileRepository(root / "data", root / "profiles")
            profile = repository.profile_path("source")
            profile.mkdir(parents=True)
            shareable_profile(repository, profile)
            (profile / "config.yaml").write_text("model: {}\n", encoding="utf-8")
            (profile / ".env").write_text("API_KEY=synthetic-database-secret\n")
            (profile / ".xnobrain" / "conversation-contexts").mkdir(parents=True)
            (profile / ".xnobrain" / "conversation-contexts" / "owner.json").write_text(
                '{"actor_user_id":"publisher"}', encoding="utf-8"
            )
            (profile / "conversation-runs" / "session").mkdir(parents=True)
            (profile / "conversation-runs" / "session" / "run.json").write_text(
                '{"actor_user_id":"publisher"}', encoding="utf-8"
            )
            (profile / "workspace" / "story.txt").write_text(
                "publisher content remains unchanged", encoding="utf-8"
            )
            with sqlite3.connect(profile / "state.db") as connection:
                connection.execute("CREATE TABLE history (message TEXT)")
                connection.execute("INSERT INTO history VALUES ('synthetic-database-secret')")
            portability = PortabilityService(repository, root / "hermes")
            target = root / "shareable.zip"
            metadata = portability.export_shareable("source", target, owner=PUBLISHER)
            self.assertEqual(metadata["sha256"], portability._hash_file(target))
            with ZipFile(target) as archive:
                names = archive.namelist()
                self.assertNotIn("profiles/source/.env", names)
                self.assertFalse(any("conversation-runs" in name for name in names))
                self.assertFalse(any(".xnobrain" in name for name in names))
                self.assertEqual(
                    archive.read("profiles/source/workspace/story.txt"),
                    b"publisher content remains unchanged",
                )
                snapshot = root / "snapshot.db"
                snapshot.write_bytes(archive.read("profiles/source/state.db"))
            with sqlite3.connect(snapshot) as connection:
                self.assertEqual(
                    connection.execute("SELECT message FROM history").fetchone(),
                    ("synthetic-database-secret",),
                )
            with patch(
                "xnobrain.integrations.llm_router_support.LLM_ROUTER_API_BASE_URL",
                "https://receiver.invalid/v1",
            ):
                report = portability.apply_shareable_file(
                    target, operation_id="clone-history", digest=metadata["sha256"], recipient=RECEIVER
                )
            self.assertNotEqual(report["agent_id_mappings"]["source"], "source")
            imported = repository.profile_path(report["agent_id_mappings"]["source"])
            self.assertFalse((imported / ".xnobrain").exists())
            with sqlite3.connect(imported / "state.db") as connection:
                self.assertEqual(
                    connection.execute("SELECT message FROM history").fetchone(),
                    ("synthetic-database-secret",),
                )

    def test_shareable_import_replays_operation_without_duplicate_profile(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("os.environ", {"RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": ""}),
        ):
            root = Path(directory)
            repository = FileRepository(root / "data", root / "profiles")
            source = repository.profile_path("source")
            source.mkdir(parents=True)
            shareable_profile(repository, source)
            (source / "config.yaml").write_text("model: {}\n", encoding="utf-8")
            with sqlite3.connect(source / "state.db") as database:
                database.execute("CREATE TABLE history (message TEXT)")
                database.execute("INSERT INTO history VALUES ('history survives replay')")
            portability = PortabilityService(repository, root / "hermes")
            target = root / "shareable.zip"
            metadata = portability.export_shareable("source", target, owner=PUBLISHER)
            with patch(
                "xnobrain.integrations.llm_router_support.LLM_ROUTER_API_BASE_URL",
                "https://receiver.invalid/v1",
            ):
                first = portability.apply_shareable_file(
                    target, operation_id="clone-operation-1", digest=metadata["sha256"], recipient=RECEIVER
                )
                replay = PortabilityService(repository, root / "hermes").apply_shareable_file(
                    target, operation_id="clone-operation-1", digest=metadata["sha256"], recipient=RECEIVER
                )
                self.assertEqual(first["agent_id_mappings"], replay["agent_id_mappings"])
                with self.assertRaisesRegex(StoreError, "digest mismatch"):
                    portability.apply_shareable_file(
                        target, operation_id="clone-operation-1", digest="0" * 64, recipient=RECEIVER
                    )
            imported = repository.profile_path(first["agent_id_mappings"]["source"])
            with sqlite3.connect(imported / "state.db") as database:
                self.assertEqual(
                    database.execute("SELECT message FROM history").fetchone(),
                    ("history survives replay",),
                )
            self.assertEqual(
                sorted(path.name for path in repository.profiles_root.iterdir()),
                [imported.name, "source"],
            )

    def test_shareable_import_requires_receiver_router(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("os.environ", {"RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": ""}),
        ):
            root = Path(directory)
            repository = FileRepository(root / "data", root / "profiles")
            profile = repository.profile_path("source")
            profile.mkdir(parents=True)
            shareable_profile(repository, profile)
            (profile / "config.yaml").write_text("model: {}\n", encoding="utf-8")
            with sqlite3.connect(profile / "state.db") as database:
                database.execute("CREATE TABLE history (message TEXT)")
            portability = PortabilityService(repository, root / "hermes")
            target = root / "shareable.zip"
            metadata = portability.export_shareable("source", target, owner=PUBLISHER)
            with patch("xnobrain.integrations.llm_router_support.LLM_ROUTER_API_BASE_URL", ""):
                with self.assertRaisesRegex(StoreError, "receiving Router URL"):
                    portability.apply_shareable_file(
                        target, operation_id="clone-no-router", digest=metadata["sha256"], recipient=RECEIVER
                    )
            self.assertEqual([path.name for path in repository.profiles_root.iterdir()], ["source"])

    def test_shareable_import_rejects_added_source_binding(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("os.environ", {"RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": ""}),
        ):
            root = Path(directory)
            repository = FileRepository(root / "data", root / "profiles")
            profile = repository.profile_path("source")
            profile.mkdir(parents=True)
            shareable_profile(repository, profile)
            (profile / "config.yaml").write_text("model: {}\n", encoding="utf-8")
            with sqlite3.connect(profile / "state.db") as database:
                database.execute("CREATE TABLE history (message TEXT)")
            portability = PortabilityService(repository, root / "hermes")
            target = root / "shareable.zip"
            metadata = portability.export_shareable("source", target, owner=PUBLISHER)
            with ZipFile(target, "a") as archive:
                archive.writestr(
                    "profiles/source/.xnobrain/conversation-contexts/owner.json",
                    '{"actor_user_id":"publisher"}',
                )
            with patch(
                "xnobrain.integrations.llm_router_support.LLM_ROUTER_API_BASE_URL",
                "https://receiver.invalid/v1",
            ):
                with self.assertRaises(StoreError):
                    portability.apply_shareable_file(
                        target, operation_id="clone-tampered", digest=metadata["sha256"], recipient=RECEIVER
                    )
            self.assertEqual([path.name for path in repository.profiles_root.iterdir()], ["source"])

    def test_shareable_export_rejects_external_symlink(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("os.environ", {"RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": ""}),
        ):
            root = Path(directory)
            repository = FileRepository(root / "data", root / "profiles")
            profile = repository.profile_path("source")
            profile.mkdir(parents=True)
            shareable_profile(repository, profile)
            (profile / "config.yaml").write_text("model: {}\n", encoding="utf-8")
            with sqlite3.connect(profile / "state.db") as database:
                database.execute("CREATE TABLE history (message TEXT)")
            outside = root / "outside.txt"
            outside.write_text("not owned by agent", encoding="utf-8")
            (profile / "workspace" / "outside.txt").symlink_to(outside)
            portability = PortabilityService(repository, root / "hermes")
            target = root / "shareable.zip"
            with self.assertRaisesRegex(StoreError, "symlink"):
                portability.export_shareable("source", target, owner=PUBLISHER)
            self.assertFalse(target.exists())
