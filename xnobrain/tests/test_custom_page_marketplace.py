"""Community definitions never carry app state; installed writer lifecycle is fenced."""

import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from xnobrain.models.marketplace import MarketplaceExportPackage
from xnobrain.repositories import StoreError
from xnobrain.repositories.custom_page_locks import ExecutionLease
from xnobrain.services.base import ServiceError
from xnobrain.services.custom_page import CustomPageService
from xnobrain.services.marketplace import MarketplaceService
from xnobrain.services.portability import PortabilityService
from xnobrain.tests import test_marketplace as fixtures
from xnobrain.tests.test_custom_page import news_manifest
from xnobrain.trusted_context import TrustedRequestContext


class CommunityAppExportTests(unittest.TestCase):
    def setUp(self):
        fixtures.MarketplaceExportTests.setUp(self)
        self.owner = TrustedRequestContext("owner", "tenant")
        self.pages = CustomPageService(SimpleNamespace(repository=self.repo))
        draft = self.pages.prepare(
            "owned-agent",
            {
                "manifest": news_manifest(),
                "expected_revision": 0,
                "idempotency_key": "private-draft",
            },
            self.owner,
        )
        self.pages.write(
            "owned-agent",
            "articles",
            {
                "expected_revision": 1,
                "idempotency_key": "private-record",
                "records": [
                    {
                        "id": "source",
                        "values": {"title": "PRIVATE-APP-ROW"},
                        "provenance": {
                            "kind": "synthetic",
                            "source": "PRIVATE-SOURCE",
                            "collected_at": "2026-09-14T00:00:00Z",
                        },
                    }
                ],
            },
            self.owner,
        )
        self.pages.activate(
            "owned-agent",
            {
                "expected_revision": 0,
                "revision": 1,
                "digest": draft["digest"],
                "confirmation": "ACTIVATE " + draft["digest"],
            },
            self.owner,
        )
        self.pages.attachment(
            "owned-agent",
            {
                "filename": "private.txt",
                "content_base64": base64.b64encode(b"PRIVATE-ATTACHMENT").decode(),
                "idempotency_key": "private-attachment",
            },
            self.owner,
        )
        self.pages.backup("owned-agent", self.owner)

    def tearDown(self):
        self.tmp.cleanup()

    def test_community_omits_canonical_data_backups_receipts_and_runtime_copies(self):
        for relative in (
            "conversation-runs/session/run_private.json",
            ".xnobrain/conversation-contexts/private.json",
            "cron/jobs.json",
            "workspace/page-export.json",
            "skills/custom/research/assets/agent-apps/records.json",
            "skills/custom/research/references/conversation-runs/run_private.json",
            "skills/custom/research/references/cron/jobs.json",
            "skills/custom/research/assets/AGENT-APPS/backup.json",
            "skills/custom/research/assets/agent-apps/inner/SKILL.md",
        ):
            file = self.profile / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text('{"marker":"PRIVATE-RUNTIME-MARKER"}')
        package = self.service.export("owned-agent", "MIT")
        wire = json.dumps(package)
        for secret in (
            "PRIVATE-APP-ROW",
            "PRIVATE-SOURCE",
            "PRIVATE-ATTACHMENT",
            "PRIVATE-RUNTIME-MARKER",
        ):
            self.assertNotIn(secret, wire)
        self.assertEqual(len(package["files"]), 6)
        MarketplaceExportPackage.model_validate(package)
        self.assertEqual(
            self.pages.query("owned-agent", "latest", {"expected_revision": 1}, self.owner)["rows"][
                0
            ]["values"]["title"],
            "PRIVATE-APP-ROW",
        )

    def test_export_rejects_hardlinked_private_text_before_reading(self):
        private = self.repo.data_dir / "private-app-export.txt"
        private.write_text("PRIVATE-APP-TEXT")
        target = self.profile / "skills/custom/research/assets/innocent.txt"
        os.link(private, target)
        actual_read = os.read
        private_identity = private.stat()

        def checked_read(descriptor, size):
            opened = os.fstat(descriptor)
            self.assertNotEqual(
                (opened.st_dev, opened.st_ino), (private_identity.st_dev, private_identity.st_ino)
            )
            return actual_read(descriptor, size)

        with patch("xnobrain.services.marketplace.os.read", side_effect=checked_read) as read:
            with self.assertRaises(ServiceError) as caught:
                self.service.export("owned-agent", "MIT")
        self.assertEqual(caught.exception.code, "marketplace_export_rejected")
        self.assertNotIn("PRIVATE-APP-TEXT", str(caught.exception))
        self.assertTrue(read.called)  # Public files still traversed normally.
        self.assertEqual(private.read_text(), "PRIVATE-APP-TEXT")

    def test_full_profile_archive_is_distinct_but_excludes_canonical_app_store(self):
        from io import BytesIO
        from zipfile import ZipFile

        # DATA_DIR may be nested under the default root in self-hosted installs.
        with tempfile.TemporaryDirectory() as temporary:
            from xnobrain.repositories import FileRepository

            root = Path(temporary)
            profile = root / "root"
            profile.mkdir()
            (profile / "config.yaml").write_text("model: auto\n")
            (profile / "SOUL.md").write_text("Public root soul")
            (profile / "workspace").mkdir()
            (profile / "workspace/AGENTS.md").write_text("Public root instructions")
            files = FileRepository(profile / "xnobrain", profile / "profiles", root_profile=profile)
            pages = CustomPageService(SimpleNamespace(repository=files))
            pages.prepare(
                "big-brother",
                {
                    "manifest": news_manifest(),
                    "expected_revision": 0,
                    "idempotency_key": "root-draft",
                },
                self.owner,
            )
            pages.backup("big-brother", self.owner)
            community = MarketplaceService(
                files, SimpleNamespace(profile_path=lambda _agent: profile)
            )
            published = community.export("big-brother", "MIT")
            self.assertEqual(
                [item["path"] for item in published["files"]], ["SOUL.md", "workspace/AGENTS.md"]
            )
            self.assertNotIn("app.sqlite3", json.dumps(published))
            MarketplaceExportPackage.model_validate(published)
            archive, _ = PortabilityService(files, profile).export({"agent_ids": ["big-brother"]})
            with ZipFile(BytesIO(archive)) as bundle:
                self.assertFalse(
                    any(
                        "agent-apps/" in name or "backup.sqlite3" in name
                        for name in bundle.namelist()
                    )
                )
                self.assertIn("profiles/big-brother/config.yaml", bundle.namelist())


class CommunityPageLifecycleTests(unittest.TestCase):
    def setUp(self):
        fixtures.MarketplaceTests.setUp(self)
        self.package = fixtures.MarketplaceTests.package(self)
        self.owner = TrustedRequestContext("owner", "tenant")
        self.platform = SimpleNamespace(
            repository=self.repo, conversation_runs=SimpleNamespace(_active={})
        )
        self.pages = CustomPageService(self.platform)

    def tearDown(self):
        self.tmp.cleanup()

    def install_page(self):
        installed = self.s.install(self.package)
        self.agent = installed["local_profile_id"]
        self.assertFalse((self.repo.data_dir / "agent-apps" / self.agent).exists())
        self.pages.prepare(
            self.agent,
            {
                "manifest": news_manifest(),
                "expected_revision": 0,
                "idempotency_key": "private-page",
            },
            self.owner,
        )
        return installed

    def test_uninstall_refuses_private_page_until_separate_retention_resolution(self):
        self.install_page()
        for archived in (False, True):
            if archived:
                self.pages.archive(
                    self.agent,
                    {"expected_revision": 0, "confirmation": "ARCHIVE " + self.agent},
                    self.owner,
                )
            before = self.pages.read(self.agent, self.owner)
            with self.assertRaises((ServiceError, StoreError)) as caught:
                self.s.uninstall(self.agent)
            self.assertEqual(caught.exception.code, "custom_page_retention_required")
            self.assertTrue(self.repo.profile_path(self.agent).exists())
            self.assertEqual(self.pages.read(self.agent, self.owner), before)
        self.pages.delete(
            self.agent, {"expected_revision": 0, "confirmation": "DELETE " + self.agent}, self.owner
        )
        self.assertEqual(self.s.uninstall(self.agent)["status"], "uninstalled")

    def test_reinstall_does_not_attach_a_new_writer_to_retained_or_damaged_page(self):
        installed = self.install_page()
        self.repo.soft_delete_profile(self.agent)  # Simulated legacy uninstall, not the fixed API.
        with self.assertRaises((ServiceError, StoreError)) as caught:
            self.s.install(self.package)
        self.assertEqual(caught.exception.code, "custom_page_retention_required")
        self.assertFalse(self.repo.profile_path(installed["local_profile_id"]).exists())

    def test_update_and_uninstall_observe_execution_and_update_fences(self):
        installed = self.s.install(self.package)
        identifier = installed["local_profile_id"]
        lease = ExecutionLease(self.repo.data_dir, identifier)
        try:
            for action in (
                lambda: self.s.update({**self.package, "status": "updating"}, identifier),
                lambda: self.s.uninstall(identifier),
            ):
                with self.assertRaises(StoreError) as caught:
                    action()
                self.assertEqual(caught.exception.code, "custom_page_jobs_active")
        finally:
            lease.close()
        with patch(
            "xnobrain.repositories.runtime_update_gate.maintenance",
            return_value={"dispatch_paused": True},
        ):
            with self.assertRaises(StoreError) as caught:
                self.s.uninstall(identifier)
            self.assertEqual(caught.exception.code, "runtime_update_maintenance")
        self.assertTrue(self.repo.profile_path(identifier).exists())

    def test_definition_update_preserves_customer_app_exactly(self):
        self.install_page()
        before = self.pages.export(self.agent, self.owner)
        updated = {
            **self.package,
            "status": "updating",
            "definition": {**self.package["definition"], "soul": "Updated public instructions"},
        }
        updated["digest"] = self.s.digest(updated)
        self.s.update(updated, self.agent)
        self.assertEqual(self.pages.export(self.agent, self.owner), before)

    def test_corrupt_backup_only_and_symlink_stores_never_allow_uninstall(self):
        for name in ("backup-only", "corrupt", "symlink", "non-directory"):
            with self.subTest(store=name):
                package = {**self.package, "id": "inst_" + name}
                identifier = self.s.install(package)["local_profile_id"]
                app = self.repo.data_dir / "agent-apps" / identifier
                app.parent.mkdir(exist_ok=True)
                if name == "non-directory":
                    app.write_bytes(b"retained unknown state")
                elif name == "symlink":
                    outside = Path(self.tmp.name) / "external-app"
                    outside.mkdir()
                    app.symlink_to(outside, target_is_directory=True)
                else:
                    app.mkdir()
                    (
                        app / ("backup.sqlite3" if name == "backup-only" else "app.sqlite3")
                    ).write_bytes(b"damaged but retained")
                with self.assertRaises((ServiceError, StoreError)):
                    self.s.uninstall(identifier)
                self.assertTrue(self.repo.profile_path(identifier).is_dir())
                if name not in {"symlink", "non-directory"}:
                    self.assertEqual(len(list(app.iterdir())), 1)

    def test_failed_profile_publish_releases_lifecycle_fence(self):
        from xnobrain.repositories.custom_page_locks import lifecycle_gate

        original = os.replace

        def fail_publish(source, target):
            if Path(source).is_dir():
                raise OSError("synthetic failure")
            return original(source, target)

        with patch("xnobrain.services.marketplace.os.replace", side_effect=fail_publish):
            with self.assertRaises(OSError):
                self.s.install(self.package)
        identifier = "market-" + self.package["id"].removeprefix("inst_")
        with lifecycle_gate(self.repo.data_dir, identifier):
            pass
        self.assertFalse(self.repo.profile_path(identifier).exists())
        self.assertEqual(self.s.install(self.package)["local_profile_id"], identifier)


class CommunityAppRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixtures.MarketplaceExportAPITests.setUp(self)

    def tearDown(self):
        fixtures.MarketplaceExportAPITests.tearDown(self)

    async def test_uninstall_facade_conflict_preserves_profile_and_app(self):
        from xnobrain.trusted_context import principal_signature

        platform = self.composition.service
        package = fixtures.MarketplaceTests.package(SimpleNamespace(s=platform.marketplace))
        package["digest"] = MarketplaceService.digest(package)
        installed = platform.marketplace.install(package)
        identifier = installed["local_profile_id"]
        owner = TrustedRequestContext("synthetic-owner", "synthetic-tenant")
        page = platform.custom_page.prepare(
            identifier,
            {
                "manifest": news_manifest(),
                "expected_revision": 0,
                "idempotency_key": "api-page",
            },
            owner,
        )
        with patch.dict(os.environ, {"RUNTIME_INTERNAL_SERVICE_TOKEN": "synthetic"}):
            from httpx import ASGITransport, AsyncClient

            headers = {
                "x-xnobrain-verified-subject": owner.subject,
                "x-xnobrain-verified-tenant": owner.tenant_id,
                "x-xnobrain-principal-signature": principal_signature(
                    "synthetic", owner.subject, owner.tenant_id
                ),
            }
            async with AsyncClient(
                transport=ASGITransport(app=self.app), base_url="http://test", headers=headers
            ) as client:
                reply = await client.post(
                    "/xnobrain/api/runtime/v1/marketplace/uninstall",
                    json={"local_profile_id": identifier},
                )
                self.assertEqual(reply.status_code, 409, reply.text)
                self.assertEqual(reply.json()["error"]["code"], "custom_page_retention_required")
        self.assertTrue(platform.repository.live_profile_path(identifier).is_dir())
        self.assertEqual(
            platform.custom_page.preview(identifier, 1, owner)["digest"], page["digest"]
        )


def hold_installed_writer(root, agent, channel):
    lease = ExecutionLease(Path(root), agent)
    try:
        channel.send("locked")
        channel.recv()
    finally:
        lease.close()
        channel.close()


class CommunityPageRaceTests(unittest.TestCase):
    def setUp(self):
        CommunityPageLifecycleTests.setUp(self)

    def tearDown(self):
        CommunityPageLifecycleTests.tearDown(self)

    def test_independent_executor_process_blocks_update_and_uninstall(self):
        import multiprocessing

        identifier = self.s.install(self.package)["local_profile_id"]
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(
            target=hold_installed_writer, args=(str(self.repo.data_dir), identifier, child)
        )
        process.start()
        try:
            self.assertTrue(parent.poll(10))
            self.assertEqual(parent.recv(), "locked")
            for action in (
                lambda: self.s.update({**self.package, "status": "updating"}, identifier),
                lambda: self.s.uninstall(identifier),
            ):
                with self.assertRaises(StoreError) as caught:
                    action()
                self.assertEqual(caught.exception.code, "custom_page_jobs_active")
            parent.send("release")
            process.join(10)
            self.assertEqual(process.exitcode, 0)
        finally:
            if process.is_alive():
                process.terminate()
                process.join(10)
            parent.close()
            child.close()
        self.assertEqual(self.s.uninstall(identifier)["status"], "uninstalled")

    def test_uninstall_wins_racing_page_creation_without_orphan_data(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor

        identifier = self.s.install(self.package)["local_profile_id"]
        entered, release, attempted = threading.Event(), threading.Event(), threading.Event()
        original = self.repo.soft_delete_profile

        def wait_remove(agent):
            entered.set()
            self.assertTrue(release.wait(5))
            return original(agent)

        def create_page():
            attempted.set()
            return self.pages.prepare(
                identifier,
                {
                    "manifest": news_manifest(),
                    "expected_revision": 0,
                    "idempotency_key": "raced-page",
                },
                self.owner,
            )

        with (
            ThreadPoolExecutor(max_workers=2) as pool,
            patch.object(self.repo, "soft_delete_profile", side_effect=wait_remove),
        ):
            removing = pool.submit(self.s.uninstall, identifier)
            try:
                self.assertTrue(entered.wait(5))
                creating = pool.submit(create_page)
                self.assertTrue(attempted.wait(5))
            finally:
                release.set()
            self.assertEqual(removing.result(5)["status"], "uninstalled")
            with self.assertRaises(StoreError) as caught:
                creating.result(5)
            self.assertEqual(caught.exception.code, "agent_not_found")
        self.assertFalse((self.repo.data_dir / "agent-apps" / identifier).exists())

    def test_symlink_alias_cannot_lock_one_id_and_mutate_another_profile(self):
        identifier = self.s.install(self.package)["local_profile_id"]
        alias = self.repo.profiles_root / "market-alias"
        alias.symlink_to(self.repo.profile_path(identifier), target_is_directory=True)
        before = (self.repo.profile_path(identifier) / "SOUL.md").read_bytes()
        for action in (
            lambda: self.s.update({**self.package, "status": "updating"}, "market-alias"),
            lambda: self.s.uninstall("market-alias"),
        ):
            with self.assertRaises(ServiceError) as caught:
                action()
            self.assertEqual(caught.exception.code, "installation_profile_conflict")
        self.assertEqual((self.repo.profile_path(identifier) / "SOUL.md").read_bytes(), before)
        self.assertTrue(alias.is_symlink())
