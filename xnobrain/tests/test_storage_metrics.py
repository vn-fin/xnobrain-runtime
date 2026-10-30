from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import mock_open, patch

_SPEC = importlib.util.spec_from_file_location(
    "runtime_storage_metrics", Path(__file__).parents[1] / "integrations" / "runtime.py"
)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
Manager = _MODULE.LocalRuntimeManager


class RuntimeStorageMetricsTests(unittest.TestCase):
    def setUp(self):
        self.files = {
            "/proc/self/mountinfo": (
                "1 0 0:219 /containers/root / rw - btrfs /dev/loop0 rw,subvolid=293\n"
                "2 1 0:219 /custom/data /opt/data rw - btrfs /dev/loop0 rw,subvolid=292\n"
            ),
        }
        for identifier, used, total in (
            (293, 7903092736, 8 << 30),
            (292, 6889472, 2 << 30),
        ):
            base = f"/sys/fs/btrfs/test-filesystem/qgroups/0_{identifier}"
            self.files.update(
                {
                    f"{base}/limit_flags": "1",
                    f"{base}/referenced": str(used),
                    f"{base}/max_referenced": str(total),
                    f"{base}/exclusive": "50000000",
                }
            )

    def read(self, path, *args, **kwargs):
        if str(path) not in self.files:
            raise FileNotFoundError(path)
        return self.files[str(path)]

    def volumes(self, instance_type="container", data_dir="/opt/data"):
        with (
            patch.object(Path, "read_text", autospec=True, side_effect=self.read),
            patch.object(Manager, "_filesystem_uuid", return_value="test-filesystem"),
            patch.object(
                _MODULE.shutil,
                "disk_usage",
                side_effect=AssertionError("host pool used"),
            ),
        ):
            return Manager(data_dir=data_dir)._storage_volumes(instance_type)

    def test_container_reads_referenced_quota_instead_of_exclusive_or_host_pool(self):
        root, data = self.volumes()
        self.assertEqual((root["used_bytes"], root["total_bytes"]), (7903092736, 8 << 30))
        self.assertEqual((data["used_bytes"], data["total_bytes"]), (6889472, 2 << 30))
        self.assertEqual(root["source"], "btrfs_qgroup_referenced")

    def test_private_btrfs_usage_excludes_shared_image_blocks(self):
        root, data = self.volumes()
        self.assertEqual(root["private_used_bytes"], 50000000)
        self.assertEqual(root["shared_bytes"], 7903092736 - 50000000)
        self.assertTrue(root["private_metrics_available"])
        self.assertEqual(data["used_bytes"], 6889472)

    def test_missing_exclusive_counter_does_not_break_capacity(self):
        del self.files["/sys/fs/btrfs/test-filesystem/qgroups/0_292/exclusive"]
        self.assertTrue(self.volumes()[1]["available"])
        self.assertEqual(self.capacity()["free_bytes"], (2 << 30) - 6889472)

    def test_host_sample_includes_filesystem_overhead_not_just_files(self):
        volumes = [{"name": "root"}, {"name": "data"}]
        with patch.object(
            Manager,
            "_private_storage_sample",
            return_value={
                "root": {"private_used_bytes": 464257024, "shared_bytes": 8116764672},
                "data": {"private_used_bytes": 665845760, "shared_bytes": 0},
            },
        ):
            Manager._attach_private_storage(volumes)
        self.assertEqual(sum(v["private_used_bytes"] for v in volumes), 1130102784)
        self.assertEqual(volumes[1]["shared_bytes"], 0)

    def test_missing_invalid_or_partial_host_samples_are_unknown(self):
        for sample in (
            {},
            {"root": {"private_used_bytes": -1, "shared_bytes": 2}},
            {"root": {"private_used_bytes": True, "shared_bytes": 2}},
        ):
            with (
                self.subTest(sample=sample),
                patch.object(Manager, "_private_storage_sample", return_value=sample),
            ):
                volumes = [{"name": "root", "used_bytes": 7 << 30}]
                Manager._attach_private_storage(volumes)
                self.assertFalse(volumes[0]["private_metrics_available"])
                self.assertIsNone(volumes[0]["private_used_bytes"])

    def test_sample_rejects_stale_foreign_malformed_or_untrusted_data(self):
        base = {
            "version": 1,
            "workspace_id": "w",
            "sampled_at": 1000,
            "source": "lvm_thin_exclusive",
            "volumes": {"root": {"private_used_bytes": 0}},
        }
        metadata = SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_uid=0, st_size=200)
        for changes, now, uid, mode, accepted in (
            ({}, 1050, 0, 0o644, True),
            ({}, 1181, 0, 0o644, False),
            ({}, 990, 0, 0o644, False),
            ({"workspace_id": "other"}, 1050, 0, 0o644, False),
            ({}, 1050, 1000, 0o644, False),
            ({}, 1050, 0, 0o666, False),
            ({"source": "filesystem"}, 1050, 0, 0o644, False),
            ({"sampled_at": "bad"}, 1050, 0, 0o644, False),
        ):
            metadata.st_uid, metadata.st_mode = uid, stat.S_IFREG | mode
            with (
                self.subTest(changes=changes, now=now, uid=uid, mode=mode),
                patch.dict(os.environ, {"RUNTIME_WORKSPACE_ID": "w"}),
                patch.object(_MODULE.os, "open", return_value=123),
                patch.object(_MODULE.os, "fdopen", mock_open(read_data=json.dumps(base | changes))),
                patch.object(_MODULE.os, "fstat", return_value=metadata),
                patch.object(_MODULE.time, "time", return_value=now),
            ):
                self.assertEqual(bool(Manager._private_storage_sample()), accepted)

    def test_data_directory_inside_mount_uses_that_volume(self):
        self.assertEqual(len(self.volumes(data_dir="/opt/data/profiles")), 2)

    def test_data_on_root_is_not_counted_twice(self):
        self.assertEqual(len(self.volumes(data_dir="/home/runtime")), 1)

    def test_missing_or_invalid_quota_never_falls_back_to_pool(self):
        for value in ("0", "3", "17", "invalid"):
            with self.subTest(value=value):
                self.files["/sys/fs/btrfs/test-filesystem/qgroups/0_293/limit_flags"] = value
                root = self.volumes()[0]
                self.assertFalse(root["available"])
                self.assertIsNone(root["used_bytes"])
        del self.files["/sys/fs/btrfs/test-filesystem/qgroups/0_293/limit_flags"]
        self.assertFalse(self.volumes()[0]["available"])

    def test_exclusive_quota_uses_matching_counter(self):
        base = "/sys/fs/btrfs/test-filesystem/qgroups/0_293"
        self.files[f"{base}/limit_flags"] = "2"
        self.files[f"{base}/max_exclusive"] = str(8 << 30)
        root = self.volumes()[0]
        self.assertEqual(root["used_bytes"], 50000000)
        self.assertEqual(root["source"], "btrfs_qgroup_exclusive")

    def test_over_quota_reading_is_not_clamped(self):
        self.files["/sys/fs/btrfs/test-filesystem/qgroups/0_293/referenced"] = str(9 << 30)
        self.assertEqual(self.volumes()[0]["used_bytes"], 9 << 30)

    def capacity(self, free_bytes=70 << 30):
        with (
            patch.object(Path, "read_text", autospec=True, side_effect=self.read),
            patch.object(Manager, "_filesystem_uuid", return_value="test-filesystem"),
            patch.object(
                _MODULE.shutil,
                "disk_usage",
                return_value=shutil._ntuple_diskusage(
                    80 << 30, (80 << 30) - free_bytes, free_bytes
                ),
            ),
        ):
            return Manager.storage_capacity(Path("/opt/data"), require_quota=True)

    def test_preflight_capacity_uses_data_quota_not_pool_or_root(self):
        capacity = self.capacity()
        self.assertTrue(capacity["available"])
        self.assertEqual(capacity["free_bytes"], (2 << 30) - 6889472)
        self.assertEqual(capacity["source"], "btrfs_qgroup_referenced")

    def test_preflight_capacity_also_honors_pool_exhaustion(self):
        self.assertEqual(self.capacity(1024)["free_bytes"], 1024)

    def test_preflight_unknown_quota_fails_closed(self):
        del self.files["/sys/fs/btrfs/test-filesystem/qgroups/0_292/limit_flags"]
        self.assertEqual(
            self.capacity(), {"available": False, "free_bytes": None, "source": "unavailable"}
        )

    def test_preflight_over_quota_has_no_headroom(self):
        self.files["/sys/fs/btrfs/test-filesystem/qgroups/0_292/referenced"] = str(3 << 30)
        self.assertEqual(self.capacity()["free_bytes"], 0)

    def test_preflight_unquotaed_mount_is_not_a_container_limit(self):
        self.files["/proc/self/mountinfo"] = "1 0 0:1 / / rw - ext4 /dev/sda rw\n"
        self.assertFalse(self.capacity()["available"])

    def test_vm_preserves_filesystem_metrics_even_on_btrfs(self):
        disk = shutil._ntuple_diskusage(30 << 30, 9 << 30, 21 << 30)
        with (
            patch.object(Path, "read_text", autospec=True, side_effect=self.read),
            patch.object(_MODULE.shutil, "disk_usage", return_value=disk),
            patch.object(
                Manager,
                "_filesystem_uuid",
                side_effect=AssertionError("VM used host quota"),
            ),
        ):
            volumes = Manager()._storage_volumes("virtual-machine")
        self.assertEqual(len(volumes), 1)
        self.assertEqual((volumes[0]["used_bytes"], volumes[0]["total_bytes"]), (9 << 30, 30 << 30))

    def test_type_option_and_automatic_detection(self):
        for explicit, expected in (
            ("vm", "virtual-machine"),
            ("virtual-machine", "virtual-machine"),
            ("container", "container"),
        ):
            with patch.dict(os.environ, {"RUNTIME_INSTANCE_TYPE": explicit}):
                self.assertEqual(Manager._instance_type(), expected)
        with (
            patch.dict(os.environ, {"RUNTIME_INSTANCE_TYPE": ""}),
            patch.object(Path, "exists", return_value=False),
        ):
            self.assertEqual(Manager._instance_type(), "virtual-machine")
        with (
            patch.dict(os.environ, {"RUNTIME_INSTANCE_TYPE": ""}),
            patch.object(Path, "exists", return_value=True),
        ):
            self.assertEqual(Manager._instance_type(), "container")

    def test_overlay_container_reports_unknown_instead_of_host_capacity(self):
        self.files["/proc/self/mountinfo"] = "1 0 0:1 / / rw - overlay overlay rw\n"
        self.assertFalse(self.volumes()[0]["available"])

    def test_detail_uses_same_volume_totals_for_http_and_stream_consumers(self):
        volumes = self.volumes()
        with (
            patch.object(Manager, "_storage_volumes", return_value=volumes),
            patch.object(Manager, "_instance_type", return_value="container"),
        ):
            detail = Manager().detail()
        self.assertEqual(detail["info"]["type"], "container")
        self.assertEqual(detail["metrics"]["disk_total_bytes"], 10 << 30)
        self.assertEqual(detail["metrics"]["disk_usage_bytes"], 7903092736 + 6889472)
        self.assertTrue(detail["metrics"]["disk_metrics_available"])


if __name__ == "__main__":
    unittest.main()
