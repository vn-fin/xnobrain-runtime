"""Task HTTP translation uses trusted context and returns durable acceptance."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from starlette.requests import Request

from xnobrain.handlers.api import APIHandlers
from xnobrain.repositories.files import FileRepository
from xnobrain.services.portability import PortabilityService
from xnobrain.services.portability_tasks import PortabilityTasks


class PortabilityTaskHandlersTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.environment = patch.dict(
            "os.environ",
            {
                "RUNTIME_INTERNAL_SERVICE_TOKEN": "",
                "RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": "",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        root = Path(self.temporary.name)
        repository = FileRepository(root / "data", root / "profiles")
        self.tasks = PortabilityTasks(PortabilityService(repository, root / "hermes"))
        self.handlers = APIHandlers(SimpleNamespace(portability_tasks=self.tasks))

    @staticmethod
    def request(operation, *, task_id=None):
        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/",
                "query_string": b"",
                "headers": [(b"idempotency-key", b"same-request")],
                "route": SimpleNamespace(name=operation),
                "path_params": {"task_id": task_id},
            }
        )

    async def test_admission_and_replay_return_202_with_same_id(self):
        first = await self.handlers.bundle_task(
            self.request("bundle_task_create"), {"agent_ids": ["profile"]}
        )
        second = await self.handlers.bundle_task(
            self.request("bundle_task_create"), {"agent_ids": ["profile"]}
        )
        self.assertEqual(first.status_code, 202)
        task = json.loads(first.body)["data"]
        self.assertEqual(task["task_id"], json.loads(second.body)["data"]["task_id"])
        response = await self.handlers.bundle_task(
            self.request("bundle_task_get", task_id=task["task_id"]), {}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.body)["data"]["status"], "PENDING")
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")

    async def test_managed_request_without_verified_identity_is_denied(self):
        with patch.dict("os.environ", {"RUNTIME_INTERNAL_SERVICE_TOKEN": "test-only"}):
            response = await self.handlers.bundle_task(
                self.request("bundle_task_create"), {"agent_ids": ["profile"]}
            )
        self.assertEqual(response.status_code, 403)

    async def test_import_requires_key_and_returns_persistent_acceptance(self):
        request = self.request("bundle_task_import")
        request.scope["headers"] = []
        response = await self.handlers.bundle_task(request, {"upload_id": "upload"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.body)["error"]["code"], "idempotency_key_required")
        upload = self.tasks.portability.upload_root / "upload"
        upload.mkdir()
        (upload / "bundle.zip").write_bytes(b"test fixture")
        self.tasks.portability.repository.atomic_json(
            upload / "metadata.json",
            {
                "complete": True,
                "sha256": "fixture-digest",
            },
        )
        first = await self.handlers.bundle_task(
            self.request("bundle_task_import"), {"upload_id": "upload"}
        )
        replay = await self.handlers.bundle_task(
            self.request("bundle_task_import"), {"upload_id": "upload"}
        )
        self.assertEqual(first.status_code, 202)
        self.assertEqual(
            json.loads(first.body)["data"]["task_id"], json.loads(replay.body)["data"]["task_id"]
        )

    async def test_workspace_stream_delivers_persisted_snapshot_failure(self):
        from unittest.mock import Mock

        task, _ = self.tasks.create_export(
            {"agent_ids": ["profile"]}, scope="standalone", actor="owner"
        )
        claim = self.tasks.store.claim("worker")
        self.tasks.store.finish(
            claim["id"],
            "worker",
            claim["fence"],
            error={
                "code": "snapshot_generation_failed",
                "message": "Snapshot generation failed",
                "retryable": False,
            },
        )
        service = self.handlers.service
        service.agent_activity = lambda: {"agents": {}}
        service.sandbox = lambda _action: {}
        service.kanban = Mock()
        service.kanban.board_event_cursor.return_value = 0
        service.kanban.board_events.return_value = []
        request = self.request("workspace_event_stream")
        response = await self.handlers.workspace_event_stream(request)
        iterator = response.body_iterator
        try:
            import asyncio

            async with asyncio.timeout(3):
                async for message in iterator:
                    if "event: bundle.task.updated" in message:
                        payload = json.loads(message.split("data: ", 1)[1].strip())
                        self.assertEqual(payload["task_id"], task["task_id"])
                        self.assertEqual(payload["status"], "FAILED")
                        self.assertIn("event_id", payload)
                        self.assertNotIn("input_json", payload)
                        break
        finally:
            await iterator.aclose()

    async def test_reconnected_stream_delivers_completed_snapshot_download_descriptor(self):
        import asyncio
        import time
        from unittest.mock import Mock

        task, _ = self.tasks.create_export(
            {"agent_ids": ["profile"]}, scope="standalone", actor="owner"
        )
        claim = self.tasks.store.claim("worker")
        artifact = self.tasks.artifacts / task["export_id"]
        artifact.mkdir()
        (artifact / "bundle.zip").write_bytes(b"snapshot")
        result = {
            "export_id": task["export_id"],
            "filename": "snapshot.zip",
            "size": 8,
            "sha256": "fixture-digest",
            "chunk_size": 512 * 1024,
            "total_parts": 1,
            "expires_at_epoch": time.time() + 3600,
            "download_parts_url_template": (
                f"/xnobrain/api/runtime/v1/bundles/task-exports/{task['export_id']}/parts/{{part_number}}"
            ),
        }
        self.tasks.store.finish(claim["id"], "worker", claim["fence"], result=result)
        # A separate actor's task must never seed this subscriber's stream.
        foreign, _ = self.tasks.create_export(
            {"agent_ids": ["private"]}, scope="standalone", actor="foreign"
        )
        service = self.handlers.service
        service.agent_activity = lambda: {"agents": {}}
        service.sandbox = lambda _action: {}
        service.kanban = Mock()
        service.kanban.board_event_cursor.return_value = 0
        service.kanban.board_events.return_value = []
        for _connection in range(2):
            response = await self.handlers.workspace_event_stream(
                self.request("workspace_event_stream")
            )
            iterator = response.body_iterator
            observed = False
            try:
                async with asyncio.timeout(3):
                    async for message in iterator:
                        if "event: bundle.task.updated" not in message:
                            continue
                        payload = json.loads(message.split("data: ", 1)[1].strip())
                        self.assertNotEqual(payload["task_id"], foreign["task_id"])
                        self.assertEqual(payload["task_id"], task["task_id"])
                        self.assertEqual(payload["status"], "COMPLETED")
                        self.assertEqual(payload["result"]["export_id"], task["export_id"])
                        self.assertEqual(
                            payload["result"]["download_parts_url_template"],
                            result["download_parts_url_template"],
                        )
                        self.assertTrue(payload["result"]["artifact_available"])
                        self.assertNotIn("input_json", payload)
                        observed = True
                        break
            finally:
                await iterator.aclose()
            self.assertTrue(observed)
