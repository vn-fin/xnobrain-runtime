"""Organization agent copies exclude personal state and preserve destination history."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

from xnobrain.repositories.base import StoreError
from xnobrain.repositories.files import FileRepository
from xnobrain.services.portability import PortabilityService
from xnobrain.tests.test_portability_state_snapshot import PUBLISHER, shareable_profile
from xnobrain.trusted_context import TrustedRequestContext


class OrganizationWorkspaceSnapshotTests(unittest.TestCase):
    def test_checked_refresh_detects_destination_changes_and_recovers_receipt(self):
        from xnobrain.services.portability_organization import (
            inspect_organization_profile,
            organization_refresh_receipt,
        )

        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "xnobrain.integrations.llm_router_support.LLM_ROUTER_API_BASE_URL",
                "http://router/v1",
            ),
        ):
            root = Path(directory)
            repository = FileRepository(root / "data", root / "profiles")
            source = repository.profile_path("source")
            source.mkdir(parents=True)
            shareable_profile(repository, source)
            (source / "config.yaml").write_text("model: {}\n")
            service = PortabilityService(repository, root / "hermes")
            archive = root / "copy.zip"
            recipient = "manager\x00organization-test\x00org_test"
            service.export_shareable("source", archive, owner=PUBLISHER, organization=True)
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            result = service.apply_shareable_file(
                archive,
                operation_id="initial",
                digest=digest,
                recipient=recipient,
                organization=True,
            )
            target_id = result["agent_id_mappings"]["source"]
            target = repository.profile_path(target_id)
            inspection = inspect_organization_profile(service, target_id, recipient)
            self.assertTrue(inspection["baseline_known"])
            self.assertFalse(inspection["locally_modified"])
            (target / "state.db").write_bytes(b"company history")
            self.assertEqual(
                inspect_organization_profile(service, target_id, recipient)["fingerprint"],
                inspection["fingerprint"],
            )
            service.agent_manager = SimpleNamespace(
                _registry_lock=RLock(), active_agent_ids=lambda: {target_id}
            )
            service.active_kanban_agents = set
            profiles_before = set((root / "profiles").iterdir())
            with self.assertRaises(StoreError) as busy:
                service.apply_shareable_file(
                    archive,
                    operation_id="busy",
                    digest=digest,
                    recipient=recipient,
                    organization=True,
                    refresh_target=target_id,
                    expected_fingerprint=inspection["fingerprint"],
                )
            self.assertEqual(busy.exception.code, "agent_busy")
            self.assertEqual(set((root / "profiles").iterdir()), profiles_before)
            service.agent_manager.active_agent_ids = set
            (target / "HERMES.md").write_text("local change")
            self.assertTrue(
                inspect_organization_profile(service, target_id, recipient)["locally_modified"]
            )
            with self.assertRaises(StoreError) as caught:
                service.apply_shareable_file(
                    archive,
                    operation_id="conflict",
                    digest=digest,
                    recipient=recipient,
                    organization=True,
                    refresh_target=target_id,
                    expected_fingerprint=inspection["fingerprint"],
                )
            self.assertEqual(caught.exception.code, "target_changed")
            self.assertEqual((target / "HERMES.md").read_text(), "local change")
            fingerprint = inspect_organization_profile(service, target_id, recipient)["fingerprint"]
            service.apply_shareable_file(
                archive,
                operation_id="checked",
                digest=digest,
                recipient=recipient,
                organization=True,
                refresh_target=target_id,
                expected_fingerprint=fingerprint,
            )
            self.assertEqual((target / "state.db").read_bytes(), b"company history")
            self.assertFalse(
                inspect_organization_profile(service, target_id, recipient)["locally_modified"]
            )
            receipt = organization_refresh_receipt(service, target_id, recipient, "checked", digest)
            self.assertEqual(receipt["state"], "completed")
            self.assertEqual(receipt["agent_id_mappings"], {"source": target_id})
            (target / "HERMES.md").write_text("edit after successful delivery")
            service.apply_shareable_file(
                archive,
                operation_id="checked",
                digest=digest,
                recipient=recipient,
                organization=True,
                refresh_target=target_id,
                expected_fingerprint=fingerprint,
            )
            self.assertEqual((target / "HERMES.md").read_text(), "edit after successful delivery")
            with self.assertRaises(StoreError):
                inspect_organization_profile(service, target_id, "manager\x00other\x00org_other")

    def test_copy_and_refresh_keep_only_organization_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = FileRepository(root / "data", root / "profiles")
            source = repository.profile_path("source")
            source.mkdir(parents=True)
            shareable_profile(repository, source)
            (source / "config.yaml").write_text("model: {}\n")
            (source / "memories").mkdir()
            (source / "memories" / "MEMORY.md").write_text("personal memory")
            (source / "sessions").mkdir()
            (source / "sessions" / "private.json").write_text("personal session")
            (source / "state.db").write_bytes(b"personal history")
            (source / ".env").write_text("API_KEY=private-credential\n")
            (source / "skills" / "writer").mkdir(parents=True)
            (source / "skills" / "writer" / "SKILL.md").write_text("write well")
            service = PortabilityService(repository, root / "hermes")
            archive = root / "copy.zip"
            service.export_shareable("source", archive, owner=PUBLISHER, organization=True)
            with ZipFile(archive) as bundle:
                names = bundle.namelist()
                self.assertFalse(any("memories/" in name or "sessions/" in name for name in names))
                self.assertNotIn("profiles/source/state.db", names)
                self.assertNotIn("profiles/source/.env", names)
                self.assertIn("profiles/source/skills/writer/SKILL.md", names)
                self.assertEqual(
                    json.loads(bundle.read("manifest.json"))["snapshot_kind"],
                    "organization-agent-v1",
                )
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            with patch(
                "xnobrain.integrations.llm_router_support.LLM_ROUTER_API_BASE_URL",
                "http://router/v1",
            ):
                report = service.apply_shareable_file(
                    archive,
                    operation_id="pull-1",
                    digest=digest,
                    recipient="org\x00tenant\x00org_test",
                    organization=True,
                )
                target_id = report["agent_id_mappings"]["source"]
                target = repository.profile_path(target_id)
                self.assertNotEqual(target_id, "source")
                self.assertFalse((target / "state.db").exists())
                self.assertFalse((target / ".env").exists())
                (target / "memories").mkdir()
                (target / "memories" / "MEMORY.md").write_text("organization memory")
                (target / "state.db").write_bytes(b"organization history")
                (source / "skills" / "writer" / "SKILL.md").write_text("updated skill")
                service.export_shareable("source", archive, owner=PUBLISHER, organization=True)
                digest = hashlib.sha256(archive.read_bytes()).hexdigest()
                service.apply_shareable_file(
                    archive,
                    operation_id="pull-2",
                    digest=digest,
                    recipient="org\x00tenant\x00org_test",
                    organization=True,
                    refresh_target=target_id,
                )
                self.assertEqual((target / "state.db").read_bytes(), b"organization history")
                self.assertEqual(
                    (target / "memories" / "MEMORY.md").read_text(), "organization memory"
                )
                self.assertEqual(
                    (target / "skills" / "writer" / "SKILL.md").read_text(), "updated skill"
                )
                self.assertEqual((source / "state.db").read_bytes(), b"personal history")
                (target / "skills" / "writer" / "SKILL.md").write_text("organization edit")
                service.apply_shareable_file(
                    archive,
                    operation_id="pull-2",
                    digest=digest,
                    recipient="org\x00tenant\x00org_test",
                    organization=True,
                    refresh_target=target_id,
                )
                self.assertEqual(
                    (target / "skills" / "writer" / "SKILL.md").read_text(), "organization edit"
                )

    def test_organization_snapshot_intent_is_bound_to_path(self):
        from xnobrain.trusted_context import snapshot_intent

        prefix = "/xnobrain/api/runtime/v1/community/snapshots/organization/"
        self.assertEqual(snapshot_intent("POST", prefix + "source/export"), "export")
        self.assertEqual(snapshot_intent("POST", prefix + "import"), "import")
        self.assertEqual(snapshot_intent("POST", prefix + "target/refresh"), "import")
        self.assertEqual(snapshot_intent("POST", prefix + "other/target/refresh"), "")
        self.assertEqual(snapshot_intent("POST", prefix + "target/inspect"), "export")
        self.assertEqual(
            snapshot_intent("POST", prefix + "target/refresh-checked/" + "a" * 64), "import"
        )
        self.assertEqual(snapshot_intent("POST", prefix + "target/refresh-checked/bad"), "")
        self.assertEqual(snapshot_intent("GET", prefix + "target/receipts/job"), "receipt")
        self.assertEqual(snapshot_intent("POST", prefix + "target/receipts/job"), "")

    def test_export_redacts_known_runtime_credentials_in_workspace_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = FileRepository(root / "data", root / "profiles")
            source = repository.profile_path("source")
            source.mkdir(parents=True)
            shareable_profile(repository, source)
            (source / "config.yaml").write_text("model: {}\n")
            (source / "workspace").mkdir(exist_ok=True)
            (source / "workspace" / "notes.txt").write_text("runtime-secret-12345")
            service = PortabilityService(repository, root / "hermes")
            with patch.object(
                service, "_known_secret_values", return_value={b"runtime-secret-12345"}
            ):
                service.export_shareable(
                    "source", root / "copy.zip", owner=PUBLISHER, organization=True
                )
                with ZipFile(root / "copy.zip") as archive:
                    self.assertNotIn(
                        b"runtime-secret-12345", archive.read("profiles/source/workspace/notes.txt")
                    )

    def test_organization_export_ignores_the_owner_identity_organization(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = FileRepository(root / "data", root / "profiles")
            source = repository.profile_path("source")
            source.mkdir(parents=True)
            shareable_profile(repository, source)
            (source / "config.yaml").write_text("model: {}\n")
            repository.atomic_json(
                source / ".community-profile-owner.json",
                {"subject": "publisher", "tenant_id": "tenant", "organization_id": "XNOQuant"},
            )
            service = PortabilityService(repository, root / "hermes")
            # The organization grant authorizes the pull; the owner must still match.
            service.export_shareable(
                "source", root / "copy.zip", owner=PUBLISHER, organization=True
            )
            with self.assertRaises(StoreError):
                service.export_shareable("source", root / "copy.zip", owner=PUBLISHER)
            stranger = TrustedRequestContext(
                subject="stranger", tenant_id="tenant", organization_id=""
            )
            with self.assertRaises(StoreError):
                service.export_shareable(
                    "source", root / "copy.zip", owner=stranger, organization=True
                )
