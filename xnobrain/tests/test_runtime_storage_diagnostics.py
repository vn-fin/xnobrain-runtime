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

    def test_symlink_reports_relative_entry_not_external_target(self):
        link = self.profile / "bad\nlink"
        link.symlink_to("/PRIVATE_EXTERNAL_TARGET")
        with self.assertRaises(RuntimeUpdateStorageError) as caught:
            list(self.storage._walk_files(self.profile))
        self.assertEqual(
            caught.exception.diagnostics,
            {
                "storage_check": "symlink_scope",
                "storage_root": "root_profile",
                "relative_path": "bad?link",
            },
        )
        self.assertNotIn("PRIVATE", str(caught.exception.diagnostics))
        self.assertNotIn(str(self.profile), str(caught.exception.diagnostics))

    def test_fifo_is_rejected_without_reading_and_identified(self):
        os.mkfifo(self.data / "pipe")
        with self.assertRaises(RuntimeUpdateStorageError) as caught:
            list(self.storage._walk_files(self.data))
        self.assertEqual(caught.exception.code, "runtime_update_unsafe_data")
        self.assertEqual(caught.exception.diagnostics["storage_check"], "file_type")
        self.assertEqual(caught.exception.diagnostics["relative_path"], "pipe")

    def test_symlink_path_is_bounded(self):
        directory = self.profiles / ("a" * 200) / ("b" * 200)
        directory.mkdir(parents=True)
        (directory / ("c" * 200)).symlink_to("/PRIVATE_EXTERNAL_TARGET")
        with self.assertRaises(RuntimeUpdateStorageError) as caught:
            list(self.storage._walk_files(self.profiles))
        self.assertLessEqual(len(caught.exception.diagnostics["relative_path"]), 512)
