"""Offline checkpoints preserve drained data and replay crash-safe evidence."""

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from xnobrain.integrations.runtime_data_volume import MARKER
from xnobrain.integrations.runtime_source_checkpoint import (
    checkpoint,
    require_stopped,
    restore_ready,
)
from xnobrain.integrations.runtime_update_storage import RuntimeUpdateStorage
from xnobrain.repositories.base import RepositoryBase
from xnobrain.repositories.runtime_updates import RuntimeUpdateRepository


class SourceCheckpointTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.anchor = Path(self.temp.name).resolve()
        self.volume_id = "d223fcce-3588-4a30-b1a3-cd80e938bd68"
        self.marker = self.anchor / MARKER
        self.marker.write_text(json.dumps({"schema_version": 1, "volume_id": self.volume_id}))
        environment = patch.dict(
            os.environ,
            {"RUNTIME_DATA_VOLUME_ID": self.volume_id, "RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": ""},
        )
        environment.start()
        self.addCleanup(environment.stop)
        # Exercise the actual managed mount/marker verifier with an Incus
        # filesystem-volume mount table, rather than mocking the guard away.
        self.mountinfo = f"42 1 0:33 / {self.anchor} rw,relatime - virtiofs incus_data rw\n"
        read_text = Path.read_text

        def read(path, *args, **kwargs):
            if path == Path("/proc/self/mountinfo"):
                return self.mountinfo
            return read_text(path, *args, **kwargs)

        mounts = patch.object(Path, "read_text", read)
        mounts.start()
        self.addCleanup(mounts.stop)
        self.paths = [self.anchor / name for name in ("data", "profiles", "root")]
        for path in self.paths:
            path.mkdir()
        self.base = RepositoryBase(self.paths[0], self.paths[1], root_profile=self.paths[2])
        self.journal = RuntimeUpdateRepository(self.base)
        self.binding = {
            "operation_id": "upd_offline",
            "generation": 7,
            "target": {
                "kind": "runtime_source_v1",
                "source_revision": "a" * 40,
                "git_tree": "b" * 40,
                "manifest_digest": "sha256:" + "c" * 64,
                "package_digest": "sha256:" + "d" * 64,
                "data_schema": 1,
            },
        }
        self.request = {
            **self.binding,
            "data_path": str(self.anchor),
            **dict(
                zip(
                    ("data_dir", "profiles_root", "root_profile"), map(str, self.paths), strict=True
                )
            ),
        }
        self.journal.save_maintenance(
            {**self.binding, "dispatch_paused": True, "phase": "drained", "kind": "runtime_source"}
        )
        self.journal.save_operation({**self.binding, "steps": {"drain": {"drained": True}}})
        self.state = self.paths[0] / "connector.json"
        self.state.write_text('{"status":"ready"}')
        stopped = patch("xnobrain.integrations.runtime_source_checkpoint.require_stopped")
        stopped.start()
        self.addCleanup(stopped.stop)

    def run_checkpoint(self, request=None):
        return checkpoint(request or self.request, data_anchor=self.anchor)

    def test_managed_volume_failure_precedes_repository_writes(self):
        for failure in ("missing marker", "wrong marker", "missing mount", "read only", "no id"):
            with self.subTest(failure=failure):
                self.marker.write_text(
                    json.dumps({"schema_version": 1, "volume_id": self.volume_id})
                )
                mountinfo = self.mountinfo
                if failure == "missing marker":
                    self.marker.unlink()
                elif failure == "wrong marker":
                    self.marker.write_text('{"schema_version":1,"volume_id":"wrong"}')
                elif failure == "missing mount":
                    self.mountinfo = ""
                elif failure == "read only":
                    self.mountinfo = self.mountinfo.replace("rw,relatime", "ro,relatime")
                with (
                    patch.dict(
                        os.environ,
                        {
                            "RUNTIME_DATA_VOLUME_ID": "" if failure == "no id" else self.volume_id,
                            # Even stale provisioning flags cannot initialize recovery data.
                            "RUNTIME_DATA_VOLUME_INITIALIZE": "true",
                        },
                    ),
                    patch("xnobrain.integrations.runtime_source_checkpoint.RepositoryBase") as base,
                ):
                    with self.assertRaises((ValueError, OSError)):
                        self.run_checkpoint()
                    base.assert_not_called()
                self.mountinfo = mountinfo
                if failure == "missing marker":
                    self.assertFalse(self.marker.exists())
        self.assertEqual(list(self.journal.checkpoints.glob("*.json")), [])

    def test_baseline_is_taken_after_old_connector_stops_and_never_refreshed(self):
        self.state.write_text('{"status":"error"}')
        identity = self.run_checkpoint()
        baseline = self.journal.checkpoint(identity)
        storage = RuntimeUpdateStorage(*self.paths)
        self.assertTrue(storage.verify(baseline["manifest"])["preserved"])
        self.state.write_text('{"status":"different"}')
        self.assertEqual(self.run_checkpoint(), identity)
        self.assertEqual(self.journal.checkpoint(identity), baseline)
        self.assertFalse(storage.verify(baseline["manifest"])["preserved"])
        self.assertTrue(self.journal.operation("upd_offline")["steps"]["drain"]["drained"])

    def test_lost_reply_after_checkpoint_file_reconciles_original_baseline(self):
        with patch.object(
            RuntimeUpdateRepository, "save_maintenance", side_effect=OSError("crash")
        ):
            with self.assertRaises(OSError):
                self.run_checkpoint()
        files = list(self.journal.checkpoints.glob("*.json"))
        self.assertEqual(len(files), 1)
        original = json.loads(files[0].read_text())
        self.state.write_text("changed after crash")
        self.assertEqual(self.run_checkpoint(), original["checkpoint_id"])
        self.assertEqual(self.journal.operation("upd_offline")["steps"]["checkpoint"], original)

    def test_lost_reply_after_maintenance_reconciles_operation(self):
        with patch.object(RuntimeUpdateRepository, "save_operation", side_effect=OSError("crash")):
            with self.assertRaises(OSError):
                self.run_checkpoint()
        identity = self.journal.maintenance()["checkpoint_id"]
        self.state.write_text("changed after crash")
        self.assertEqual(self.run_checkpoint(), identity)
        self.assertEqual(
            self.journal.operation("upd_offline")["steps"]["checkpoint"]["checkpoint_id"], identity
        )

    def test_rejects_stale_owner_target_paths_and_undrained_state(self):
        for field, value in (
            ("generation", 8),
            ("operation_id", "upd_other"),
            ("data_dir", "/tmp"),
        ):
            with self.subTest(field=field):
                request = {**self.request, field: value}
                with self.assertRaises(ValueError):
                    self.run_checkpoint(request)
        request = copy.deepcopy(self.request)
        request["target"]["source_revision"] = "e" * 40
        with self.assertRaises(ValueError):
            self.run_checkpoint(request)
        self.journal.save_maintenance(
            {**self.binding, "dispatch_paused": True, "phase": "draining"}
        )
        with self.assertRaises(ValueError):
            self.run_checkpoint()
        self.assertEqual(list(self.journal.checkpoints.glob("*.json")), [])

    def test_stopped_target_restore_proves_original_data_without_new_checkpoint(self):
        identity = self.run_checkpoint()
        original = self.journal.checkpoint(identity)
        request = {**self.request, "checkpoint_id": identity}
        with patch.object(
            RuntimeUpdateStorage, "flush_sqlite", side_effect=AssertionError("flush")
        ):
            result = restore_ready(request, data_anchor=self.anchor)
        self.assertTrue(result["restore_ready"])
        self.assertEqual(result["checkpoint_id"], identity)
        self.assertEqual(self.journal.checkpoint(identity), original)
        self.assertTrue(self.journal.maintenance()["dispatch_paused"])
        self.assertEqual(self.journal.operation("upd_offline")["steps"]["restore_ready"], result)
        self.state.write_text("target changed durable data")
        with self.assertRaisesRegex(ValueError, "data changed"):
            restore_ready(request, data_anchor=self.anchor)
        self.assertEqual(self.journal.checkpoint(identity), original)

    def test_restore_rejects_stale_owner_resume_and_missing_evidence(self):
        identity = self.run_checkpoint()
        request = {**self.request, "checkpoint_id": identity}
        operation = self.journal.operation("upd_offline")
        maintenance = self.journal.maintenance()
        for case in ("generation", "checkpoint", "resumed", "abort", "phase", "kind", "unpaused"):
            with self.subTest(case=case):
                self.journal.save_operation(copy.deepcopy(operation))
                self.journal.save_maintenance(copy.deepcopy(maintenance))
                attempt = copy.deepcopy(request)
                if case == "generation":
                    attempt["generation"] += 1
                elif case == "checkpoint":
                    attempt["checkpoint_id"] = "ckp_" + "f" * 64
                elif case in {"resumed", "abort"}:
                    altered = copy.deepcopy(operation)
                    altered["steps"]["resume" if case == "resumed" else "abort_unchanged"] = {
                        "done": True
                    }
                    self.journal.save_operation(altered)
                else:
                    altered = copy.deepcopy(maintenance)
                    altered[
                        {"phase": "phase", "kind": "kind", "unpaused": "dispatch_paused"}[case]
                    ] = {"phase": "draining", "kind": "other", "unpaused": False}[case]
                    self.journal.save_maintenance(altered)
                with self.assertRaises(ValueError):
                    restore_ready(attempt, data_anchor=self.anchor)


class StoppedProcessTest(unittest.TestCase):
    def test_requires_terminal_unit_and_no_main_pid(self):
        for state, pid, accepted in (
            ("inactive", "0", True),
            ("failed", "0", True),
            ("active", "0", False),
            ("deactivating", "0", False),
            ("inactive", "25", False),
        ):
            with (
                self.subTest(state=state, pid=pid),
                patch(
                    "xnobrain.integrations.runtime_source_checkpoint.subprocess.run",
                    return_value=SimpleNamespace(
                        stdout=f"ActiveState={state}\nMainPID={pid}\nControlPID=0\n"
                    ),
                ),
            ):
                if accepted:
                    require_stopped()
                else:
                    with self.assertRaises(ValueError):
                        require_stopped()

    def test_container_service_and_control_process_are_checked(self):
        for control in ("0", "12", None):
            output = "ActiveState=inactive\nMainPID=0\n"
            if control is not None:
                output += f"ControlPID={control}\n"
            with patch(
                "xnobrain.integrations.runtime_source_checkpoint.subprocess.run",
                return_value=SimpleNamespace(stdout=output),
            ) as command:
                if control == "0":
                    require_stopped("xnobrain-runtime.service")
                else:
                    with self.assertRaises(ValueError):
                        require_stopped("xnobrain-runtime.service")
                self.assertEqual(command.call_args.args[0][-1], "xnobrain-runtime.service")


class OfflineLauncherTest(unittest.TestCase):
    def test_isolated_launcher_avoids_application_initializers_and_user_pythonpath(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scripts").mkdir()
            launcher = Path(__file__).resolve().parents[2] / "scripts" / "runtime-source-offline.py"
            shutil.copyfile(launcher, root / "scripts" / launcher.name)
            (root / "xnobrain").mkdir()
            (root / "xnobrain" / "__init__.py").write_text("")
            for name in ("integrations", "models", "repositories", "services"):
                directory = root / "xnobrain" / name
                directory.mkdir()
                (directory / "__init__.py").write_text("raise RuntimeError('composition loaded')")
            (root / "xnobrain" / "integrations" / "runtime_source_checkpoint.py").write_text(
                "import xnobrain.models, xnobrain.services, xnobrain.repositories\n"
                "print('offline-only')\n"
            )
            poison = root / "poison"
            poison.mkdir()
            (poison / "sitecustomize.py").write_text("raise RuntimeError('user code loaded')")
            result = subprocess.run(
                [sys.executable, "-I", "-B", str(root / "scripts" / launcher.name)],
                env={**os.environ, "PYTHONPATH": str(poison), "PYTHONHOME": ""},
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            self.assertEqual(result.stdout, "offline-only\n")
            self.assertEqual(result.stderr, "")
