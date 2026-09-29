import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from xnobrain.integrations.runtime_data_volume import (
    MARKER,
    guard_environment,
    validate_archive,
    verify_mount,
    verify_volume,
)


class RuntimeDataVolumeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.volume_id = str(uuid4())

    def test_missing_mount_never_creates_marker(self):
        with self.assertRaises(ValueError):
            verify_volume(self.root, self.volume_id, initialize=True)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_exact_bind_mount_is_accepted_but_parent_mount_is_not(self):
        mounts = self.root / "mountinfo"
        mounts.write_text(f"42 1 8:1 / {self.root} rw - ext4 /dev/a rw\n")
        verify_mount(self.root, mounts)
        mounts.write_text("42 1 8:1 / / rw - ext4 /dev/a rw\n")
        with self.assertRaises(ValueError):
            verify_mount(self.root, mounts)

    def test_read_only_volume_is_not_ready(self):
        mounts = self.root / "mountinfo"
        mounts.write_text(f"42 1 8:1 / {self.root} ro - ext4 /dev/a ro\n")
        with self.assertRaises(ValueError):
            verify_mount(self.root, mounts)

    @patch("xnobrain.integrations.runtime_data_volume.verify_mount")
    def test_fresh_volume_and_restart_preserve_marker_and_payload(self, _mount):
        verify_volume(self.root, self.volume_id, initialize=True)
        (self.root / "conversation").write_text("keep")
        verify_volume(self.root, self.volume_id, initialize=True)
        self.assertEqual((self.root / "conversation").read_text(), "keep")
        self.assertEqual(json.loads((self.root / MARKER).read_text())["volume_id"], self.volume_id)
        with self.assertRaises(ValueError):
            verify_volume(self.root, str(uuid4()), adopt=True)

    @patch("xnobrain.integrations.runtime_data_volume.verify_mount")
    def test_fresh_initialization_cannot_adopt_populated_volume(self, _mount):
        (self.root / "data").write_text("keep")
        with self.assertRaises(ValueError):
            verify_volume(self.root, self.volume_id, initialize=True)
        verify_volume(self.root, self.volume_id, adopt=True)
        self.assertEqual((self.root / "data").read_text(), "keep")

    @patch("xnobrain.integrations.runtime_data_volume.verify_mount")
    def test_ext4_recovery_directory_may_be_empty_but_not_populated(self, _mount):
        recovery = self.root / "lost+found"
        recovery.mkdir()
        if recovery.stat().st_uid != 0:
            self.skipTest("root-owned filesystem fixture required")
        (recovery / "recovered-file").write_text("keep")
        with self.assertRaises(ValueError):
            verify_volume(self.root, self.volume_id, initialize=True)
        (recovery / "recovered-file").unlink()
        verify_volume(self.root, self.volume_id, initialize=True)

    @patch("xnobrain.integrations.runtime_data_volume.verify_mount")
    def test_symlink_marker_never_authorizes_storage(self, _mount):
        target = self.root / "target"
        target.write_text(json.dumps({"schema_version": 1, "volume_id": self.volume_id}))
        (self.root / MARKER).symlink_to(target)
        with self.assertRaises(OSError):
            verify_volume(self.root, self.volume_id)

    def test_legacy_environment_requires_no_mount_but_invalid_policy_fails(self):
        with patch.dict("os.environ", {}, clear=True):
            guard_environment()
        with patch.dict("os.environ", {"RUNTIME_DATA_MOUNT_REQUIRED": "maybe"}, clear=True):
            with self.assertRaises(ValueError):
                guard_environment()

    def archive(self, members):
        archive = self.root / "data.tar"
        with tarfile.open(archive, "w") as stream:
            for member in members:
                stream.addfile(member, io.BytesIO(b"x" * member.size))
        return archive

    def test_archive_preserves_leaf_tool_symlink_and_internal_hardlink(self):
        file = tarfile.TarInfo("profile/data")
        file.size = 1
        link = tarfile.TarInfo("profile/bin/tool")
        link.type = tarfile.SYMTYPE
        link.linkname = "/usr/local/bin/tool"
        hard = tarfile.TarInfo("profile/data2")
        hard.type = tarfile.LNKTYPE
        hard.linkname = "profile/data"
        validate_archive(self.archive([file, link, hard]))

    def test_archive_rejects_escapes_special_files_and_duplicate_names(self):
        for name in ["../outside", "/outside", MARKER]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_archive(self.archive([tarfile.TarInfo(name)]))
        fifo = tarfile.TarInfo("fifo")
        fifo.type = tarfile.FIFOTYPE
        with self.assertRaises(ValueError):
            validate_archive(self.archive([fifo]))
        with self.assertRaises(ValueError):
            validate_archive(self.archive([tarfile.TarInfo("x"), tarfile.TarInfo("./x")]))

    def test_archive_rejects_symlink_ancestor_even_when_listed_after_child(self):
        link = tarfile.TarInfo("profile")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc"
        with self.assertRaises(ValueError):
            validate_archive(self.archive([tarfile.TarInfo("profile/passwd"), link]))

    def test_archive_rejects_hardlink_to_external_path(self):
        link = tarfile.TarInfo("link")
        link.type = tarfile.LNKTYPE
        link.linkname = "../outside"
        with self.assertRaises(ValueError):
            validate_archive(self.archive([link]))
