"""P4 source rollout drain: defer busy work, verified abort-unchanged, lineage."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
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
    workspace_activity,
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

    async def test_retained_busy_fence_retries_same_operation_after_current_task(self):
        started, finish = asyncio.Event(), asyncio.Event()
        completed = []

        class Scheduler:
            @workspace_activity(lambda _self: self.data, independent=True)
            async def next_task(_self):
                completed.append("unrelated")

        async def current_task():
            with WorkspaceActivity(self.data):
                started.set()
                await finish.wait()
                # Necessary descendants can complete the admitted logical task.
                with WorkspaceActivity(self.data):
                    completed.append("descendant")
                with self.assertRaises(StoreError):
                    await Scheduler().next_task()
                completed.append("current")

        task = asyncio.create_task(current_task())
        self.addCleanup(task.cancel)
        await started.wait()
        self.platform.conversation_runs._active = {"run": SimpleNamespace(task=task)}
        body = request(deadline_seconds=0, strategy="source_in_place_v1")
        for _ in range(2):
            with self.assertRaises(ServiceError) as busy:
                await asyncio.wait_for(self.call("drain", body), timeout=1)
            self.assertEqual(busy.exception.code, "runtime_update_deferred_busy")
            self.assertTrue(self.service.dispatch_paused)
            self.assertFalse(task.done())
        # A fresh Runtime service and another OS process observe the same fence.
        self.service = RuntimeUpdateService(self.platform)
        self.assertTrue(self.service.dispatch_paused)
        code = """
import sys
from pathlib import Path
from xnobrain.repositories.base import StoreError
from xnobrain.repositories.runtime_update_gate import WorkspaceActivity
try:
    WorkspaceActivity(Path(sys.argv[1]))
except StoreError as error:
    assert error.code == 'runtime_update_maintenance'
else:
    raise AssertionError('restart admitted new work')
"""
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-c", code, str(self.data)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        finish.set()
        await asyncio.wait_for(task, timeout=1)
        self.assertEqual(completed, ["descendant", "current"])
        drained = await self.call("drain", body)
        self.assertTrue(drained["drained"])
        self.assertEqual(drained["active"]["total"], 0)
        self.assertTrue(self.service.dispatch_paused)
        self.shutdown.assert_not_awaited()
        self.assertFalse(self.connector.cancelled)
        await self.call("abort_unchanged", request())
        with WorkspaceActivity(self.data):
            pass

    async def test_container_preflight_rejects_unknown_or_insufficient_data_quota(self):
        from xnobrain.integrations.runtime import LocalRuntimeManager

        layout = {
            "data_path": str(self.data),
            "separate_mount": True,
            "mount_point": str(self.data),
            "filesystem": "btrfs",
        }
        body = request(
            expected_layout="incus_custom_volume",
            data_path=str(self.data),
            required_free_bytes=4 << 30,
        )
        for capacity, code in (
            (
                {"available": True, "free_bytes": 2 << 30, "source": "btrfs_qgroup_referenced"},
                "runtime_update_insufficient_capacity",
            ),
            (
                {"available": False, "free_bytes": None, "source": "unavailable"},
                "runtime_update_capacity_unavailable",
            ),
        ):
            with (
                self.subTest(code=code),
                patch.object(self.service.storage, "layout", return_value=layout),
                patch.object(LocalRuntimeManager, "_instance_type", return_value="container"),
                patch.object(
                    LocalRuntimeManager, "storage_capacity", return_value=capacity
                ) as measured,
                self.assertRaises(ServiceError) as blocked,
            ):
                await self.call("preflight", body)
            self.assertEqual(blocked.exception.code, code)
            measured.assert_called_once_with(self.data, require_quota=True)
            stored = self.service.repository.operation(body["operation_id"])["steps"]["preflight"]
            self.assertFalse(stored["ready"])
            self.assertEqual(stored["free_bytes"], capacity["free_bytes"])
            self.assertFalse(self.service.dispatch_paused)

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
        await self.call("checkpoint", request())
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

    async def test_drain_replay_preserves_original_identity_and_checkpoint(self):
        drained = await self.call("drain", request(strategy="source_in_place_v1"))
        await self.call("checkpoint", request())
        before = self.service.repository.maintenance()
        with patch.dict(os.environ, {"XNOBRAIN_SOURCE_COMMIT": "9" * 40}):
            self.assertEqual(
                await self.call("drain", request(strategy="source_in_place_v1")), drained
            )
            self.assertEqual(self.service.repository.maintenance(), before)
            with self.assertRaises(ServiceError):
                await self.call("abort_unchanged", request())

    async def test_aborted_operation_cannot_drain_again(self):
        await self.call("drain", request(strategy="source_in_place_v1"))
        await self.call("abort_unchanged", request())
        with self.assertRaises(ServiceError) as raised:
            await self.call("drain", request(strategy="source_in_place_v1"))
        self.assertEqual(raised.exception.code, "runtime_update_aborted")
        self.assertEqual(self.service.repository.maintenance(), {})
        self.assertFalse(self.connector.paused)

    async def test_drain_recovers_after_phase_saved_but_outcome_write_lost(self):
        with patch.object(self.service, "_record", side_effect=OSError("lost outcome")):
            with self.assertRaises(OSError):
                await self.call("drain", request(strategy="source_in_place_v1"))
        original = self.service.repository.maintenance()
        self.service = RuntimeUpdateService(self.platform)
        with patch.dict(os.environ, {"XNOBRAIN_SOURCE_COMMIT": "9" * 40}):
            result = await self.call("drain", request(strategy="source_in_place_v1"))
        self.assertTrue(result["drained"])
        self.assertFalse(result["cancelled_at_deadline"])
        self.assertEqual(self.service.repository.maintenance(), original)
        self.shutdown.assert_not_awaited()

    async def test_checkpoint_recovers_original_manifest_after_lost_outcome(self):
        data = self.data / "sentinel.txt"
        data.write_text("original")
        await self.call("drain", request(strategy="source_in_place_v1"))
        with patch.object(self.service, "_record", side_effect=OSError("lost outcome")):
            with self.assertRaises(OSError):
                await self.call("checkpoint", request())
        original = self.service.repository.checkpoint(
            self.service.repository.maintenance()["checkpoint_id"]
        )
        data.write_text("unexpected writer")
        self.service = RuntimeUpdateService(self.platform)
        replay = await self.call("checkpoint", request())
        self.assertEqual(replay, original)
        with self.assertRaises(ServiceError) as raised:
            await self.call("restore_ready", request(checkpoint_id=replay["checkpoint_id"]))
        self.assertEqual(raised.exception.code, "runtime_source_restore_unsafe")
        self.assertTrue(self.service.dispatch_paused)

    async def test_internal_source_caller_cannot_force_cancel(self):
        with self.assertRaises(ServiceError):
            await self.call(
                "drain", request(strategy="source_in_place_v1", cancel_active_at_deadline=True)
            )
        self.shutdown.assert_not_awaited()
        self.assertEqual(self.service.repository.maintenance(), {})

    async def test_source_restore_requires_unchanged_checkpoint_data(self):
        data = self.data / "sentinel.txt"
        data.write_text("before")
        await self.call("drain", request(strategy="source_in_place_v1"))
        checkpoint = await self.call("checkpoint", request())
        restore = request(checkpoint_id=checkpoint["checkpoint_id"])
        proof = await self.call("restore_ready", restore)
        self.assertTrue(proof["restore_ready"])
        data.write_text("new data")
        for method in ("restore_ready", "abort_unchanged"):
            with self.assertRaises(ServiceError) as raised:
                await self.call(method, restore)
            self.assertEqual(raised.exception.code, "runtime_source_restore_unsafe")
        self.assertTrue(self.service.repository.maintenance()["dispatch_paused"])
        self.assertTrue(self.connector.paused)
        self.assertEqual(data.read_text(), "new data")

    async def test_source_restore_refuses_missing_checkpoint_or_resume_outcome(self):
        await self.call("drain", request(strategy="source_in_place_v1"))
        with self.assertRaises(ServiceError):
            await self.call("restore_ready", request(checkpoint_id="missing"))
        checkpoint = await self.call("checkpoint", request())
        self.service._record(request(), "resume", {"resumed": True})
        with self.assertRaises(ServiceError) as raised:
            await self.call("restore_ready", request(checkpoint_id=checkpoint["checkpoint_id"]))
        self.assertEqual(raised.exception.code, "runtime_source_restore_unsafe")

    async def test_restore_proof_requires_service_identity_and_original_fence(self):
        await self.call("drain", request(strategy="source_in_place_v1"))
        checkpoint = await self.call("checkpoint", request())
        body = request(checkpoint_id=checkpoint["checkpoint_id"])
        before = self.service.repository.maintenance()
        with self.assertRaises(ServiceError) as raised:
            await self.service.restore_ready(body, update_token="wrong-token")
        self.assertEqual(raised.exception.status, 401)
        for foreign in ({"operation_id": "upd_foreign"}, {"generation": 5}):
            with self.assertRaises(ServiceError):
                await self.call("restore_ready", {**body, **foreign})
        self.assertEqual(self.service.repository.maintenance(), before)
        self.assertTrue(self.service.dispatch_paused)

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
