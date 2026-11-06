"""Cron delivery targets expose Zalo only for enabled agent channels."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from xnobrain.repositories.files import FileRepository
from xnobrain.services.cron import CronService, CronServiceError


class _Agents:
    def __init__(self, profiles: Path):
        self.profiles = profiles
        self.root = profiles / "big-brother"

    def profile_path(self, agent_id: str) -> Path:
        return self.profiles / agent_id

    def list_agent_names(self) -> list[str]:
        return ["agent-1"]

    def describe_agent(self, *args, **kwargs):
        return {"name": "agent-1"}


class CronZaloTargetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.profiles = root / "profiles"
        self.profile = self.profiles / "agent-1"
        self.profile.mkdir(parents=True)
        (self.profiles / "big-brother").mkdir()
        self.environment = mock.patch.dict(
            os.environ, {"RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": ""}
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.repository = FileRepository(
            root / "data", self.profiles, root_profile=self.profiles / "big-brother"
        )
        self.service = CronService(self.repository, _Agents(self.profiles))

    def _write_config(self, enabled: bool) -> None:
        self.profile.joinpath("config.yaml").write_text(
            "platforms:\n"
            "  zalo:\n"
            f"    enabled: {'true' if enabled else 'false'}\n",
            encoding="utf-8",
        )

    def _options(self) -> list[dict]:
        with mock.patch.object(self.service, "_native_module", return_value=[]):
            return self.service.list_delivery_target_options("agent-1")["options"]

    def test_enabled_channel_adds_a_zalo_delivery_option(self):
        self._write_config(True)

        options = self._options()

        zalo = next(item for item in options if item["id"] == "zalo")
        self.assertEqual(zalo["target_type"], "channel")
        self.assertTrue(zalo["available"])

    def test_disabled_channel_has_no_zalo_delivery_option(self):
        self._write_config(False)

        self.assertFalse(any(item["id"] == "zalo" for item in self._options()))

    def test_add_target_accepts_zalo_thread_when_enabled(self):
        self._write_config(True)
        job = {"id": "job-1", "xnobrain_delivery_targets": []}
        with mock.patch.object(
            self.service, "_find_job", return_value=("agent-1", job)
        ), mock.patch.object(self.service, "_snapshot_store"), mock.patch.object(
            self.service, "_native", return_value=job
        ), mock.patch.object(
            self.service, "_native_module", return_value=[]
        ):
            target = self.service.add_delivery_target(
                "job-1", {"target_type": "channel", "destination": "zalo:111"}, "agent-1"
            )

        self.assertEqual(target["target_type"], "channel")
        self.assertEqual(target["destination"], "zalo:111")
        self.assertTrue(target["available"])

    def test_add_target_rejects_zalo_when_disabled(self):
        self._write_config(False)
        job = {"id": "job-1", "xnobrain_delivery_targets": []}
        with mock.patch.object(self.service, "_find_job", return_value=("agent-1", job)):
            with self.assertRaises(CronServiceError) as raised:
                self.service.add_delivery_target(
                    "job-1",
                    {"target_type": "channel", "destination": "zalo"},
                    "agent-1",
                )

        self.assertEqual(raised.exception.code, "delivery_target_unavailable")

    def test_add_target_rejects_malformed_zalo_destination(self):
        self._write_config(True)
        job = {"id": "job-1", "xnobrain_delivery_targets": []}
        with mock.patch.object(self.service, "_find_job", return_value=("agent-1", job)):
            with self.assertRaises(CronServiceError) as raised:
                self.service.add_delivery_target(
                    "job-1",
                    {"target_type": "channel", "destination": "zalo:abc"},
                    "agent-1",
                )

        self.assertEqual(raised.exception.code, "invalid_delivery_target")


if __name__ == "__main__":
    unittest.main()
