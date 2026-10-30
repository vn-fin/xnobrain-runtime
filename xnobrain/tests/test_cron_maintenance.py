"""Native ticker startup and skipped ticks preserve a closed checkpoint."""

from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

from cron import jobs
from cron.scheduler_provider import InProcessCronScheduler
from hermes_constants import reset_hermes_home_override, set_hermes_home_override

from xnobrain.integrations.cron_maintenance import TICKER_WRITERS, install_ticker_maintenance
from xnobrain.integrations.runtime_update_storage import RuntimeUpdateStorage
from xnobrain.repositories.base import RepositoryBase
from xnobrain.repositories.runtime_update_gate import WorkspaceActivity
from xnobrain.repositories.runtime_updates import RuntimeUpdateRepository


class CronMaintenanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        anchor = Path(temporary.name).resolve()
        self.data, self.root, self.profiles = (
            anchor / name for name in ("data", "root", "profiles")
        )
        for directory in (self.data, self.root, self.profiles):
            directory.mkdir()
        environment = patch.dict(os.environ, {"RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": ""})
        environment.start()
        self.addCleanup(environment.stop)
        for name in TICKER_WRITERS:
            restore = patch.object(jobs, name, getattr(jobs, name))
            restore.start()
            self.addCleanup(restore.stop)
        install_ticker_maintenance(self.data)
        self.journal = RuntimeUpdateRepository(
            RepositoryBase(self.data, self.profiles, root_profile=self.root)
        )
        self.storage = RuntimeUpdateStorage(self.data, self.profiles, self.root)
        self.secondary = self.profiles / "agent"
        self.secondary.mkdir()
        for home in (self.root, self.secondary):
            with self.profile(home), patch("cron.jobs.time.time", return_value=100.0):
                jobs.record_ticker_heartbeat(success=True)
                jobs.record_ticker_error("fixture status")
                InProcessCronScheduler().recover_interrupted()

    @staticmethod
    @contextmanager
    def profile(home):
        token = set_hermes_home_override(str(home))
        try:
            with jobs.use_cron_store(home):
                yield
        finally:
            reset_hermes_home_override(token)

    def pause(self):
        self.journal.save_maintenance(
            {
                "dispatch_paused": True,
                "kind": "runtime_source",
                "operation_id": "upd_ticker",
                "generation": 1,
                "target": {"source_commit": "fixture"},
            }
        )

    def test_native_status_writes_resume_after_maintenance(self):
        with self.profile(self.root), patch("cron.jobs.time.time", return_value=200.0):
            jobs.record_ticker_heartbeat(success=True)
            self.assertEqual((self.root / "cron/ticker_heartbeat").read_text(), "200.0")
            self.assertEqual((self.root / "cron/ticker_last_success").read_text(), "200.0")
            self.pause()
            baseline = self.storage.manifest()
            with patch("cron.jobs.time.time", return_value=300.0):
                jobs.record_ticker_heartbeat(success=True)
                jobs.record_ticker_error("different status")
                jobs.clear_ticker_error()
            self.assertTrue(self.storage.verify(baseline)["preserved"])
            self.journal.clear_maintenance()
            with patch("cron.jobs.time.time", return_value=400.0):
                jobs.record_ticker_heartbeat(success=True)
            self.assertEqual((self.root / "cron/ticker_last_success").read_text(), "400.0")

    def test_startup_and_skipped_ticks_preserve_both_profile_stores(self):
        self.pause()
        baseline = self.storage.manifest()
        for homes in (None, [self.root, self.secondary]):
            with self.subTest(multiplex=homes is not None):
                stop = Mock()
                stop.is_set.side_effect = [False, True]
                with self.profile(self.root), patch("cron.scheduler.tick") as tick:
                    InProcessCronScheduler().start(
                        stop,
                        can_dispatch=lambda: False,
                        profile_homes=homes,
                    )
                tick.assert_not_called()
                stop.wait.assert_called_once()
                self.assertTrue(self.storage.verify(baseline)["preserved"])

    def test_admitted_task_lineage_cannot_update_ticker_status(self):
        with WorkspaceActivity(self.data), self.profile(self.root):
            self.pause()
            baseline = self.storage.manifest()
            jobs.record_ticker_heartbeat(success=True)
            jobs.clear_ticker_error()
            self.assertTrue(self.storage.verify(baseline)["preserved"])

    def test_unreadable_gate_keeps_status_and_user_cron_data_still_verified(self):
        self.pause()
        baseline = self.storage.manifest()
        self.journal.maintenance_path.write_text("invalid journal")
        with self.profile(self.root):
            jobs.record_ticker_heartbeat(success=True)
            jobs.clear_ticker_error()
        self.assertTrue(self.storage.verify(baseline)["preserved"])
        # No cron path is excluded from the manifest by the ticker guard.
        path = self.secondary / "cron/jobs.json"
        path.write_text("[]\n")
        baseline = self.storage.manifest()
        path.write_text('[{"id":"user-job"}]\n')
        self.assertFalse(self.storage.verify(baseline)["preserved"])


if __name__ == "__main__":
    unittest.main()
