"""Large full-profile bundles and consistent export/import bounds."""

import hashlib
import json
import stat
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from xnobrain.repositories.base import StoreError
from xnobrain.repositories.files import FileRepository
from xnobrain.services.portability import PortabilityService


class ArchiveLimitTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.enterContext(patch.dict("os.environ", {"RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": ""}))
        self.repository = FileRepository(self.root / "data", self.root / "profiles")
        self.service = PortabilityService(self.repository, self.root / "hermes")

    def bundle(self, count=3, *, bad_checksum=False):
        path = self.root / "bundle.zip"
        manifest = {
            "format": "xnobrain-bundle",
            "version": 1,
            "agents": [{"id": "source"}],
            "teams": [],
        }
        payloads = {
            "manifest.json": json.dumps(manifest).encode(),
            "profiles/source/config.yaml": b"model: {}\n",
        }
        payloads.update(
            (f"profiles/source/skills/item-{number}.txt", b"") for number in range(count - 3)
        )
        checksums = {
            name: {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
            for name, data in payloads.items()
        }
        if bad_checksum:
            checksums["profiles/source/config.yaml"]["sha256"] = "0" * 64
        with ZipFile(path, "w", ZIP_DEFLATED) as archive:
            for name, data in payloads.items():
                archive.writestr(name, data)
            archive.writestr("checksums.json", json.dumps(checksums))
        return path

    def profile(self):
        profile = self.repository.profile_path("source")
        profile.mkdir(parents=True)
        (profile / "config.yaml").write_text("model: {}\n")
        (profile / "notes.txt").write_text("complete profile contents\n")
        return profile

    def assert_rejected(self, path, message):
        with self.assertRaisesRegex(StoreError, message) as rejected:
            self.service.inspect_file(path)
        self.assertEqual(rejected.exception.code, "invalid_bundle")

    def test_import_accepts_bundle_above_previous_twenty_thousand_limit(self):
        path = self.bundle(20_001)
        archive, files, _ = self.service._validated_archive_file(path)
        with archive:
            self.assertEqual(len(files), 20_001)
        # The single-profile Community channel keeps its existing limit.
        with self.assertRaisesRegex(StoreError, "20,001 files; maximum is 20,000"):
            self.service._validated_archive_file(path, max_files=20_000)

    def test_import_accepts_exact_limit_and_ignores_directory_entries(self):
        path = self.bundle(5)
        with ZipFile(path, "a") as archive:
            archive.writestr("profiles/source/skills/", b"")
        with patch("xnobrain.services.portability.MAX_FILES", 5):
            archive, files, _ = self.service._validated_archive_file(path)
            with archive:
                self.assertEqual(len(files), 5)

    def test_over_limit_rejected_before_payload_hashing(self):
        path = self.bundle(6)
        with (
            patch("xnobrain.services.portability.MAX_FILES", 5),
            patch.object(self.service, "_hash_member") as hash_member,
        ):
            self.assert_rejected(path, "6 files; maximum is 5")
            hash_member.assert_not_called()

    def test_duplicate_and_case_collision_have_specific_error(self):
        for name in ("profiles/source/config.yaml", "profiles/source/CONFIG.yaml"):
            with self.subTest(name=name):
                path = self.bundle()
                with warnings.catch_warnings(), ZipFile(path, "a") as archive:
                    warnings.simplefilter("ignore", UserWarning)
                    archive.writestr(name, b"model: {}\n")
                self.assert_rejected(path, "duplicate or case-colliding file paths")

    def test_checksum_verification_remains_required(self):
        self.assert_rejected(self.bundle(bad_checksum=True), "bundle checksum mismatch")

    def test_unsafe_paths_and_symlinks_remain_rejected(self):
        for name in ("profiles/source/../escape", "profiles/source/link"):
            with self.subTest(name=name):
                path = self.bundle()
                info = ZipInfo(name)
                if name.endswith("link"):
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                with ZipFile(path, "a") as archive:
                    archive.writestr(info, b"target")
                self.assert_rejected(path, "unsafe path|unsupported file type")

    def test_export_and_import_count_metadata_and_teams_at_same_boundary(self):
        self.profile()
        self.repository.put_team({"id": "team", "orchestrator_id": "source"})
        with patch("xnobrain.services.portability.MAX_FILES", 5):
            exported = self.service.start_export({"agent_ids": ["source"]})
            path = self.service.export_root / exported["export_id"] / "bundle.zip"
            archive, files, _ = self.service._validated_archive_file(path)
            with archive:
                self.assertEqual(len(files), 5)
                self.assertIn("teams/team.yaml", files)
        self.service.delete_transfer("export", exported["export_id"])
        with patch("xnobrain.services.portability.MAX_FILES", 4):
            with self.assertRaisesRegex(StoreError, "5 files; maximum is 4"):
                self.service.start_export({"agent_ids": ["source"]})
        self.assertEqual(list(self.service.export_root.iterdir()), [])

    def test_export_rejects_case_collision_and_cleans_failed_transfer(self):
        profile = self.profile()
        (profile / "NOTES.txt").write_text("different file\n")
        with self.assertRaisesRegex(StoreError, "duplicate or case-colliding"):
            self.service.start_export({"agent_ids": ["source"]})
        self.assertEqual(list(self.service.export_root.iterdir()), [])

    def test_import_and_export_enforce_expanded_size(self):
        path = self.bundle()
        self.profile()
        with patch("xnobrain.services.portability.MAX_EXPANDED", 10):
            self.assert_rejected(path, "bundle expansion limits exceeded")
            with self.assertRaisesRegex(StoreError, "bundle expansion limits exceeded"):
                self.service.start_export({"agent_ids": ["source"]})
        self.assertEqual(list(self.service.export_root.iterdir()), [])

    def test_metadata_size_limit_remains_enforced(self):
        path = self.bundle()
        with ZipFile(path) as archive:
            archive.getinfo("checksums.json").file_size = 16 * 1024 * 1024 + 1
            with self.assertRaisesRegex(StoreError, "bundle metadata is too large"):
                self.service._archive_files(archive)


if __name__ == "__main__":
    unittest.main()
