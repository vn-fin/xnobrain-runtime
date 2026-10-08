"""P4 source rollout drain: defer busy work, verified abort-unchanged, lineage."""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from pydantic import ValidationError
from xnobrain.models.runtime_updates import RuntimeRolloutDrain
from xnobrain.repositories.base import RepositoryBase, StoreError
from xnobrain.repositories.runtime_update_gate import (
    WorkspaceActivity,
    require_admission,
)
from xnobrain.services.base import ServiceError
from xnobrain.services.runtime_updates import RuntimeUpdateService

TARGET = {
    "version": "2.1.0",
    "source_commit": "a" * 40,
    "runtime_digest": "sha256:" + "b" * 64,
    "data_schema": 1,
}
INCUS_TARGET = {
    "kind": "incus_image",
    "fingerprint": "c" * 64,
    "build_descriptor_digest": "sha256:" + "d" * 64,
    "version": "2.1.0",
    "source_commit": "a" * 40,
    "data_schema": 1,
}


def request(**values):
    return {"operation_id": "upd_source", "generation": 4, "target": TARGET, **values}


class FakeConnector:
    def __init__(self):
        self.paused = False
        self.cancelled = False

    async def pause_dispatch(self):
        self.paused = True

    def resume_dispatch(self):
        self.paused = False

    async def cancel_active_commands(self):
        self.cancelled = True


class SourceDrainTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.data = root / "data"
        self.profiles = root / "profiles"
        self.data.mkdir()
        self.profiles.mkdir()
        self.connector = FakeConnector()
        self.shutdown = AsyncMock()
        self.platform = SimpleNamespace(
            repository=RepositoryBase(self.data, self.profiles),
            config=SimpleNamespace(root_profile=root / "big-brother"),
            organization_connector=self.connector,
            cron=SimpleNamespace(active_execution_count=0),
            conversation_runs=SimpleNamespace(_active={}, shutdown=self.shutdown),
            team_runs=SimpleNamespace(_active={}, shutdown=AsyncMock()),
            kanban=SimpleNamespace(
                active_agent_ids=Mock(return_value=set()),
                cancel_active_tasks_for_update=Mock(return_value=0),
            ),
        )
        self.environment = patch.dict(
            os.environ,
            {
                "RUNTIME_UPDATE_SERVICE_TOKEN": "update-service-token",
                "XNOBRAIN_VERSION": "2.0.0",
                "XNOBRAIN_SOURCE_COMMIT": "e" * 40,
                "XNOBRAIN_RUNTIME_DIGEST": "sha256:" + "f" * 64,
                "RUNTIME_DATA_SCHEMA": "1",
            },
            clear=False,
        )
        self.environment.start()
        self.service = RuntimeUpdateService(self.platform)

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    async def call(self, method, body):
        return await getattr(self.service, method)(body, update_token="update-service-token")

    async def test_busy_source_drain_defers_without_cancelling_then_aborts(self):
        running = asyncio.create_task(asyncio.sleep(60))
        self.addCleanup(running.cancel)
        self.platform.conversation_runs._active = {"run": SimpleNamespace(task=running)}
        with self.assertRaises(ServiceError) as raised:
            await self.call("drain", request(deadline_seconds=0, strategy="source_in_place_v1"))
        self.assertEqual(raised.exception.code, "runtime_update_deferred_busy")
        self.assertFalse(running.done())
        self.shutdown.assert_not_awaited()
        self.assertFalse(self.connector.cancelled)
        maintenance = self.service.repository.maintenance()
        self.assertEqual(maintenance["kind"], "runtime_source")
        self.assertTrue(maintenance["dispatch_paused"])
        self.assertTrue(self.connector.paused)

        result = await self.call("abort_unchanged", request())
        self.assertTrue(result["aborted_unchanged"])
        self.assertEqual(self.service.repository.maintenance(), {})
        self.assertFalse(self.connector.paused)
        self.assertFalse(running.done())
        # A lost reply is replayed without releasing anything twice.
        self.assertEqual(await self.call("abort_unchanged", request()), result)

    async def test_abort_refuses_when_source_changed_during_maintenance(self):
        await self.call("drain", request(deadline_seconds=0, strategy="source_in_place_v1"))
        with (
            patch.dict(os.environ, {"XNOBRAIN_SOURCE_COMMIT": "9" * 40}),
            self.assertRaises(ServiceError) as raised,
        ):
            await self.call("abort_unchanged", request())
        self.assertEqual(raised.exception.code, "runtime_update_abort_unsafe")
        self.assertTrue(self.service.repository.maintenance()["dispatch_paused"])

    async def test_abort_refuses_after_drain_phases(self):
        await self.call("drain", request(deadline_seconds=0, strategy="source_in_place_v1"))
        maintenance = self.service.repository.maintenance()
        self.service.repository.save_maintenance({**maintenance, "phase": "checkpointed"})
        with self.assertRaises(ServiceError) as raised:
            await self.call("abort_unchanged", request())
        self.assertEqual(raised.exception.code, "runtime_update_abort_unsafe")

    async def test_abort_requires_matching_maintenance_owner(self):
        await self.call("drain", request(deadline_seconds=0, strategy="source_in_place_v1"))
        with self.assertRaises(ServiceError):
            await self.call("abort_unchanged", request(operation_id="upd_other"))
        with self.assertRaises(ServiceError):
            await self.call("abort_unchanged", request(generation=5))
        self.assertTrue(self.service.repository.maintenance()["dispatch_paused"])

    async def test_legacy_drain_keeps_cancelling_behaviour(self):
        running = asyncio.create_task(asyncio.sleep(60))
        self.addCleanup(running.cancel)
        self.platform.conversation_runs._active = {"run": SimpleNamespace(task=running)}

        async def shutdown():
            running.cancel()
            self.platform.conversation_runs._active.clear()

        self.platform.conversation_runs.shutdown = shutdown
        result = await self.call(
            "drain", request(deadline_seconds=0, cancel_active_at_deadline=True)
        )
        self.assertTrue(result["cancelled_at_deadline"])
        self.assertNotIn("kind", self.service.repository.maintenance())

    async def test_source_maintenance_admits_only_existing_lineage(self):
        await self.call("drain", request(deadline_seconds=0, strategy="source_in_place_v1"))
        # A new root request is refused with the typed maintenance code.
        with self.assertRaises(StoreError) as raised:
            WorkspaceActivity(self.data)
        self.assertEqual(raised.exception.code, "runtime_update_maintenance")
        # Work admitted before drain may create descendants in its own lineage.
        self.service.repository.clear_maintenance()
        parent = WorkspaceActivity(self.data)
        self.service.repository.save_maintenance(
            {
                **request(),
                "dispatch_paused": True,
                "phase": "draining",
                "kind": "runtime_source",
            }
        )
        with parent:
            require_admission(self.data)
            child = WorkspaceActivity(self.data)
            child.close()
        parent.close()


class SourceDrainContractTests(unittest.TestCase):
    def test_source_strategy_cannot_request_cancellation(self):
        with self.assertRaises(ValidationError):
            RuntimeRolloutDrain(
                operation_id="upd_x",
                generation=1,
                target=INCUS_TARGET,
                strategy="source_in_place_v1",
                cancel_active_at_deadline=True,
            )
        drain = RuntimeRolloutDrain(
            operation_id="upd_x",
            generation=1,
            target=INCUS_TARGET,
            strategy="source_in_place_v1",
        )
        self.assertFalse(drain.cancel_active_at_deadline)


if __name__ == "__main__":
    unittest.main()
