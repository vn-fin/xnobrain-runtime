"""Rejected storage entries expose bounded metadata without following targets."""

import os
import tempfile
import unittest
from pathlib import Path

from xnobrain.integrations.runtime_update_storage import (
    RuntimeUpdateStorage,
    RuntimeUpdateStorageError,
)


class RuntimeStorageDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.data, self.profiles, self.profile = (
            root / name for name in ("data", "profiles", "root")
        )
        for path in (self.data, self.profiles, self.profile):
            path.mkdir()
        self.storage = RuntimeUpdateStorage(self.data, self.profiles, self.profile)

    def test_sidecar_symlink_reports_relative_entry_not_external_target(self):
        (self.profile / "bad\nstate.db").write_bytes(b"")
        (self.profile / "bad\nstate.db-wal").symlink_to("/PRIVATE_EXTERNAL_TARGET")
        with self.assertRaises(RuntimeUpdateStorageError) as caught:
            self.storage.flush_sqlite()
        self.assertEqual(
            caught.exception.diagnostics,
            {
                "storage_check": "sqlite_sidecar_symlink",
                "storage_root": "root_profile",
                "relative_path": "bad?state.db-wal",
            },
        )
        self.assertNotIn("PRIVATE", str(caught.exception.diagnostics))
        self.assertNotIn(str(self.profile), str(caught.exception.diagnostics))

    def test_legacy_manifest_rejects_fifo_without_reading_and_identified(self):
        os.mkfifo(self.data / "pipe")
        with self.assertRaises(RuntimeUpdateStorageError) as caught:
            self.storage.manifest(version=2)
        self.assertEqual(caught.exception.code, "runtime_update_unsafe_data")
        self.assertEqual(caught.exception.diagnostics["storage_check"], "file_type")
        self.assertEqual(caught.exception.diagnostics["relative_path"], "pipe")

    def test_rejected_path_is_bounded(self):
        directory = self.profiles / ("a" * 200) / ("b" * 200)
        directory.mkdir(parents=True)
        os.mkfifo(directory / ("c" * 200))
        with self.assertRaises(RuntimeUpdateStorageError) as caught:
            self.storage.manifest(version=2)
        self.assertLessEqual(len(caught.exception.diagnostics["relative_path"]), 512)
