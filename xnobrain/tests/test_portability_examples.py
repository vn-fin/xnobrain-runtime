"""Clean examples share integrity checks and never publish during validation."""

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from xnobrain.repositories.base import StoreError
from xnobrain.repositories.files import FileRepository
from xnobrain.services.portability import PortabilityService
from xnobrain.services.portability_examples import start_example_upload, validate_example_upload


class ExampleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.enterContext(patch.dict("os.environ", {"RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": ""}))
        self.repo = FileRepository(self.root / "data", self.root / "profiles")
        self.service = PortabilityService(self.repo, self.root / "home")

    def upload(self, extra=None, manifest_extra=None):
        manifest = {
            "format": "xnobrain-bundle",
            "export_id": "synthetic-example",
            "version": 1,
            "agents": [{"id": "starter"}],
            "teams": [],
        }
        manifest.update(manifest_extra or {})
        files = {
            "manifest.json": json.dumps(manifest).encode(),
            "profiles/starter/config.yaml": b"model: {}\n",
            "profiles/starter/SOUL.md": b"You help organize a competition.\n",
            "profiles/starter/skills/plan/SKILL.md": b"---\nname: plan\n---\nMake a plan.\n",
        }
        files.update(extra or {})
        checksums = {
            name: {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
            for name, data in files.items()
        }
        path = self.root / "example.zip"
        with ZipFile(path, "w", ZIP_DEFLATED) as archive:
            for name, data in files.items():
                archive.writestr(name, data)
            archive.writestr("checksums.json", json.dumps(checksums))
        payload = path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        upload = start_example_upload(self.service, {"size": len(payload), "sha256": digest})
        chunk = upload["chunk_size"]
        for number in range(upload["total_parts"]):
            self.service.put_upload_part(
                upload["upload_id"], number, payload[number * chunk : (number + 1) * chunk]
            )
        self.service.complete_upload(upload["upload_id"], {})
        return upload["upload_id"]

    def history(self, *, content="A complete example conversation.", private=False):
        path = self.root / "history.db"
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, user_id TEXT, "
                "profile_name TEXT, title TEXT, session_key TEXT, model_config TEXT)"
            )
            connection.execute(
                "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, content TEXT, "
                "platform_message_id TEXT, "
                "FOREIGN KEY (session_id) REFERENCES sessions(id))"
            )
            connection.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("chat-1", "publisher", "owner-1", "starter", "Example", "private-route", "{}"),
            )
            connection.execute(
                "INSERT INTO messages VALUES (?, ?, ?, ?)",
                (1, "chat-1", content, "publisher-message"),
            )
            if private:
                connection.execute("CREATE TABLE credentials (name TEXT)")
                connection.execute("INSERT INTO credentials VALUES ('publisher')")
        payload = path.read_bytes()
        path.unlink()
        return payload

    def test_clean_example_validation_does_not_create_profile(self):
        result = validate_example_upload(self.service, self.upload())
        self.assertEqual(result["profile_id"], "starter")
        self.assertEqual(result["skills"], ["plan"])
        self.assertEqual(result["capability"], "profile-example-v1")
        self.assertFalse(self.repo.profile_path("starter").exists())

    def test_reviewed_example_history_is_visible_after_import(self):
        from xnobrain.services.portability_tasks import PortabilityTasks

        upload = self.upload({"profiles/starter/state.db": self.history()})
        validation = validate_example_upload(self.service, upload)
        self.assertEqual(validation["conversation_count"], 1)
        self.assertEqual(validation["message_count"], 1)
        tasks = PortabilityTasks(self.service)
        tasks.create_import(
            {"upload_id": upload}, scope="example", actor="participant", key="history"
        )
        result = tasks.execute(tasks.store.claim("worker"))
        target_id = result["agent_id_mappings"]["starter"]
        database = self.repo.profile_path(target_id) / "state.db"
        with sqlite3.connect(database) as connection:
            session = connection.execute(
                "SELECT source, user_id, profile_name, session_key, model_config "
                "FROM sessions WHERE id = 'chat-1'"
            ).fetchone()
            message = connection.execute(
                "SELECT content, platform_message_id FROM messages WHERE session_id = 'chat-1'"
            ).fetchone()
        self.assertEqual(session, ("imported", None, target_id, None, None))
        self.assertEqual(message, ("A complete example conversation.", None))

    def test_example_history_rejects_credentials_and_auxiliary_state(self):
        for payload in (
            self.history(content="Bearer " + "a" * 32),
            self.history(private=True),
        ):
            with self.subTest(payload=hashlib.sha256(payload).hexdigest()[:8]):
                upload = self.upload({"profiles/starter/state.db": payload})
                with self.assertRaises(StoreError):
                    validate_example_upload(self.service, upload)

    def test_model_token_limits_are_not_credentials(self):
        upload = self.upload({"profiles/starter/config.yaml": b"model:\n  max_tokens: 2048\n"})
        validate_example_upload(self.service, upload)

    def test_categorized_skills_validate_and_import_with_their_assets(self):
        from xnobrain.services.portability_tasks import PortabilityTasks

        skill = "research/financial-models/forecast"
        asset = f"skills/{skill}/references/guide.md"
        upload = self.upload(
            {
                f"profiles/starter/skills/{skill}/SKILL.md": b"Make a forecast.\n",
                f"profiles/starter/{asset}": b"Review the assumptions.\n",
            }
        )
        result = validate_example_upload(self.service, upload)
        self.assertEqual(result["skills"], ["plan", skill])
        tasks = PortabilityTasks(self.service)
        tasks.create_import(
            {"upload_id": upload}, scope="example", actor="participant", key="nested"
        )
        result = tasks.execute(tasks.store.claim("worker"))
        imported = self.repo.profile_path(result["agent_id_mappings"]["starter"])
        self.assertEqual((imported / asset).read_bytes(), b"Review the assumptions.\n")

    def test_nested_skill_does_not_authorize_category_or_sibling_files(self):
        for name in [
            "research/unowned.py",
            "research/forecast-other/main.py",
            "research/forecast/nested/.env",
        ]:
            with self.subTest(name=name):
                upload = self.upload(
                    {
                        "profiles/starter/skills/research/forecast/SKILL.md": b"Forecast.\n",
                        f"profiles/starter/skills/{name}": b"not portable",
                    }
                )
                with self.assertRaises(StoreError):
                    validate_example_upload(self.service, upload)

    def test_storage_is_reserved_before_transfer(self):
        from types import SimpleNamespace

        free = SimpleNamespace(free=256 * 1024 * 1024 + 3072)
        with patch("xnobrain.services.portability_examples.shutil.disk_usage", return_value=free):
            upload = start_example_upload(self.service, {"size": 1024})
            self.assertGreater(upload["example_reserved_bytes"], 1024)
            with self.assertRaisesRegex(StoreError, "staging space"):
                start_example_upload(self.service, {"size": 1024})
            self.service.delete_transfer("upload", upload["upload_id"])
            start_example_upload(self.service, {"size": 1024})

    def test_importer_provenance_is_required_for_publication(self):
        with self.assertRaises(StoreError):
            validate_example_upload(self.service, self.upload(manifest_extra={"export_id": None}))

    def test_validated_example_runs_through_existing_import_worker(self):
        from xnobrain.services.portability_tasks import PortabilityTasks

        upload = self.upload()
        validate_example_upload(self.service, upload)
        tasks = PortabilityTasks(self.service)
        tasks.create_import({"upload_id": upload}, scope="example", actor="participant", key="copy")
        claim = tasks.store.claim("worker")
        result = tasks.execute(claim)
        self.assertIn("starter", result["agent_id_mappings"])

    def test_private_content_rejected_even_with_correct_checksums(self):
        for name in [
            ".env",
            "state.db",
            "logs/run.txt",
            "workspace/auth.json",
            "workspace/.ssh/key",
            "memories/MEMORY.md",
            ".portability-owner.json",
        ]:
            with self.subTest(name=name):
                upload = self.upload({f"profiles/starter/{name}": b"private"})
                with self.assertRaises(StoreError):
                    validate_example_upload(self.service, upload)

    def test_credential_values_rejected_and_environment_references_allowed(self):
        upload = self.upload({"profiles/starter/config.yaml": b"model:\n  api_key: secret-value\n"})
        with self.assertRaisesRegex(StoreError, "credentials"):
            validate_example_upload(self.service, upload)
        upload = self.upload(
            {"profiles/starter/config.yaml": b"model:\n  api_key: ${PROVIDER_KEY}\n"}
        )
        validate_example_upload(self.service, upload)

    def test_clean_policy_requires_declared_skill_and_no_teams(self):
        for extra, manifest in [
            ({"profiles/starter/skills/undeclared/main.py": b"pass"}, {}),
            ({}, {"teams": [{"id": "team"}]}),
        ]:
            upload = self.upload(extra, manifest)
            with self.assertRaises(StoreError):
                validate_example_upload(self.service, upload)

    def test_available_storage_checked_before_hashing(self):
        from types import SimpleNamespace

        upload = self.upload()
        with (
            patch(
                "xnobrain.services.portability.shutil.disk_usage",
                return_value=SimpleNamespace(free=1),
            ),
            patch.object(self.service, "_hash_member") as hash_member,
        ):
            with self.assertRaisesRegex(StoreError, "staging space"):
                validate_example_upload(self.service, upload)
            hash_member.assert_not_called()

    def test_example_transfer_validation_and_import_do_not_apply_byte_caps(self):
        from xnobrain.services.portability_tasks import PortabilityTasks

        # Reduce ordinary caps to exercise every former byte guard without
        # allocating a multi-gigabyte fixture. Real content/checksums still flow
        # through chunk completion, validation and the durable import worker.
        with (
            patch("xnobrain.services.portability.MAX_COMPRESSED", 1),
            patch("xnobrain.services.portability.MAX_EXPANDED", 1),
            patch("xnobrain.services.portability.MAX_FILE_BYTES", 1),
        ):
            upload = self.upload({"profiles/starter/workspace/reference.txt": b"reference" * 1024})
            validate_example_upload(self.service, upload)
            tasks = PortabilityTasks(self.service)
            tasks.create_import(
                {"upload_id": upload}, scope="example", actor="participant", key="large"
            )
            result = tasks.execute(tasks.store.claim("worker"))
            imported = self.repo.profile_path(result["agent_id_mappings"]["starter"])
            self.assertEqual(
                (imported / "workspace/reference.txt").read_bytes(), b"reference" * 1024
            )
            with self.assertRaises(StoreError):
                self.service.start_upload({"size": 2})

    def test_large_example_admission_depends_on_available_storage(self):
        from types import SimpleNamespace

        size = 3 * 1024 * 1024 * 1024
        with patch(
            "xnobrain.services.portability_examples.shutil.disk_usage",
            return_value=SimpleNamespace(free=8 * size),
        ):
            upload = start_example_upload(self.service, {"size": size})
        self.assertEqual(upload["size"], size)
        self.assertEqual(upload["purpose"], "profile-example")

    def test_completed_upload_digest_is_verified(self):
        upload = self.upload()
        directory = self.service.upload_root / upload
        with (directory / "bundle.zip").open("ab") as output:
            output.write(b"changed")
        with self.assertRaisesRegex(StoreError, "changed"):
            validate_example_upload(self.service, upload)

    def test_disabled_feature_removes_only_example_routes(self):
        from xnobrain.routes.setup import route_groups

        with patch.dict("os.environ", {"FT_ENABLE_PROFILE_EXAMPLES": "invalid"}):
            operations = {route.operation for group in route_groups() for route in group}
        self.assertNotIn("bundle_example_capabilities", operations)
        self.assertIn("bundle_example_validation", operations)
        self.assertIn("bundle_upload_start", operations)
