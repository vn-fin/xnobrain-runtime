"""Offline migration must validate the full data tree before changing ownership."""

from __future__ import annotations

import importlib.util
import io
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack, contextmanager, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]


class ContainerDataMigrationTests(unittest.TestCase):
    def setUp(self):
        specification = importlib.util.spec_from_file_location(
            "container_data_migration_test", ROOT / "scripts/migrate-container-data-owner.py"
        )
        self.helper = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(self.helper)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.anchor = self.root / "data"
        self.anchor.mkdir()
        (self.anchor / ".xnobrain-volume.json").write_text("synthetic-volume-marker")

    @contextmanager
    def offline(self, *, states=None, processes=(), mounts=""):
        state_values = {"ActiveState": "inactive", "MainPID": "0", "ControlPID": "0"}
        state_values.update(states or {})
        guard = SimpleNamespace(read_environment=Mock(return_value={}), verify_data=Mock())
        specification = SimpleNamespace(loader=SimpleNamespace(exec_module=Mock()))
        read_text = Path.read_text
        glob = Path.glob
        original_stat = os.stat

        def status(command, **kwargs):
            return SimpleNamespace(stdout=state_values[command[-1].removeprefix("--property=")])

        def read(path, *args, **kwargs):
            if str(path) == "/proc/self/mountinfo":
                return mounts
            return read_text(path, *args, **kwargs)

        def list_processes(path, pattern):
            if str(path) == "/proc":
                return iter(processes)
            return glob(path, pattern)

        def root_owned_stat(*args, **kwargs):
            info = original_stat(*args, **kwargs)
            # Linux CI runs as root; this only makes the ownership branch equally
            # testable by a developer without performing a real chown.
            values = list(info)
            values[4] = 0
            return os.stat_result(values)

        with ExitStack() as stack:
            for context in (
                patch.object(sys, "argv", ["migration", "--offline-data-backed-up"]),
                patch.object(self.helper, "ANCHOR", self.anchor),
                patch.object(self.helper.os, "geteuid", return_value=0),
                patch.object(
                    self.helper.pwd,
                    "getpwnam",
                    return_value=SimpleNamespace(pw_uid=1000, pw_gid=1000),
                ),
                patch.object(self.helper.subprocess, "run", side_effect=status),
                patch.object(
                    self.helper.importlib.util,
                    "spec_from_file_location",
                    return_value=specification,
                ),
                patch.object(self.helper.importlib.util, "module_from_spec", return_value=guard),
                patch.object(Path, "glob", list_processes),
                patch.object(Path, "read_text", read),
                patch.object(self.helper.os, "stat", side_effect=root_owned_stat),
                redirect_stdout(io.StringIO()),
            ):
                stack.enter_context(context)
            chown = stack.enter_context(patch.object(self.helper.os, "chown"))
            yield chown, guard

    def test_active_or_not_yet_stopped_runtime_is_rejected_before_changes(self):
        for states in (
            {"ActiveState": "active"},
            {"ActiveState": "activating"},
            {"ActiveState": "deactivating"},
            {"MainPID": "42"},
            {"ControlPID": "42"},
        ):
            with self.subTest(states=states), self.offline(states=states) as (chown, guard):
                with self.assertRaisesRegex(ValueError, "stop Runtime first"):
                    self.helper.migrate()
                chown.assert_not_called()
                guard.verify_data.assert_not_called()

    def test_running_workload_process_is_rejected_even_when_service_is_stopped(self):
        process = self.root / "status"
        process.write_text("Name:\tleftover-worker\nUid:\t1000\t1000\t1000\t1000\n")
        with self.offline(processes=[process]) as (chown, guard):
            with self.assertRaisesRegex(
                ValueError, "workload identity still has running processes"
            ):
                self.helper.migrate()
            chown.assert_not_called()
            guard.verify_data.assert_not_called()

    def test_wrong_volume_is_rejected_before_any_ownership_changes(self):
        with self.offline() as (chown, guard):
            guard.verify_data.side_effect = ValueError("storage_binding_mismatch")
            with self.assertRaisesRegex(ValueError, "storage_binding_mismatch"):
                self.helper.migrate()
            chown.assert_not_called()

    def test_nested_mount_is_rejected_before_any_ownership_changes(self):
        mounts = "1 0 0:1 / /opt/data/nested rw - tmpfs tmpfs rw\n"
        with self.offline(mounts=mounts) as (chown, _):
            with self.assertRaisesRegex(ValueError, "nested mount"):
                self.helper.migrate()
            chown.assert_not_called()

    def test_hardlink_is_rejected_after_full_validation_without_partial_chown(self):
        (self.anchor / "ordinary-file").write_text("ordinary data")
        outside = self.root / "outside"
        outside.write_text("must stay unchanged")
        original = outside.stat()
        os.link(outside, self.anchor / "linked-file")
        with self.offline() as (chown, _):
            with self.assertRaisesRegex(ValueError, "hardlinked file"):
                self.helper.migrate()
            chown.assert_not_called()
        self.assertEqual(outside.read_text(), "must stay unchanged")
        self.assertEqual(outside.stat().st_uid, original.st_uid)
        self.assertEqual(outside.stat().st_mode, original.st_mode)

    def test_unsupported_fifo_is_rejected_without_partial_chown(self):
        (self.anchor / "ordinary-file").write_text("ordinary data")
        os.mkfifo(self.anchor / "pipe")
        with self.offline() as (chown, _):
            with self.assertRaisesRegex(ValueError, "unsupported data object"):
                self.helper.migrate()
            chown.assert_not_called()

    def test_symlinks_are_not_followed_and_volume_marker_is_preserved(self):
        outside = self.root / "outside"
        outside.mkdir()
        canary = outside / "protected-file"
        canary.write_text("protected runtime")
        original = canary.stat()
        (self.anchor / "external-file-link").symlink_to(canary)
        (self.anchor / "external-directory-link").symlink_to(outside, target_is_directory=True)
        (self.anchor / "regular-file").write_text("user data")
        marker = self.anchor / ".xnobrain-volume.json"
        marker_metadata = marker.stat()
        with self.offline() as (chown, guard):
            self.helper.migrate()
            names = {call.args[0] for call in chown.call_args_list}
            self.assertIn("external-file-link", names)
            self.assertIn("external-directory-link", names)
            self.assertIn("regular-file", names)
            self.assertNotIn("protected-file", names)
            self.assertNotIn(".xnobrain-volume.json", names)
            for call in chown.call_args_list:
                self.assertEqual(call.args[1:], (1000, 1000))
                self.assertIs(call.kwargs["follow_symlinks"], False)
                self.assertIsInstance(call.kwargs["dir_fd"], int)
            guard.verify_data.assert_called_once_with({})
        self.assertEqual(canary.read_text(), "protected runtime")
        self.assertEqual(canary.stat().st_uid, original.st_uid)
        self.assertEqual(canary.stat().st_mode, original.st_mode)
        self.assertEqual(marker.read_text(), "synthetic-volume-marker")
        self.assertEqual(marker.stat().st_uid, marker_metadata.st_uid)
        self.assertEqual(marker.stat().st_mode, marker_metadata.st_mode)


if __name__ == "__main__":
    unittest.main()
