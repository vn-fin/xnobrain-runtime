"""P4 source rollout drain: defer busy work, verified abort-unchanged, lineage."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
from pydantic import ValidationError
from xnobrain.integrations.rebalance_admission import WorkspaceAdmissionMiddleware
from xnobrain.integrations.runtime_build_identity import source_installed_identity
from xnobrain.models.runtime_updates import RuntimeRolloutDrain, RuntimeRolloutRequest
from xnobrain.repositories.base import RepositoryBase, StoreError
from xnobrain.repositories.runtime_update_gate import (
    WorkspaceActivity,
    activity_present,
    require_admission,
)
from xnobrain.repositories.runtime_updates import RuntimeUpdateRepository
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

    async def test_abort_refuses_after_candidate_phases(self):
        await self.call("drain", request(deadline_seconds=0, strategy="source_in_place_v1"))
        maintenance = self.service.repository.maintenance()
        self.service.repository.save_maintenance({**maintenance, "phase": "recovering"})
        with self.assertRaises(ServiceError) as raised:
            await self.call("abort_unchanged", request())
        self.assertEqual(raised.exception.code, "runtime_update_abort_unsafe")

    async def test_source_abort_after_restore_requires_identical_identity(self):
        await self.call("drain", request(deadline_seconds=0, strategy="source_in_place_v1"))
        maintenance = self.service.repository.maintenance()
        self.service.repository.save_maintenance({**maintenance, "phase": "checkpointed"})
        # A swapped-but-not-restored source receipt blocks reopening dispatch.
        with (
            patch(
                "xnobrain.services.runtime_updates.source_installed_identity",
                return_value={"kind": "runtime_source_v1", "source_revision": "1" * 40},
            ),
            self.assertRaises(ServiceError) as raised,
        ):
            await self.call("abort_unchanged", request())
        self.assertEqual(raised.exception.code, "runtime_update_abort_unsafe")
        # After restoration the identity equals the pre-drain identity again.
        result = await self.call("abort_unchanged", request())
        self.assertTrue(result["aborted_unchanged"])
        self.assertFalse(self.connector.paused)

    async def test_image_maintenance_cannot_abort_after_checkpoint(self):
        await self.call("drain", request(deadline_seconds=0))
        maintenance = self.service.repository.maintenance()
        self.service.repository.save_maintenance({**maintenance, "phase": "checkpointed"})
        with self.assertRaises(ServiceError):
            await self.call("abort_unchanged", request())

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


class MaintenanceMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    PREFIX = "/xnobrain/api/runtime/v1"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.data = root / "data"
        (root / "profiles").mkdir()
        self.data.mkdir()
        self.updates = RuntimeUpdateRepository(RepositoryBase(self.data, root / "profiles"))
        self.observed = []

        async def app(scope, receive, send):
            # Record whether the request holds an activity lease the drain sees.
            self.observed.append((scope["path"], activity_present(self.data)))
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})

        transport = httpx.ASGITransport(app=WorkspaceAdmissionMiddleware(app, root=self.data))
        self.client = httpx.AsyncClient(transport=transport, base_url="http://runtime")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.temporary.cleanup()

    def maintain(self, kind):
        self.updates.save_maintenance(
            {**request(), "dispatch_paused": True, "phase": "draining", "kind": kind}
        )

    async def test_source_maintenance_refuses_new_work_with_typed_header(self):
        self.maintain("runtime_source")
        response = await self.client.post(self.PREFIX + "/agents/a/conversations")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["data"]["code"], "runtime_update_maintenance")
        self.assertEqual(response.headers["X-XNOBrain-Maintenance"], "runtime-update")
        self.assertEqual(self.observed, [])

    async def test_explicit_stop_and_cancel_pass_while_holding_activity(self):
        self.maintain("runtime_source")
        for path in (
            "/agents/a/conversations/c/runs/r/stop",
            "/agents/a/conversations/c/runs/r/children/k/stop",
            "/teams/t/runs/r/cancel",
            "/kanban/boards/b/tasks/t/cancel",
        ):
            response = await self.client.post(self.PREFIX + path)
            self.assertEqual(response.status_code, 200, path)
        self.assertTrue(all(held for _path, held in self.observed))
        self.assertEqual(len(self.observed), 4)

    async def test_stop_suffix_does_not_admit_other_methods(self):
        self.maintain("runtime_source")
        response = await self.client.delete(self.PREFIX + "/teams/t/runs/r/cancel")
        self.assertEqual(response.status_code, 503)

    async def test_reads_pass_and_rebalance_keeps_its_label(self):
        self.maintain("vm_rebalance")
        self.assertEqual((await self.client.get(self.PREFIX + "/agents")).status_code, 200)
        response = await self.client.put(self.PREFIX + "/agents/a")
        self.assertEqual(response.headers["X-XNOBrain-Maintenance"], "vm-rebalance")


SOURCE_TARGET = {
    "kind": "runtime_source_v1",
    "source_revision": "1" * 40,
    "git_tree": "2" * 40,
    "manifest_digest": "sha256:" + "3" * 64,
    "package_digest": "sha256:" + "4" * 64,
    "data_schema": 1,
}


class SourceTargetTests(SourceDrainTests):
    """Reuse the drain fixture; verify against the source receipt identity."""

    def source(self, **values):
        return {"operation_id": "upd_source", "generation": 4, "target": SOURCE_TARGET, **values}

    async def verify_with_receipt(self, receipt):
        await self.call("drain", self.source(deadline_seconds=0, strategy="source_in_place_v1"))
        checkpoint = await self.call("checkpoint", self.source())
        with patch(
            "xnobrain.services.runtime_updates.source_installed_identity",
            return_value=receipt,
        ):
            return await self.call(
                "post_verify", self.source(checkpoint_id=checkpoint["checkpoint_id"])
            )

    async def test_post_verify_accepts_matching_source_receipt_then_resumes(self):
        result = await self.verify_with_receipt(dict(SOURCE_TARGET))
        self.assertTrue(result["verified"])
        resumed = await self.call("resume", self.source())
        self.assertTrue(resumed["resumed"])
        self.assertFalse(self.connector.paused)

    async def test_post_verify_rejects_other_source_and_keeps_maintenance(self):
        with self.assertRaises(ServiceError):
            await self.verify_with_receipt({**SOURCE_TARGET, "git_tree": "9" * 40})
        with self.assertRaises(ServiceError) as raised:
            await self.call("resume", self.source())
        self.assertEqual(raised.exception.code, "runtime_update_not_verified")
        self.assertTrue(self.service.repository.maintenance()["dispatch_paused"])


class SourceReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "runtime-source.json"

    def tearDown(self):
        self.temporary.cleanup()

    def test_reads_only_a_regular_source_receipt(self):
        self.assertEqual(source_installed_identity(self.path), {})
        receipt = {**SOURCE_TARGET, "operation_id": "upd_1", "installed_at": "now"}
        self.path.write_text(json.dumps(receipt), encoding="utf-8")
        self.assertEqual(source_installed_identity(self.path), SOURCE_TARGET)
        self.path.write_text(json.dumps({**receipt, "kind": "incus_image"}), encoding="utf-8")
        self.assertEqual(source_installed_identity(self.path), {})
        target = Path(self.temporary.name) / "elsewhere.json"
        target.write_text(json.dumps(receipt), encoding="utf-8")
        self.path.unlink()
        self.path.symlink_to(target)
        self.assertEqual(source_installed_identity(self.path), {})

    def test_rollout_request_accepts_only_valid_source_targets(self):
        request = RuntimeRolloutRequest(operation_id="upd_x", generation=1, target=SOURCE_TARGET)
        self.assertEqual(request.target.kind, "runtime_source_v1")
        for bad in ({**SOURCE_TARGET, "git_tree": "HEAD"}, {**SOURCE_TARGET, "extra": 1}):
            with self.assertRaises(ValidationError):
                RuntimeRolloutRequest(operation_id="upd_x", generation=1, target=bad)


if __name__ == "__main__":
    unittest.main()
