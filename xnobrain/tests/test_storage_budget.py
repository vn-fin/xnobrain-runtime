"""Shared soft budget: trusted samples, fail closed and cleanup admission."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from xnobrain.integrations.kanban_admission import dispatch_scope
from xnobrain.integrations.native_admission import NativeAdmission, native_root
from xnobrain.integrations.rebalance_admission import WorkspaceAdmissionMiddleware
from xnobrain.integrations.runtime import LocalRuntimeManager
from xnobrain.services.agents import AgentsServiceMixin
from xnobrain.services.base import ServiceError
from xnobrain.services.portability_worker import PortabilityWorker
from xnobrain.services.runtime_updates import RuntimeUpdateService
from xnobrain.services.storage_budget import require_storage_growth, storage_budget

GIB = 1 << 30


def sample(root=10 * GIB, data=19 * GIB):
    return {
        "root": {"private_used_bytes": root, "shared_bytes": 8 * GIB},
        "data": {"private_used_bytes": data, "shared_bytes": 5 * GIB},
    }


class StorageBudgetTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"RUNTIME_STORAGE_SOFT_LIMIT_BYTES": str(30 * GIB)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.read = patch.object(
            LocalRuntimeManager, "_private_storage_sample", return_value=sample()
        )
        self.reader = self.read.start()
        self.addCleanup(self.read.stop)

    def test_shared_blocks_excluded_and_exact_boundary_blocks(self):
        self.assertEqual(storage_budget()["used_bytes"], 29 * GIB)
        require_storage_growth()
        self.reader.return_value = sample(data=20 * GIB)
        with self.assertRaises(ServiceError) as caught:
            require_storage_growth()
        self.assertEqual(
            (caught.exception.status, caught.exception.code),
            (507, "workspace_storage_budget_exceeded"),
        )
        self.reader.return_value = sample(data=GIB)
        require_storage_growth()  # Automatically resumes after a fresh lower sample.

    def test_missing_partial_invalid_samples_never_become_zero(self):
        for value in ({}, {"root": sample()["root"]}, sample(root=-1), sample(root=True)):
            with self.subTest(value=value):
                self.reader.return_value = value
                self.assertIsNone(storage_budget()["used_bytes"])
                with self.assertRaises(ServiceError) as caught:
                    require_storage_growth()
                self.assertEqual(caught.exception.status, 503)

    def test_legacy_disabled_and_invalid_policy_fail_closed(self):
        for value in ("0", ""):
            with patch.dict(os.environ, {"RUNTIME_STORAGE_SOFT_LIMIT_BYTES": value}):
                if value == "0":
                    require_storage_growth()
                    self.assertFalse(storage_budget()["enabled"])
                else:
                    with self.assertRaises(ServiceError):
                        require_storage_growth()
        self.reader.assert_not_called()

    def test_background_dispatch_blocked_but_cleanup_gate_available(self):
        self.reader.return_value = sample(data=21 * GIB)
        fake = SimpleNamespace(
            platform=SimpleNamespace(
                repository=SimpleNamespace(data_dir=Path("/unused"), storage_mount=Mock())
            )
        )
        with self.assertRaises(ServiceError):
            RuntimeUpdateService.require_dispatch(fake)
        with patch("xnobrain.services.runtime_updates.require_admission") as maintenance:
            RuntimeUpdateService.require_dispatch(fake, storage_growth=False)
            maintenance.assert_called_once()

    def test_native_claim_does_not_consume_work_when_full(self):
        self.reader.return_value = sample(data=21 * GIB)
        with tempfile.TemporaryDirectory() as directory:
            gate = NativeAdmission(Path(directory), "job", "cron")
            with patch.object(gate.store, "native_get") as get:
                self.assertFalse(gate.poll())
                get.assert_not_called()
        with patch("xnobrain.integrations.native_admission.managed", return_value=False):
            with native_root(Path("/unused"), "job", "cron") as admitted:
                self.assertFalse(admitted)

    def test_kanban_keeps_task_unclaimed_when_full(self):
        self.reader.return_value = sample(data=21 * GIB)
        claim = Mock()
        kb = SimpleNamespace(claim_task=claim, claim_review_task=Mock())
        with (
            patch("xnobrain.integrations.kanban_admission.managed", return_value=False),
            patch("xnobrain.integrations.kanban_delegation.binding", return_value=None),
            dispatch_scope(kb, Path("/unused"), "board"),
        ):
            self.assertIsNone(kb.claim_task(None, "task"))
        claim.assert_not_called()

    def test_agent_creation_rejected_before_profile_write(self):
        self.reader.return_value = sample(data=21 * GIB)
        with self.assertRaises(ServiceError):
            AgentsServiceMixin.create_agent(None, {"display_name": "new"})


class StorageHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_quota_rejects_upload_before_reading_body_but_allows_cleanup(self):
        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        with tempfile.TemporaryDirectory() as directory:
            middleware = WorkspaceAdmissionMiddleware(app, Path(directory))
            for method, path, expected in (
                ("PUT", "/bundles/uploads/id/parts/1", 507),
                ("POST", "/agents", 507),
                ("GET", "/agents-workspaces/a/file", 204),
                ("DELETE", "/agents/a", 204),
                ("POST", "/agents-workspaces/a/delete", 204),
                ("POST", "/agents-workspaces/a/read", 204),
                ("POST", "/teams/t/runs/r/cancel", 204),
                ("POST", "/system/update/abort", 204),
                ("POST", "/sessions/s/runs/r/approval", 204),
                ("POST", "/conversations/s/runs/r/approval", 204),
            ):
                receive, send = AsyncMock(), AsyncMock()
                with (
                    patch.dict(os.environ, {"RUNTIME_STORAGE_SOFT_LIMIT_BYTES": str(30 * GIB)}),
                    patch.object(
                        LocalRuntimeManager,
                        "_private_storage_sample",
                        return_value=sample(data=21 * GIB),
                    ),
                ):
                    await middleware(
                        {
                            "type": "http",
                            "method": method,
                            "path": "/xnobrain/api/runtime/v1" + path,
                        },
                        receive,
                        send,
                    )
                self.assertEqual(send.call_args_list[0].args[0]["status"], expected, path)
                self.assertNotIn(
                    b"x-xnobrain-maintenance", dict(send.call_args_list[0].args[0]["headers"])
                )
                receive.assert_not_called()

    async def test_portability_waits_without_claiming(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Mock(path=Path(directory) / "tasks.sqlite")
            worker = PortabilityWorker(store, Mock())
            with patch(
                "xnobrain.services.portability_worker.require_storage_growth",
                side_effect=ServiceError("full", status=507),
            ):
                with self.assertRaises(ServiceError):
                    await worker._claim_and_execute()
            store.claim.assert_not_called()
