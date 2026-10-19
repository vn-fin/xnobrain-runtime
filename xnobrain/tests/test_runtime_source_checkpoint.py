"""Offline checkpoints preserve drained data and replay crash-safe evidence."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from xnobrain.integrations.runtime_source_checkpoint import checkpoint, require_stopped
from xnobrain.integrations.runtime_update_storage import RuntimeUpdateStorage
from xnobrain.repositories.base import RepositoryBase
from xnobrain.repositories.runtime_updates import RuntimeUpdateRepository


class SourceCheckpointTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.anchor = Path(self.temp.name).resolve()
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
            **dict(zip(("data_dir", "profiles_root", "root_profile"), map(str, self.paths))),
        }
        self.journal.save_maintenance({**self.binding, "dispatch_paused": True, "phase": "drained"})
        self.journal.save_operation({**self.binding, "steps": {"drain": {"drained": True}}})
        self.state = self.paths[0] / "connector.json"
        self.state.write_text('{"status":"ready"}')
        stopped = patch("xnobrain.integrations.runtime_source_checkpoint.require_stopped")
        stopped.start()
        self.addCleanup(stopped.stop)

    def run_checkpoint(self, request=None):
        return checkpoint(request or self.request, data_anchor=self.anchor)

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
                    return_value=SimpleNamespace(stdout=f"ActiveState={state}\nMainPID={pid}\n"),
                ),
            ):
                if accepted:
                    require_stopped()
                else:
                    with self.assertRaises(ValueError):
                        require_stopped()
