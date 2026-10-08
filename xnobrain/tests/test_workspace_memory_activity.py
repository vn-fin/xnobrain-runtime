"""Idle history must survive polling gaps and fail closed across processes."""

from __future__ import annotations

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

from xnobrain.repositories.base import RepositoryBase, StoreError
from xnobrain.repositories.portability_tasks import PortabilityTaskStore
from xnobrain.repositories.runtime_update_gate import (
    WorkspaceActivity,
    activity_present,
    admission_gate,
)
from xnobrain.repositories.workspace_memory import WorkspaceMemoryRepository
from xnobrain.services.portability_tasks import PortabilityTasks


class WorkspaceMemoryActivityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.now = 10**9
        environment = patch.dict(os.environ, {"RUNTIME_WORKSPACE_ID": "workspace-test"})
        environment.start()
        self.addCleanup(environment.stop)
        self.clock = patch(
            "xnobrain.repositories.workspace_memory.time.monotonic_ns", side_effect=lambda: self.now
        )
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.store = WorkspaceMemoryRepository(self.root, "workspace-test")
        with admission_gate(self.root):
            self.store.start()

    def advance(self, seconds):
        self.now += seconds * 10**9

    def observe(self, *, coverage=True):
        with admission_gate(self.root):
            return self.store.observe(
                busy=activity_present(self.root, business_only=True), coverage_complete=coverage
            )

    def test_default_and_fast_boundaries_and_chat_at_last_second(self):
        for threshold in (30, 60, 600):
            with self.subTest(threshold=threshold):
                with WorkspaceActivity(self.root):
                    pass
                self.advance(threshold - 1)
                before = self.observe()
                self.assertEqual(before["idle_duration_ms"], (threshold - 1) * 1000)
                with WorkspaceActivity(self.root):
                    self.advance(2 * threshold)
                    self.assertEqual(self.observe()["activity_state"], "busy")
                finished = self.observe()
                self.assertGreater(finished["activity_epoch"], before["activity_epoch"])
                self.assertEqual(finished["idle_duration_ms"], 0)
                self.advance(threshold - 1)
                self.assertLess(self.observe()["idle_duration_ms"], threshold * 1000)
                self.advance(1)
                self.assertEqual(self.observe()["idle_duration_ms"], threshold * 1000)

    def test_short_work_between_polls_invalidates_the_old_epoch(self):
        self.advance(600)
        before = self.observe()
        with WorkspaceActivity(self.root):
            self.advance(1)
        after = self.observe()
        self.assertNotEqual(before["activity_epoch"], after["activity_epoch"])
        self.assertEqual(after["idle_duration_ms"], 0)

    def test_background_poll_preserves_idle_but_business_promotion_resets_it(self):
        initial = self.observe()
        with WorkspaceActivity(self.root, passive=True):
            self.assertTrue(activity_present(self.root))  # Still blocks checkpoint/rebalance.
            self.assertFalse(activity_present(self.root, business_only=True))
            self.advance(600)
            idle = self.observe()
            self.assertEqual(idle["activity_epoch"], initial["activity_epoch"])
            self.assertEqual(idle["idle_duration_ms"], 600_000)
            with WorkspaceActivity(self.root):
                self.assertEqual(self.observe()["activity_state"], "busy")
            self.assertEqual(self.observe()["idle_duration_ms"], 0)
        self.assertFalse(activity_present(self.root))

    def test_passive_poll_cannot_inherit_permission_to_start_work_during_maintenance(self):
        for kind in ("vm_rebalance", "workspace_memory_reclaim"):
            with self.subTest(kind=kind), WorkspaceActivity(self.root, passive=True):
                with patch(
                    "xnobrain.repositories.runtime_update_gate.maintenance",
                    return_value={"kind": kind, "dispatch_paused": True},
                ):
                    with self.assertRaises(StoreError):
                        WorkspaceActivity(self.root)

    def test_terminal_file_cleanup_is_active_only_when_files_remain(self):
        jobs = PortabilityTaskStore(self.root)
        base = RepositoryBase(self.root, self.root / "profiles")
        (self.root / "bundles").mkdir()
        service = PortabilityTasks(
            SimpleNamespace(repository=base, transfer_root=self.root / "bundles"), store=jobs
        )
        paths = []
        for kind in ("IMPORT", "EXPORT"):
            task, _ = jobs.admit(
                scope="workspace",
                actor="actor",
                kind=kind,
                key=kind.lower(),
                fingerprint=jobs.digest({"kind": kind}),
                inputs={"environment_ref": "fixture"},
                **({"upload_id": "upload"} if kind == "IMPORT" else {}),
            )
            claim = jobs.claim("worker")
            jobs.finish(
                task["id"],
                "worker",
                claim["fence"],
                result={"export_id": "fixture", "expires_at_epoch": 0},
            )
            path = (
                self.root / "portability-imports" / task["id"]
                if kind == "IMPORT"
                else service.artifacts / "fixture"
            )
            path.mkdir(parents=True)
            (path / "scratch").write_text("fixture")
            paths.append(path)
        secret = self.root / "portability-inputs" / "fixture"
        secret.parent.mkdir()
        secret.write_text("fixture")
        with patch(
            "xnobrain.repositories.runtime_update_gate.maintenance",
            return_value={"kind": "workspace_memory_reclaim", "dispatch_paused": True},
        ):
            with self.assertRaises(StoreError):
                service.cleanup_terminal_inputs()
        self.assertTrue(secret.exists())
        self.assertTrue(all(path.exists() for path in paths))
        remove = shutil.rmtree
        observed = []

        def tracked_remove(path):
            observed.append(activity_present(self.root, business_only=True))
            remove(path)

        before = self.observe()
        with patch("shutil.rmtree", side_effect=tracked_remove):
            service.cleanup_terminal_inputs()
        self.assertEqual(observed, [True, True])
        self.assertFalse(secret.exists())
        self.assertGreater(self.observe()["activity_epoch"], before["activity_epoch"])
        self.advance(600)
        idle = self.observe()
        service.cleanup_terminal_inputs()
        self.assertEqual(self.observe()["activity_epoch"], idle["activity_epoch"])
        self.assertEqual(self.observe()["idle_duration_ms"], 600_000)

    def test_inherited_cli_lease_remains_a_business_lease(self):
        from xnobrain.integrations.rebalance_cli import ACTIVITY_FD, cli_activity

        parent = WorkspaceActivity(self.root)
        with patch.dict(
            os.environ, {"DATA_DIR": str(self.root), ACTIVITY_FD: str(parent.descriptor)}
        ):
            inherited = cli_activity()
        parent.close()
        with inherited:
            self.assertEqual(self.observe()["activity_state"], "busy")
        self.assertEqual(self.observe()["idle_duration_ms"], 0)

    def test_passive_polling_keeps_record_and_lock_inodes_unchanged(self):
        before = self.store.path.read_bytes()
        inode = (self.root / ".runtime-update-admission.lock").stat().st_ino
        for _ in range(20):
            self.advance(1)
            self.assertEqual(self.observe()["activity_state"], "idle")
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual((self.root / ".runtime-update-admission.lock").stat().st_ino, inode)

    def test_json_failure_revokes_old_proof_and_does_not_block_work(self):
        self.advance(600)
        old = self.observe()
        old_json = self.store.path.read_bytes()
        with patch.object(WorkspaceMemoryRepository, "_atomic_json", side_effect=OSError()):
            with WorkspaceActivity(self.root):
                (self.root / "work-completed").write_text("synthetic")
            self.assertEqual(self.store.path.read_bytes(), old_json)
            second_process_view = WorkspaceMemoryRepository(self.root, "workspace-test")
            with admission_gate(self.root):
                observed = second_process_view.observe(busy=False, coverage_complete=True)
            self.assertEqual(observed["activity_state"], "unknown")
        recovered = self.observe()
        self.assertNotEqual(old["runtime_boot_id"], recovered["runtime_boot_id"])
        self.assertEqual(recovered["idle_duration_ms"], 0)

    def test_restart_corruption_and_boot_mismatch_start_fresh_intervals(self):
        for change in ("restart", "corrupt", "workspace", "boot"):
            with self.subTest(change=change):
                self.advance(600)
                old = self.observe()
                if change == "restart":
                    with admission_gate(self.root):
                        self.store.start()
                elif change == "corrupt":
                    self.store.path.write_text("{")
                else:
                    state = json.loads(self.store.path.read_text())
                    state["workspace_id" if change == "workspace" else "guest_boot_id"] = "other"
                    self.store.path.write_text(json.dumps(state))
                fresh = self.observe()
                self.assertNotEqual(old["runtime_boot_id"], fresh["runtime_boot_id"])
                self.assertEqual(fresh["idle_duration_ms"], 0)

    def test_unknown_coverage_cannot_resume_an_old_idle_interval(self):
        self.advance(600)
        before = self.observe()
        self.assertEqual(self.observe(coverage=False)["activity_state"], "unknown")
        unknown_json = self.store.path.read_bytes()
        self.advance(600)
        self.observe(coverage=False)
        self.assertEqual(unknown_json, self.store.path.read_bytes())
        recovered = self.observe()
        self.assertNotEqual(before["runtime_boot_id"], recovered["runtime_boot_id"])
        self.assertEqual(recovered["idle_duration_ms"], 0)

    def test_child_lease_survives_parent_and_missing_completion_is_conservative(self):
        # Parent and child share the actual monotonic clock in this case.
        self.clock.stop()
        self.store = WorkspaceMemoryRepository(self.root, "workspace-test")
        with admission_gate(self.root):
            self.store.start()
        parent = WorkspaceActivity(self.root)
        child = subprocess.Popen(
            [sys.executable, "-c", "import sys; print('ready',flush=True); sys.stdin.readline()"],
            pass_fds=(parent.descriptor,),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual(child.stdout.readline(), "ready\n")
            parent.close()
            self.assertEqual(self.observe()["activity_state"], "busy")
            child.communicate("finish\n", timeout=5)
            self.assertEqual(child.returncode, 0)
            # No child callback: first verified quiescence starts the interval.
            recovered = self.observe()
            self.assertEqual(recovered["activity_state"], "idle")
            self.assertLess(recovered["idle_duration_ms"], 100)
        finally:
            parent.close()
            if child.poll() is None:
                child.kill()
            child.communicate()

    def test_concurrent_processes_preserve_all_transition_epochs(self):
        self.clock.stop()
        self.store = WorkspaceMemoryRepository(self.root, "workspace-test")
        with admission_gate(self.root):
            self.store.start()
        source = (
            "import sys\nfrom pathlib import Path\n"
            "from xnobrain.repositories.runtime_update_gate import WorkspaceActivity\n"
            "for _ in range(20):\n"
            "    with WorkspaceActivity(Path(sys.argv[1])):\n        pass\n"
        )
        children = [
            subprocess.Popen([sys.executable, "-c", source, str(self.root)]) for _ in range(2)
        ]
        try:
            for child in children:
                self.assertEqual(child.wait(timeout=10), 0)
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.wait()
        self.assertEqual(self.observe()["activity_epoch"], 80)


if __name__ == "__main__":
    unittest.main()
