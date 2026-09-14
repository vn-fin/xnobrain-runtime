"""Required storage cannot be unrelated, transient, missing or replaced."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xnobrain.repositories import FileRepository, StoreError
from xnobrain.repositories.storage_mount import StorageMountGuard


class StorageMountTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data = self.root / "data"
        self.environment = patch.dict(
            "os.environ", {"RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": str(self.root)}
        )
        self.environment.start()
        self.mounts = [
            (1, "0:1", Path("/"), "overlay", ["rw"]),
            (2, "8:1", self.root, "ext4", ["rw"]),
        ]
        self.reader = patch(
            "xnobrain.repositories.storage_mount.mount_table", side_effect=lambda: self.mounts
        )
        self.reader.start()

    def tearDown(self):
        self.reader.stop()
        self.environment.stop()
        self.temporary.cleanup()

    def test_valid_mount_allows_create_and_guard_rechecks_identity(self):
        files = FileRepository(self.data, self.data / "profiles")
        self.assertTrue(files.data_dir.is_dir())
        files.storage_mount.check()
        self.mounts[-1] = (3, "8:1", self.root, "ext4", ["rw"])
        with self.assertRaises(StoreError):
            files.storage_mount.check()

    def test_missing_mount_never_allocates_fallback_directories(self):
        self.mounts.pop()
        with self.assertRaises(StoreError):
            FileRepository(self.data, self.data / "profiles")
        self.assertFalse(self.data.exists())

    def test_unrelated_anchor_and_symlink_are_not_authority(self):
        for anchor in ("/", str(self.root / "missing"), "relative/path"):
            with patch.dict("os.environ", {"RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": anchor}):
                with self.assertRaises(StoreError):
                    StorageMountGuard(self.data)
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(StoreError):
            StorageMountGuard(alias / "data")
        self.assertFalse(self.data.exists())

    def test_transient_network_unknown_readonly_and_nested_mounts_are_denied(self):
        for filesystem in ("tmpfs", "overlay", "nfs", "nfs4", "cifs", "fuse.sshfs", "unknown"):
            self.mounts[-1] = (2, "8:1", self.root, filesystem, ["rw"])
            with self.assertRaises(StoreError):
                FileRepository(self.data, self.data / "profiles")
        self.mounts[-1] = (2, "8:1", self.root, "ext4", ["ro"])
        with self.assertRaises(StoreError):
            StorageMountGuard(self.data)
        self.mounts[-1] = (2, "8:1", self.root, "ext4", ["rw"])
        self.mounts.append((3, "0:2", self.data / "agent-apps", "tmpfs", ["rw"]))
        with self.assertRaises(StoreError):
            FileRepository(self.data, self.data / "profiles")
        self.assertFalse(self.data.exists())

    def test_data_outside_verified_mount_is_rejected(self):
        sibling = self.root.parent / (self.root.name + "-outside")
        with self.assertRaises(StoreError):
            FileRepository(sibling, sibling / "profiles")
        self.assertFalse(sibling.exists())

    def test_cached_page_service_denies_writes_after_mount_disappears(self):
        from types import SimpleNamespace

        from xnobrain.services.custom_page import CustomPageService
        from xnobrain.tests.test_custom_page import news_manifest
        from xnobrain.trusted_context import TrustedRequestContext

        files = FileRepository(self.data, self.data / "profiles")
        (files.profiles_root / "research").mkdir()
        pages = CustomPageService(SimpleNamespace(repository=files))
        owner = TrustedRequestContext("owner", "tenant")
        pages.prepare(
            "research",
            {"manifest": news_manifest(), "expected_revision": 0, "idempotency_key": "mount-check"},
            owner,
        )
        path = self.data / "agent-apps" / "research" / "app.sqlite3"
        before = path.read_bytes()
        self.mounts.pop()
        with self.assertRaises(StoreError):
            pages.prepare(
                "research",
                {
                    "manifest": news_manifest(),
                    "expected_revision": 0,
                    "idempotency_key": "mount-second",
                },
                owner,
            )
        self.assertEqual(path.read_bytes(), before)

    def test_explicitly_unconfigured_native_layout_preserves_existing_behavior(self):
        with patch.dict("os.environ", {"RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": ""}):
            files = FileRepository(self.data, self.data / "profiles")
            self.assertIsNone(files.storage_mount.anchor)
