"""Relocation fencing and graceful admission across tasks and processes."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import grpc
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from xnobrain.integrations.rebalance_admission import WorkspaceAdmissionMiddleware
from xnobrain.integrations.runtime_gateway import RuntimeGatewayService
from xnobrain.repositories.base import RepositoryBase, StoreError
from xnobrain.repositories.runtime_update_gate import WorkspaceActivity, activity_present
from xnobrain.repositories.runtime_updates import RuntimeUpdateRepository
from xnobrain.runtime.v1 import runtime_gateway_pb2 as pb
from xnobrain.runtime.v1 import runtime_gateway_pb2_grpc as rpc
from xnobrain.services.runtime_rebalances import RuntimeRebalanceService


class RuntimeRebalanceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "data"
        self.root.mkdir()
        self.repository = RepositoryBase(self.root, Path(temporary.name) / "profiles")
        self.platform = SimpleNamespace(
            repository=self.repository,
            runtime_updates=SimpleNamespace(
                repository=RuntimeUpdateRepository(self.repository),
                _active_counts=Mock(return_value={"total": 0}),
            ),
            router=SimpleNamespace(_request=AsyncMock(return_value={"data": []})),
            organization_connector=SimpleNamespace(dispatch_paused=False),
        )
        self.environment = patch.dict(
            os.environ,
            {
                "RUNTIME_WORKSPACE_ID": "workspace",
                "FT_ENABLE_VM_REBALANCE": "true",
                "RUNTIME_DATA_DIR": str(self.root),
                "DATA_DIR": str(self.root),
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        identity = patch(
            "xnobrain.services.runtime_rebalances.incus_installed_identity",
            return_value={"build_descriptor_digest": "build-identity"},
        )
        self.installed = identity.start()
        self.addCleanup(identity.stop)
        self.service = RuntimeRebalanceService(self.platform)

    @staticmethod
    def identity(phase="preparing", fence=1, operation="reb_test"):
        return SimpleNamespace(
            workspace_id="workspace",
            operation_id=operation,
            fence=fence,
            step_id=f"{operation}:{phase}",
        )

    async def test_drain_preserves_existing_lineage_and_blocks_new_work(self):
        lease = WorkspaceActivity(self.root)
        try:
            status = await self.service.command(self.identity(), "prepare")
            self.assertTrue(status["paused"])
            self.assertFalse(status["idle"])
            with self.assertRaises(StoreError) as blocked:
                WorkspaceActivity(self.root)
            self.assertEqual(blocked.exception.code, "workspace_rebalance_maintenance")
            # Already admitted work can finish nested file/tool operations.
            with lease.lineage(), WorkspaceActivity(self.root):
                (self.root / "result.txt").write_text("completed", encoding="utf-8")
        finally:
            lease.close()
        status = await self.service.command(self.identity("migrating"), "status")
        self.assertTrue(status["idle"])
        self.assertEqual((self.root / "result.txt").read_text(), "completed")

    async def test_restart_duplicate_resume_and_stale_fences(self):
        first = await self.service.command(self.identity(), "prepare")
        self.assertEqual(await self.service.command(self.identity(), "prepare"), first)
        recovered = RuntimeRebalanceService(self.platform)
        status = await recovered.command(self.identity("resuming"), "resume")
        self.assertTrue(status["resumed"])
        self.assertEqual(await recovered.command(self.identity("resuming"), "resume"), status)
        with self.assertRaises(StoreError):
            await recovered.command(self.identity(), "prepare")
        await recovered.command(self.identity(fence=2, operation="reb_next"), "prepare")
        with self.assertRaises(StoreError):
            await recovered.command(self.identity("resuming"), "resume")
        self.assertTrue(self.platform.runtime_updates.repository.maintenance()["dispatch_paused"])

    async def test_changed_build_cannot_overwrite_receipt_or_resume(self):
        await self.service.command(self.identity(), "prepare")
        self.installed.return_value = {"build_descriptor_digest": "different"}
        for action, phase in (("prepare", "preparing"), ("resume", "resuming")):
            with self.assertRaises(StoreError):
                await self.service.command(self.identity(phase), action)
        self.assertEqual(self.service._receipt()["build_identity"], "build-identity")
        self.assertTrue(self.platform.runtime_updates.repository.maintenance()["dispatch_paused"])

    async def test_prepare_and_resume_recover_between_durable_writes(self):
        # A receipt survives process failure before the admission write. Retrying
        # the same operation completes publication without changing its fence.
        with patch.object(self.service.repository, "save_maintenance", side_effect=OSError()):
            with self.assertRaises(OSError):
                await self.service.command(self.identity(), "prepare")
        self.assertEqual(self.service._receipt()["operation_id"], "reb_test")
        self.assertFalse(self.service.repository.maintenance())
        recovered = RuntimeRebalanceService(self.platform)
        self.assertTrue((await recovered.command(self.identity(), "prepare"))["paused"])
        # Resume records its obligation first. A failed gate release must remain
        # paused until another process completes the same idempotent command.
        with patch.object(recovered.repository, "save_maintenance", side_effect=OSError()):
            with self.assertRaises(OSError):
                await recovered.command(self.identity("resuming"), "resume")
        self.assertTrue(recovered._receipt()["resumed"])
        observed = await recovered.command(self.identity("resuming"), "status")
        self.assertTrue(observed["paused"])
        self.assertFalse(observed["resumed"])
        restarted = RuntimeRebalanceService(self.platform)
        self.assertTrue((await restarted.command(self.identity("resuming"), "resume"))["resumed"])

    async def test_paused_schedule_does_not_claim_or_replay_on_duplicate_resume(self):
        from xnobrain.services.cron import CronService

        cron = CronService(self.repository, Mock())
        cron.dispatch_allowed = Mock(return_value=False)
        await self.service.command(self.identity(), "prepare")
        with self.assertRaises(StoreError) as blocked:
            cron.fire_due("profile", "due-job")
        self.assertEqual(blocked.exception.code, "workspace_rebalance_maintenance")
        cron.dispatch_allowed.assert_not_called()
        for _ in range(2):
            await self.service.command(self.identity("resuming"), "resume")
        # Releasing maintenance never invokes a scheduler or replays its work.
        cron.dispatch_allowed.assert_not_called()

    async def test_passive_cron_view_cannot_deliver_but_admitted_lineage_can_finish(self):
        from xnobrain.services.cron import CronService

        cron = CronService(self.repository, Mock())
        job = {"id": "job", "xnobrain_delivery_targets": [{"id": "target", "target_type": "file"}]}
        jobs = [("profile", [job])]
        cron._jobs_by_profile = Mock(return_value=jobs)
        cron._executions = Mock(return_value=[{"id": "execution", "status": "completed"}])
        cron._execution_output = Mock(return_value="finished")
        cron._deliver_execution = Mock(return_value={"status": "delivered"})
        cron._snapshot_store = Mock()
        cron._native = Mock()
        cron._dto = Mock(side_effect=lambda _profile, current: dict(current))
        admitted = WorkspaceActivity(self.root)
        try:
            await self.service.command(self.identity(), "prepare")
            self.assertEqual(cron.list_jobs(), [job])
            cron._deliver_execution.assert_not_called()
            cron._native.assert_not_called()
            with admitted.lineage():
                cron.reconcile_deliveries(jobs)
            cron._deliver_execution.assert_called_once()
            cron._native.assert_called_once()
            self.assertEqual(job["xnobrain_delivery_records"][0]["status"], "delivered")
        finally:
            admitted.close()
        for _ in range(2):
            await self.service.command(self.identity("resuming"), "resume")
        self.assertEqual(cron.list_jobs(), [job])
        cron._deliver_execution.assert_called_once()

    async def test_passive_cron_view_defers_legacy_pin_writes_until_resume(self):
        from xnobrain.services.cron import CronService

        cron = CronService(self.repository, Mock())
        job = {"id": "job", "model": "legacy/model", "provider": "legacy"}
        cron._snapshot_store = Mock()
        cron._native = Mock(return_value=job)
        await self.service.command(self.identity(), "prepare")
        with (
            patch("xnobrain.services.cron._legacy_pin_needs_normalize", return_value=True),
            patch("xnobrain.services.cron._router_pin_override", return_value={"model": "routed"}),
        ):
            self.assertIs(cron._normalize_legacy_pin("profile", job), job)
            cron._snapshot_store.assert_not_called()
            cron._native.assert_not_called()
            await self.service.command(self.identity("resuming"), "resume")
            cron._normalize_legacy_pin("profile", job)
        cron._snapshot_store.assert_called_once()
        self.assertEqual(cron._native.call_args_list[0].args[:2], ("profile", "update_job"))

    async def test_dependency_failure_keeps_gate_and_disable_allows_recovery(self):
        await self.service.command(self.identity(), "prepare")
        self.platform.router._request.side_effect = ConnectionError()
        with self.assertRaises(StoreError):
            await self.service.command(self.identity("resuming"), "resume")
        self.platform.router._request.side_effect = None
        with patch.dict(os.environ, {"FT_ENABLE_VM_REBALANCE": "false"}):
            self.assertTrue(
                (await self.service.command(self.identity("resuming"), "resume"))["resumed"]
            )
            with self.assertRaises(StoreError):
                await self.service.command(self.identity(fence=2, operation="reb_next"), "prepare")

    async def test_other_workspace_and_other_maintenance_are_rejected(self):
        wrong = self.identity()
        wrong.workspace_id = "other"
        with self.assertRaises(StoreError):
            await self.service.command(wrong, "prepare")
        self.platform.runtime_updates.repository.save_maintenance(
            {
                "kind": "update",
                "dispatch_paused": True,
                "operation_id": "upd_existing",
            }
        )
        with self.assertRaises(StoreError):
            await self.service.command(self.identity(), "prepare")

    async def test_resume_requires_receipt_and_unchanged_storage(self):
        with self.assertRaises(StoreError):
            await self.service.command(self.identity("resuming"), "resume")
        await self.service.command(self.identity(), "prepare")
        moved = self.root.with_name("replacement")
        self.root.rename(moved)
        self.root.mkdir()
        (moved / "runtime-updates").rename(self.root / "runtime-updates")
        with self.assertRaises(StoreError):
            await self.service.command(self.identity("resuming"), "resume")
        self.assertTrue(self.platform.runtime_updates.repository.maintenance()["dispatch_paused"])

    async def test_private_grpc_authentication_and_error_redaction(self):
        server = grpc.aio.server()
        relay = RuntimeGatewayService("private-test-token", 1, self.service)
        rpc.add_RuntimeGatewayServiceServicer_to_server(relay, server)
        port = server.add_insecure_port("127.0.0.1:0")
        await server.start()
        try:
            async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
                stub = rpc.RuntimeGatewayServiceStub(channel)
                identity = pb.RebalanceIdentity(**vars(self.identity()))
                request = pb.RuntimeGatewayServicePrepareRebalanceRequest(identity=identity)
                for metadata in ((), (("x-xnobrain-internal-token", "wrong"),)):
                    with self.assertRaises(grpc.aio.AioRpcError) as denied:
                        await stub.PrepareRebalance(request, metadata=metadata)
                    self.assertEqual(denied.exception.code(), grpc.StatusCode.UNAUTHENTICATED)
                self.assertFalse(self.platform.runtime_updates.repository.maintenance())
                trusted = (("x-xnobrain-internal-token", "private-test-token"),)
                response = await stub.PrepareRebalance(request, metadata=trusted)
                self.assertTrue(response.status.paused)
                with patch.object(self.service, "command", side_effect=RuntimeError("secret/path")):
                    with self.assertRaises(grpc.aio.AioRpcError) as failure:
                        await stub.PrepareRebalance(request, metadata=trusted)
                    self.assertEqual(failure.exception.code(), grpc.StatusCode.FAILED_PRECONDITION)
                    self.assertNotIn("secret", failure.exception.details())
        finally:
            await server.stop(grace=None)

    async def test_inherited_cli_lease_survives_parent_close(self):
        lease = WorkspaceActivity(self.root)
        env = dict(os.environ, XNOBRAIN_ACTIVITY_FD=str(lease.descriptor))
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "from xnobrain.integrations.rebalance_cli import cli_activity; import sys; "
                "lease=cli_activity(); print('ready',flush=True); sys.stdin.readline(); lease.close()",
            ],
            env=env,
            pass_fds=(lease.descriptor,),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual(await asyncio.to_thread(child.stdout.readline), "ready\n")
            lease.close()
            self.assertTrue(activity_present(self.root))
            child.communicate("done\n", timeout=5)
            self.assertEqual(child.returncode, 0)
            self.assertFalse(activity_present(self.root))
        finally:
            lease.close()
            if child.poll() is None:
                child.kill()
            child.communicate()

    async def test_http_mutation_is_held_until_response_and_never_replayed(self):
        app = FastAPI()
        app.add_middleware(WorkspaceAdmissionMiddleware, root=self.root)
        admitted, finish = asyncio.Event(), asyncio.Event()
        calls = []

        @app.post("/work")
        async def work():
            calls.append("work")
            admitted.set()
            await finish.wait()
            with WorkspaceActivity(self.root):
                return {"ok": True}

        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            active = asyncio.create_task(client.post("/work"))
            await asyncio.wait_for(admitted.wait(), timeout=2)
            status = await self.service.command(self.identity(), "prepare")
            self.assertFalse(status["idle"])
            blocked = await client.post("/work")
            self.assertEqual(blocked.status_code, 503)
            self.assertEqual(blocked.json()["status_code"], 503)
            self.assertEqual(blocked.headers["retry-after"], "5")
            finish.set()
            self.assertEqual((await active).status_code, 200)
        self.assertEqual(calls, ["work"])
        self.assertTrue((await self.service.command(self.identity("migrating"), "status"))["idle"])


if __name__ == "__main__":
    unittest.main()
