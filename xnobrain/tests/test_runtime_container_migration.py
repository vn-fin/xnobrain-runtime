import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).parents[1] / "integrations" / "runtime_container_migration.py"
SPEC = importlib.util.spec_from_file_location("container_migration", SOURCE)
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)


class ContainerMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.anchor = self.root / "data"
        self.anchor.mkdir()
        self.legacy = self.root / "legacy"

    def test_copy_preserves_bytes_and_never_follows_external_symlinks(self):
        external = self.root / "outside"
        external.mkdir()
        (external / "secret").write_text("untouched")
        (self.anchor / "file").write_bytes(b"payload\x00bytes")
        (self.anchor / "link").symlink_to(external, target_is_directory=True)
        with (
            patch.object(migration, "verify_mount"),
            patch.object(migration.os, "chown") as chown,
            patch.object(migration.os, "sync"),
        ):
            migration.adapt_copy(self.anchor, self.legacy, 1001, 1002)
            migration.adapt_copy(self.anchor, self.legacy, 1001, 1002)
        self.assertEqual((self.anchor / "file").read_bytes(), b"payload\x00bytes")
        self.assertEqual(os.readlink(self.anchor / "link"), str(external))
        self.assertEqual(os.readlink(self.legacy), str(self.anchor))
        for call in chown.call_args_list:
            self.assertFalse(call.kwargs["follow_symlinks"])
            self.assertNotIn("outside", str(call.args[0]))

    def test_conflicting_legacy_anchor_does_not_change_ownership(self):
        self.legacy.mkdir()
        with patch.object(migration, "verify_mount"), patch.object(migration.os, "chown") as chown:
            with self.assertRaises(ValueError):
                migration.adapt_copy(self.anchor, self.legacy, 1001, 1002)
            chown.assert_not_called()

    def test_mount_and_nonroot_account_required(self):
        with self.assertRaises(ValueError):
            migration.adapt_copy(self.anchor, self.legacy, 0, 1002)
        with patch.object(migration, "verify_mount", side_effect=ValueError("missing mount")):
            with self.assertRaises(ValueError):
                migration.adapt_copy(self.anchor, self.legacy, 1001, 1002)
        self.assertFalse(self.legacy.exists())

    def test_special_file_rejected(self):
        os.mkfifo(self.anchor / "fifo")
        with patch.object(migration, "verify_mount"), patch.object(migration.os, "chown"):
            with self.assertRaises(ValueError):
                migration.adapt_copy(self.anchor, self.legacy, 1001, 1002)


if __name__ == "__main__":
    unittest.main()
