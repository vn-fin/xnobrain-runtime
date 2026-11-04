"""Durable operation queue, worker fencing, and plugin lifecycle tests."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from xnobrain.integrations import extension_engine, extension_policy, extension_recovery
from xnobrain.repositories import FileRepository, StoreError
from xnobrain.repositories.custom_page_locks import ExecutionLease
from xnobrain.repositories.extension_operations import ExtensionOperationStore
from xnobrain.services.base import ServiceError
from xnobrain.services.extensions import ExtensionsService
from xnobrain.trusted_context import TrustedRequestContext


class FakeAgents:
    def __init__(self, profile: Path):
        self.profile = profile

    def profile_path(self, _agent_id: str) -> Path:
        return self.profile


class ExtensionOperationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        data = Path(self.temporary.name) / "data"
        profiles = data / "profiles"
        self.profile = profiles / "research"
        self.profile.mkdir(parents=True)
        self.repository = FileRepository(data, profiles)
        self.service = ExtensionsService(self.repository, FakeAgents(self.profile))
        self.trusted = TrustedRequestContext(subject="person-1", tenant_id="tenant-1")
        self.owner = "tenant-1\0person-1"

    def tearDown(self):
        self.temporary.cleanup()

    def queue(self, kind: str, *, plugin_id="sample", source=None, ref=None):
        body = {
            "kind": kind,
            "plugin_id": plugin_id,
            "idempotency_key": f"{kind}-{plugin_id}",
        }
        if source is not None:
            body["source"] = source
        if ref is not None:
            body["ref"] = ref
        self.service.create_operation("research", body, self.trusted)
        return self.service.store.claim_next()

    @staticmethod
    def clone_fixture(_source, _ref, stage, cancelled=lambda: False):
        stage.mkdir(parents=True)
        (stage / "plugin.yaml").write_text("name: sample\nversion: 1.0.0\n", encoding="utf-8")
        (stage / "__init__.py").write_text("def register(ctx):\n    pass\n", encoding="utf-8")
        return "a" * 40

    def test_queue_idempotency_cancellation_and_owner_scope(self):
        store = ExtensionOperationStore(Path(self.temporary.name) / "queue")
        operation, created = store.create(
            agent_id="research",
            owner=self.owner,
            kind="scan",
            inputs={"plugin_id": "sample"},
            idempotency_key="same-key",
        )
        replay, replay_created = store.create(
            agent_id="research",
            owner=self.owner,
            kind="scan",
            inputs={"plugin_id": "sample"},
            idempotency_key="same-key",
        )
        self.assertTrue(created)
        self.assertFalse(replay_created)
        self.assertEqual(operation["operation_id"], replay["operation_id"])
        with self.assertRaises(StoreError) as unauthorized:
            store.get("research", "tenant-2\0person-1", operation["operation_id"])
        self.assertEqual(unauthorized.exception.status, 404)
        cancelled = store.cancel("research", self.owner, operation["operation_id"])
        self.assertEqual(cancelled["status"], "CANCELLED")

    def test_second_worker_cannot_reconcile_live_worker(self):
        first = ExtensionOperationStore(self.repository.data_dir)
        first_worker = self.service.worker
        descriptor = first.acquire_worker()
        self.assertIsNotNone(descriptor)
        try:
            self.service.store.create(
                agent_id="research",
                owner=self.owner,
                kind="scan",
                inputs={"plugin_id": "sample"},
                idempotency_key="live-worker",
            )
            processing = self.service.store.claim_next()
            self.assertEqual(processing["status"], "PROCESSING")
            asyncio.run(first_worker.start())
            self.assertIsNone(first_worker._task)
            current = self.service.store.get("research", self.owner, processing["operation_id"])
            self.assertEqual(current["status"], "PROCESSING")
        finally:
            first.release_worker(descriptor)

    def test_restart_reconciles_processing_and_persists_retention_prune(self):
        store = ExtensionOperationStore(Path(self.temporary.name) / "queue")
        store.create(
            agent_id="research",
            owner=self.owner,
            kind="scan",
            inputs={"plugin_id": "sample"},
            idempotency_key="restart",
        )
        processing = store.claim_next()
        store.reconcile()
        recovered = store.get("research", self.owner, processing["operation_id"])
        self.assertEqual(recovered["status"], "FAILED")
        self.assertEqual(recovered["error"]["code"], "worker_restarted")

    def test_install_scans_publishes_disabled_and_requires_approval(self):
        operation = self.queue(
            "install", source="https://example.org/example/plugin.git", ref="main"
        )
        with patch.object(extension_engine, "clone_plugin", self.clone_fixture):
            self.service.worker._process(operation)

        result = self.service.get_operation("research", operation["operation_id"], self.trusted)
        self.assertEqual(result["status"], "COMPLETED")
        plugin = self.profile / "plugins" / "sample"
        self.assertTrue((plugin / "plugin.yaml").is_file())
        config = yaml.safe_load((self.profile / "config.yaml").read_text())
        self.assertIn("sample", config["plugins"]["disabled"])
        record = extension_policy.load_state(self.profile)["plugins"]["sample"]
        self.assertEqual(record["commit"], "a" * 40)
        self.assertFalse(record["approval"]["approved"])
        self.assertEqual(record["scan"]["status"], "COMPLETED")

    def test_scan_disables_plugin_and_invalidates_approval(self):
        plugin = self.profile / "plugins" / "sample"
        plugin.mkdir(parents=True)
        (plugin / "plugin.yaml").write_text("name: sample\n", encoding="utf-8")
        (plugin / "__init__.py").write_text("def register(ctx):\n    pass\n", encoding="utf-8")
        digest = extension_policy.tree_digest(plugin)
        (self.profile / "config.yaml").write_text(
            "plugins:\n  enabled: [sample]\n  disabled: []\n", encoding="utf-8"
        )
        extension_policy.save_state(
            self.profile,
            {
                "plugins": {
                    "sample": {
                        "artifact_digest": digest,
                        "scan": {"status": "COMPLETED", "verdict": "safe"},
                        "approval": {"approved": True, "artifact_digest": digest},
                        "revision": 2,
                    }
                },
                "hooks": {},
                "mcp": {},
                "revision": 2,
            },
        )
        operation = self.queue("scan")
        self.service.worker._process(operation)

        config = yaml.safe_load((self.profile / "config.yaml").read_text())
        record = extension_policy.load_state(self.profile)["plugins"]["sample"]
        self.assertIn("sample", config["plugins"]["disabled"])
        self.assertFalse(record["approval"]["approved"])
        self.assertEqual(record["revision"], 3)

    def test_update_creates_private_backup_and_revokes_previous_approval(self):
        plugin = self.profile / "plugins" / "sample"
        plugin.mkdir(parents=True)
        (plugin / "plugin.yaml").write_text("name: sample\n", encoding="utf-8")
        (plugin / "__init__.py").write_text("def old(ctx):\n    pass\n", encoding="utf-8")
        old_digest = extension_policy.tree_digest(plugin)
        (self.profile / "config.yaml").write_text(
            "plugins:\n  enabled: [sample]\n  disabled: []\n", encoding="utf-8"
        )
        extension_policy.save_state(
            self.profile,
            {
                "plugins": {
                    "sample": {
                        "source": "https://example.org/example/plugin.git",
                        "ref": "main",
                        "commit": "b" * 40,
                        "artifact_digest": old_digest,
                        "scan": {"status": "COMPLETED", "verdict": "safe"},
                        "approval": {"approved": True, "artifact_digest": old_digest},
                        "revision": 1,
                    }
                },
                "hooks": {},
                "mcp": {},
                "revision": 1,
            },
        )
        operation = self.queue("update")
        with patch.object(extension_engine, "clone_plugin", self.clone_fixture):
            self.service.worker._process(operation)

        record = extension_policy.load_state(self.profile)["plugins"]["sample"]
        self.assertEqual(record["revision"], 2)
        self.assertFalse(record["approval"]["approved"])
        self.assertNotEqual(record["artifact_digest"], old_digest)
        backup_dir = extension_policy.state_root(self.profile) / "backups" / "sample"
        self.assertTrue(any(backup_dir.glob("*.zip")))

    def test_publication_recovery_restores_old_tree_after_crash_between_renames(self):
        plugin_root = self.profile / "plugins"
        old_plugin = plugin_root / "sample"
        old_plugin.mkdir(parents=True)
        (old_plugin / "plugin.yaml").write_text("name: sample\n", encoding="utf-8")
        (old_plugin / "__init__.py").write_text("old = True\n", encoding="utf-8")
        old_digest = extension_policy.tree_digest(old_plugin)
        backup_path = plugin_root / ".sample.0123456789abcdef0123456789abcdef.rollback"
        os.replace(old_plugin, backup_path)

        replacement = plugin_root / ".sample.0123456789abcdef0123456789abcdef.stage"
        replacement.mkdir()
        (replacement / "plugin.yaml").write_text("name: sample\n", encoding="utf-8")
        (replacement / "__init__.py").write_text("new = True\n", encoding="utf-8")
        new_digest = extension_policy.tree_digest(replacement)
        intent = {
            "operation_id": "0123456789abcdef0123456789abcdef",
            "plugin_id": "sample",
            "phase": "old_moved",
            "had_old": True,
            "old_artifact_digest": old_digest,
            "new_artifact_digest": new_digest,
        }
        extension_recovery.write_publication(self.profile, intent)
        (self.profile / "config.yaml").write_text(
            "plugins:\n  enabled: [sample]\n  disabled: []\n", encoding="utf-8"
        )
        extension_policy.save_state(
            self.profile,
            {
                "plugins": {
                    "sample": {
                        "artifact_digest": old_digest,
                        "scan": {"status": "COMPLETED", "verdict": "safe"},
                        "approval": {"approved": True, "artifact_digest": old_digest},
                        "revision": 2,
                    }
                },
                "hooks": {},
                "mcp": {},
                "revision": 2,
            },
        )

        self.assertTrue(self.service._recover_publication("research", self.profile))

        recovered = self.profile / "plugins" / "sample"
        self.assertEqual(extension_policy.tree_digest(recovered), old_digest)
        self.assertFalse(backup_path.exists())
        self.assertFalse(replacement.exists())
        self.assertFalse(extension_recovery.publication_path(self.profile).exists())
        config = yaml.safe_load((self.profile / "config.yaml").read_text())
        self.assertIn("sample", config["plugins"]["disabled"])
        record = extension_policy.load_state(self.profile)["plugins"]["sample"]
        self.assertEqual(record["scan"]["status"], "REQUIRED")
        self.assertFalse(record["approval"]["approved"])

    def test_publication_recovery_preserves_last_new_artifact_but_revokes_trust(self):
        plugin = self.profile / "plugins" / "sample"
        plugin.mkdir(parents=True)
        (plugin / "plugin.yaml").write_text("name: sample\n", encoding="utf-8")
        (plugin / "__init__.py").write_text("def register(ctx):\n    pass\n", encoding="utf-8")
        digest = extension_policy.tree_digest(plugin)
        intent = {
            "operation_id": "fedcba9876543210fedcba9876543210",
            "plugin_id": "sample",
            "phase": "published",
            "had_old": False,
            "old_artifact_digest": "",
            "new_artifact_digest": digest,
        }
        extension_recovery.write_publication(self.profile, intent)

        self.service._recover_publication("research", self.profile)

        self.assertTrue(plugin.is_dir())
        record = extension_policy.load_state(self.profile)["plugins"]["sample"]
        self.assertEqual(record["artifact_digest"], digest)
        self.assertFalse(record["approval"]["approved"])
        self.assertEqual(record["scan"]["status"], "REQUIRED")
        config = yaml.safe_load((self.profile / "config.yaml").read_text())
        self.assertIn("sample", config["plugins"]["disabled"])

    def test_publication_recovery_rejects_incomplete_or_unknown_intents(self):
        base = {
            "operation_id": "0123456789abcdef0123456789abcdef",
            "plugin_id": "sample",
            "phase": "prepared",
            "had_old": False,
            "old_artifact_digest": "",
            "new_artifact_digest": "a" * 64,
        }
        for changes in (
            {"new_artifact_digest": ""},
            {"phase": "unknown"},
            {"phase": []},
        ):
            with self.subTest(changes=changes):
                intent = {**base, **changes}
                extension_recovery.publication_path(self.profile).parent.mkdir(
                    parents=True, exist_ok=True
                )
                extension_recovery.publication_path(self.profile).write_text(
                    json.dumps(intent), encoding="utf-8"
                )
                with self.assertRaises(extension_recovery.ExtensionRecoveryError):
                    extension_recovery.recover_publication(self.profile)
                extension_recovery.publication_path(self.profile).unlink()

    def test_queued_operation_rejects_recreated_profile_id(self):
        operation = self.queue("install", source="owner/repository", ref="stable")
        self.assertEqual(operation["inputs"]["source"], "https://github.com/owner/repository.git")
        old_profile = self.profile
        shutil.rmtree(old_profile)
        old_profile.mkdir(parents=True)

        with self.assertRaises(ServiceError) as error:
            self.service.worker._process(operation)

        self.assertEqual(error.exception.code, "profile_generation_conflict")
        self.assertFalse((self.profile / "plugins" / "sample").exists())

    def test_remove_protects_required_plugin_and_removes_user_plugin(self):
        required = self.queue("remove", plugin_id="xnobrain-runtime-updates")
        self.service.worker._process(required)
        required_result = self.service.get_operation(
            "research", required["operation_id"], self.trusted
        )
        self.assertEqual(required_result["status"], "BLOCKED")

        plugin = self.profile / "plugins" / "sample"
        plugin.mkdir(parents=True)
        (plugin / "plugin.yaml").write_text("name: sample\n", encoding="utf-8")
        operation = self.queue("remove")
        self.service.worker._process(operation)
        result = self.service.get_operation("research", operation["operation_id"], self.trusted)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertFalse(plugin.exists())

    def test_lifecycle_mutation_rejects_while_profile_is_running(self):
        plugin = self.profile / "plugins" / "sample"
        plugin.mkdir(parents=True)
        (plugin / "plugin.yaml").write_text("name: sample\n", encoding="utf-8")
        lease = ExecutionLease(self.repository.data_dir, "research")
        try:
            with self.assertRaises(ServiceError) as error:
                self.service.set_plugin_visibility(
                    "research",
                    "sample",
                    {"hidden": True, "expected_revision": 1},
                    self.trusted,
                )
            self.assertEqual(error.exception.code, "agent_busy")
        finally:
            lease.close()

    def test_organization_context_is_not_authorized_for_personal_extensions(self):
        trusted = TrustedRequestContext(
            subject="person-1", tenant_id="tenant-1", organization_id="org-1"
        )
        with self.assertRaises(ServiceError) as error:
            self.service.list_operations("research", trusted)
        self.assertEqual(error.exception.code, "permission_denied")


if __name__ == "__main__":
    unittest.main()
