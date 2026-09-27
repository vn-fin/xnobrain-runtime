"""Indexed uploads remain durable while handlers release the HTTP event loop."""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import FastAPI, Request

from xnobrain.handlers.api import APIHandlers
from xnobrain.handlers.operations.portability import operations
from xnobrain.repositories.files import FileRepository
from xnobrain.services.portability import CHUNK_SIZE, PortabilityService


class BundleUploadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        environment = patch.dict("os.environ", {"RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": ""})
        environment.start()
        self.addCleanup(environment.stop)
        root = Path(temporary.name)
        repository = FileRepository(root / "data", root / "profiles")
        self.service = PortabilityService(repository, root / "hermes")
        platform = SimpleNamespace(
            start_bundle_upload=self.service.start_upload,
            put_bundle_upload_part=self.service.put_upload_part,
            complete_bundle_upload=self.service.complete_upload,
            delete_bundle_transfer=self.service.delete_transfer,
        )
        handlers = APIHandlers(platform)
        handlers._operation = lambda name, request, body: operations(handlers, request, body)[name]
        app = FastAPI()

        @app.get("/ping")
        async def ping():
            return {"ok": True}

        @app.post("/uploads", name="bundle_upload_start")
        @app.post("/uploads/{transfer_id}/complete", name="bundle_upload_complete")
        async def dispatch(request: Request, body: dict):
            return await handlers.dispatch(request, body)

        @app.delete("/uploads/{transfer_id}", name="bundle_upload_delete")
        async def delete(request: Request):
            return await handlers.dispatch(request, {})

        app.add_api_route(
            "/uploads/{transfer_id}/parts/{part_number}",
            handlers.bundle_part,
            methods=["PUT"],
            name="bundle_upload_part",
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )
        self.addAsyncCleanup(self.client.aclose)

    async def admit(self, payload):
        response = await self.client.post(
            "/uploads", json={"size": len(payload), "filename": "test.zip"}
        )
        self.assertEqual(response.status_code, 201)
        return response.json()["data"]["upload_id"]

    async def put(self, upload, index, payload, digest=None):
        return await self.client.put(
            f"/uploads/{upload}/parts/{index}",
            content=payload,
            headers={"X-Part-SHA256": digest or hashlib.sha256(payload).hexdigest()},
        )

    async def test_out_of_order_replay_and_exact_assembly(self):
        payload = b"a" * CHUNK_SIZE + b"b" * CHUNK_SIZE + b"end"
        upload = await self.admit(payload)
        for index in (2, 0, 2, 1):
            part = payload[index * CHUNK_SIZE : (index + 1) * CHUNK_SIZE]
            self.assertEqual((await self.put(upload, index, part)).status_code, 201)
        with patch.object(self.service, "dry_run_file", return_value={"verified": True}):
            response = await self.client.post(f"/uploads/{upload}/complete", json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual((self.service.upload_root / upload / "bundle.zip").read_bytes(), payload)
        self.assertEqual(response.json()["data"]["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual((await self.put(upload, 0, payload[:CHUNK_SIZE])).status_code, 409)

    async def test_bad_checksum_size_index_and_missing_parts_are_rejected(self):
        upload = await self.admit(b"abc")
        for index, part, digest in [(0, b"abc", "0" * 64), (0, b"ab", None), (1, b"abc", None)]:
            response = await self.put(upload, index, part, digest)
            self.assertGreaterEqual(response.status_code, 400)
        response = await self.client.post(f"/uploads/{upload}/complete", json={})
        self.assertEqual(response.json()["error"]["code"], "missing_upload_part")

    async def test_slow_write_releases_event_loop_and_keeps_lock_after_cancellation(self):
        upload = await self.admit(b"abc")
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        original = self.service.repository.atomic_write

        def blocked(*args, **kwargs):
            entered.set()
            try:
                if not release.wait(5):
                    raise TimeoutError("test write barrier")
                return original(*args, **kwargs)
            finally:
                finished.set()

        with patch.object(self.service.repository, "atomic_write", side_effect=blocked):
            task = asyncio.create_task(self.put(upload, 0, b"abc"))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                response = await asyncio.wait_for(self.client.get("/ping"), 0.5)
                self.assertEqual(response.status_code, 200)
                self.assertFalse(task.done())
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                response = await self.client.delete(f"/uploads/{upload}")
                self.assertEqual(response.status_code, 429)
                self.assertEqual(response.json()["error"]["code"], "task_storage_busy")
            finally:
                release.set()
                await asyncio.to_thread(finished.wait, 3)
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual((await self.client.delete(f"/uploads/{upload}")).status_code, 200)

    async def test_admission_preview_and_delete_run_outside_event_loop(self):
        for operation, method, path, body in [
            ("start_upload", "POST", "/uploads", {"size": 3}),
            ("complete_upload", "POST", "/uploads/example/complete", {}),
            ("delete_transfer", "DELETE", "/uploads/example", None),
        ]:
            with self.subTest(operation=operation):
                entered, release = threading.Event(), threading.Event()

                def blocked(*_args):
                    entered.set()
                    if not release.wait(5):
                        raise TimeoutError("test operation barrier")
                    return {}

                # Use the public dispatch contract with a narrow service fixture.
                service_name = {
                    "start_upload": "start_bundle_upload",
                    "complete_upload": "complete_bundle_upload",
                    "delete_transfer": "delete_bundle_transfer",
                }[operation]
                platform = SimpleNamespace(**{service_name: blocked})
                handlers = APIHandlers(platform)
                request = Request(
                    {
                        "type": "http",
                        "method": method,
                        "path": path,
                        "headers": [],
                        "query_string": b"",
                        "path_params": {"transfer_id": "example"},
                        "route": SimpleNamespace(
                            name={
                                "start_upload": "bundle_upload_start",
                                "complete_upload": "bundle_upload_complete",
                                "delete_transfer": "bundle_upload_delete",
                            }[operation]
                        ),
                    }
                )
                handlers._operation = lambda name, req, data: operations(handlers, req, data)[name]
                task = asyncio.create_task(handlers.dispatch(request, body or {}))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                    self.assertEqual(
                        (await asyncio.wait_for(self.client.get("/ping"), 0.5)).status_code, 200
                    )
                    self.assertFalse(task.done())
                finally:
                    release.set()
                    await task
