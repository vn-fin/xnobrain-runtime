"""Idle proof, returning-user races and lost-response recovery at Runtime."""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from xnobrain.integrations.rebalance_admission import WorkspaceAdmissionMiddleware
from xnobrain.repositories.base import RepositoryBase, StoreError
from xnobrain.repositories.runtime_update_gate import WorkspaceActivity, activity_present
from xnobrain.repositories.runtime_updates import RuntimeUpdateRepository
from xnobrain.repositories.workspace_memory import WorkspaceMemoryRepository
from xnobrain.runtime.v1 import runtime_gateway_pb2 as pb
from xnobrain.services.workspace_memory import WorkspaceMemoryService, policy_values


class MemoryReclaimTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "data"
        self.root.mkdir()
        self.now = 1_000_000_000
        env = patch.dict(
            os.environ,
            {
                "RUNTIME_WORKSPACE_ID": "workspace",
                "FT_ENABLE_WORKSPACE_IDLE_MEMORY_RECLAIM": "true",
                "RUNTIME_WORKSPACE_IDLE_MEMORY_IDLE_SECONDS": "30",
                "RUNTIME_WORKSPACE_IDLE_MEMORY_SCAN_SECONDS": "5",
            },
        )
        env.start()
        self.addCleanup(env.stop)
        clock = patch(
            "xnobrain.repositories.workspace_memory.time.monotonic_ns", side_effect=lambda: self.now
        )
        clock.start()
        self.addCleanup(clock.stop)
        base = RepositoryBase(self.root, Path(temporary.name) / "profiles")
        self.platform = SimpleNamespace(
            repository=base,
            runtime_updates=SimpleNamespace(
                repository=RuntimeUpdateRepository(base),
                _active_counts=lambda **_kwargs: {"total": 0},
            ),
        )
        self.helper = SimpleNamespace(
            stopped=AsyncMock(return_value=True), execute=AsyncMock(), receipt=lambda: {}
        )
        self.service = self.new_service()
        self.service.start()
        self.policy = pb.WorkspaceMemoryPolicy(**policy_values())
        self.activity()
        self.now += 30_000_000_000
        self.identity = self.new_identity()
        self.helper.receipt = lambda: {
            "operation_id": "mem_test",
            "fence": 1,
            "guest_boot_id": self.identity.guest_boot_id,
            "state": "executing",
        }

    def new_service(self):
        return WorkspaceMemoryService(
            self.platform,
            helper=self.helper,
            coverage=lambda: True,
            tracker=WorkspaceMemoryRepository(self.root, "workspace"),
            sample=lambda: {
                "cached_bytes": 512 * 1024**2,
                "available_bytes": 2048 * 1024**2,
                "dirty_bytes": 0,
                "writeback_bytes": 0,
                "swap_bytes": 0,
            },
            backend=lambda: "virtio-reporting-order0-4k-v1",
        )

    def activity(self):
        return self.service.activity("workspace", self.policy)

    def new_identity(self, fence=1, operation="mem_test"):
        value = self.activity()
        return pb.WorkspaceMemoryReclaimIdentity(
            operation_id=operation,
            workspace_id="workspace",
            fence=fence,
            guest_boot_id=value["guest_boot_id"],
            runtime_boot_id=value["runtime_boot_id"],
            activity_epoch=value["activity_epoch"],
            policy=self.policy,
        )

    async def prepare(self):
        value = await self.service.command(self.identity, "prepare")
        return value["prepare_token"]

    async def test_guest_pressure_and_small_cache_do_not_block_idle_workspace(self):
        # drop_caches discards only clean pages, so these are not safety gates.
        pressured = WorkspaceMemoryService(
            self.platform,
            helper=self.helper,
            coverage=lambda: True,
            tracker=WorkspaceMemoryRepository(self.root, "workspace"),
            sample=lambda: {
                "cached_bytes": 0,
                "available_bytes": 0,
                "dirty_bytes": 1024**3,
                "writeback_bytes": 1024**2,
                "swap_bytes": 0,
            },
            backend=lambda: "virtio-reporting-order0-4k-v1",
        )
        pressured.start()
        pressured.activity("workspace", self.policy)
        self.now += 30_000_000_000
        value = pressured.activity("workspace", self.policy)
        identity = pb.WorkspaceMemoryReclaimIdentity(
            operation_id="mem_pressure",
            workspace_id="workspace",
            fence=1,
            guest_boot_id=value["guest_boot_id"],
            runtime_boot_id=value["runtime_boot_id"],
            activity_epoch=value["activity_epoch"],
            policy=self.policy,
        )
        prepared = await pressured.command(identity, "prepare")
        self.assertTrue(prepared["prepare_token"])

    def test_default_policy_has_no_cache_floor(self):
        with patch.dict(os.environ, {"RUNTIME_WORKSPACE_IDLE_MEMORY_MIN_CACHE_BYTES": ""}):
            os.environ.pop("RUNTIME_WORKSPACE_IDLE_MEMORY_MIN_CACHE_BYTES")
            self.assertEqual(policy_values()["min_cache_bytes"], 0)
        with patch.dict(os.environ, {"RUNTIME_WORKSPACE_IDLE_MEMORY_MIN_CACHE_BYTES": "-1"}):
            with self.assertRaises(ValueError):
                policy_values()

    async def test_intervening_short_work_invalidates_prepared_cleanup(self):
        token = await self.prepare()
        with WorkspaceActivity(self.root):
            pass
        with self.assertRaises(StoreError):
            await self.service.command(self.identity, "execute", token)
        self.helper.execute.assert_not_awaited()
        self.assertEqual(self.activity()["idle_duration_ms"], 0)

    async def test_missing_prepare_is_tombstoned_before_claim_release(self):
        status = await self.service.command(self.identity, "status")
        self.assertEqual(status["state"], "absent")
        status = await self.service.command(self.identity, "finish")
        self.assertEqual(status["state"], "skipped")
        # A request delayed in transport can no longer create a prepared lease.
        delayed = await self.service.command(self.identity, "prepare")
        self.assertEqual(delayed["state"], "skipped")
        await self.service.command(self.identity, "execute", "")
        self.helper.execute.assert_not_awaited()

    async def test_transfer_gets_hold_lease_but_passive_observers_do_not(self):
        active = []

        async def app(_scope, _receive, _send):
            active.append(activity_present(self.root))

        middleware = WorkspaceAdmissionMiddleware(app, self.root)
        for path in (
            "/agents-workspaces/agent/file",
            "/agents-workspaces/agent/preview",
            "/agents-workspaces/agent/workbook",
            "/bundles/exports/transfer/parts/1",
            "/bundles/task-exports/transfer/parts/1",
            "/marketplace/agents/agent/export",
            "/agents/agent/custom-page/export",
        ):
            await middleware(
                {"type": "http", "method": "GET", "path": "/xnobrain/api/runtime/v1" + path},
                None,
                None,
            )
        self.assertEqual(active, [True] * 7)
        epoch = self.activity()["activity_epoch"]
        active.clear()
        for path in ("/health", "/tasks", "/events", "/agents-workspaces/agent"):
            await middleware(
                {"type": "http", "method": "GET", "path": "/xnobrain/api/runtime/v1" + path},
                None,
                None,
            )
        self.assertEqual(active, [False] * 4)
        self.assertEqual(self.activity()["activity_epoch"], epoch)

    async def test_untracked_detached_process_prevents_idle(self):
        self.service.coverage = lambda: False
        self.assertEqual(self.activity()["state"], "unknown")
        with self.assertRaises(StoreError):
            await self.prepare()
        self.service.coverage = lambda: True
        self.assertEqual(self.activity()["idle_duration_ms"], 0)

    async def test_active_work_and_unknown_coverage_never_prepare(self):
        with WorkspaceActivity(self.root):
            self.now += 60_000_000_000
            with self.assertRaises(StoreError):
                await self.prepare()
        self.platform.runtime_updates._active_counts = lambda: 1 / 0
        value = self.activity()
        self.assertFalse(value["coverage_complete"])
        with self.assertRaises(StoreError):
            await self.prepare()

    async def test_returning_user_blocked_only_until_actual_helper_exit(self):
        entered, complete = asyncio.Event(), asyncio.Event()

        async def execute(*_args):
            self.helper.stopped.return_value = False
            entered.set()
            await complete.wait()
            self.helper.stopped.return_value = True

        self.helper.execute.side_effect = execute
        token = await self.prepare()
        await self.service.command(self.identity, "execute", token)
        # Status arriving before the socket call cannot clear the gate.
        before = await self.service.command(self.identity, "status")
        self.assertTrue(before["paused"])
        await entered.wait()
        with self.assertRaises(StoreError) as blocked:
            WorkspaceActivity(self.root)
        self.assertEqual(blocked.exception.code, "workspace_memory_maintenance")
        retry = await self.service.command(self.identity, "execute", token)
        self.assertEqual(retry["state"], "executing")
        complete.set()
        await self.service._task
        with WorkspaceActivity(self.root):
            pass
        self.helper.execute.assert_awaited_once()
        status = await self.service.command(self.identity, "finish")
        self.assertEqual(status["state"], "outcome_unknown")
        self.assertFalse(status["paused"])

    async def test_rpc_cancellation_does_not_cancel_accepted_execution(self):
        token = await self.prepare()
        response = await self.service.command(self.identity, "execute", token)
        self.assertTrue(response["paused"])
        await self.service._task
        await self.service.command(self.identity, "execute", token)
        self.helper.execute.assert_awaited_once()

    async def test_restart_keeps_fence_until_cgroup_is_empty(self):
        token = await self.prepare()
        self.helper.stopped.return_value = True
        await self.service.command(self.identity, "execute", token)
        # Simulate process death before socket activation, retain durable intent.
        self.service._task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await self.service._task
        recovered = self.new_service()
        self.helper.stopped.return_value = False
        status = await recovered.command(self.identity, "status")
        self.assertTrue(status["paused"])
        self.helper.stopped.return_value = True
        self.helper.receipt = lambda: {}
        status = await recovered.command(self.identity, "status")
        self.assertTrue(status["paused"], "queued activation without root intent is unresolved")
        self.helper.receipt = lambda: {
            "operation_id": "mem_test",
            "fence": 1,
            "guest_boot_id": self.identity.guest_boot_id,
            "state": "executing",
        }
        status = await recovered.command(self.identity, "status")
        self.assertFalse(status["paused"])
        self.assertEqual(status["state"], "outcome_unknown")
        self.helper.execute.assert_not_awaited()

    async def test_disabled_feature_retains_finish_and_epoch_deduplication(self):
        token = await self.prepare()
        self.helper.receipt = lambda: {
            "operation_id": "mem_test",
            "fence": 1,
            "guest_boot_id": self.identity.guest_boot_id,
            "state": "evicted",
            "cached_after_bytes": 1,
        }
        await self.service.command(self.identity, "execute", token)
        await self.service._task
        with patch.dict(os.environ, {"FT_ENABLE_WORKSPACE_IDLE_MEMORY_RECLAIM": "false"}):
            status = await self.service.command(self.identity, "finish")
            self.assertEqual(status["state"], "evicted")
        with self.assertRaises(StoreError):
            await self.service.command(self.new_identity(2, "mem_next"), "prepare")

    async def test_policy_identity_backend_and_helper_fail_closed(self):
        bad = pb.WorkspaceMemoryPolicy(revision="wrong", idle_seconds=30, scan_seconds=5)
        with self.assertRaises(StoreError):
            self.service.activity("workspace", bad)
        with self.assertRaises(StoreError):
            self.service.activity("other", self.policy)
        self.service.backend = lambda: ""
        with self.assertRaises(StoreError):
            await self.prepare()
        self.helper.stopped.return_value = False
        with self.assertRaises(StoreError):
            await self.prepare()
        self.assertFalse(
            self.platform.runtime_updates.repository.maintenance().get("dispatch_paused")
        )

    def test_omitted_policy_defaults_to_ten_minutes(self):
        with patch.dict(os.environ, {}, clear=True):
            value = policy_values()
        self.assertEqual((value["idle_seconds"], value["scan_seconds"]), (600, 60))
