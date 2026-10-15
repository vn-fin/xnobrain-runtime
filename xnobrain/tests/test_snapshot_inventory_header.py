"""Snapshot metadata fits relay limits without changing the archived inventory."""

import hashlib
import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

from starlette.requests import Request

from xnobrain.handlers.portability import PortabilityHandlers, _snapshot_inventory_header
from xnobrain.repositories.base import StoreError


class SnapshotInventoryHeaderTests(unittest.IsolatedAsyncioTestCase):
    def test_small_inventory_keeps_all_paths(self):
        inventory = {"file_count": 3, "excluded_paths": ["profiles/test/.env"]}
        summary = json.loads(_snapshot_inventory_header(inventory))
        self.assertEqual(summary["excluded_paths"], inventory["excluded_paths"])
        self.assertEqual(summary["excluded_path_count"], 1)
        self.assertFalse(summary["excluded_paths_truncated"])
        self.assertNotIn("excluded_path_count", inventory)

    def test_localized_paths_fit_ascii_wire_budget(self):
        paths = [f"profiles/test/{'資料' * 150}/{index}.log" for index in range(100)]
        inventory = {"excluded_paths": paths, "file_count": 4}
        encoded = _snapshot_inventory_header(inventory)
        self.assertLessEqual(len(encoded.encode("ascii")), 8000)
        summary = json.loads(encoded)
        self.assertEqual(summary["excluded_path_count"], 100)
        self.assertTrue(summary["excluded_paths_truncated"])
        self.assertEqual(summary["excluded_paths"], paths[: len(summary["excluded_paths"])])
        self.assertEqual(inventory["excluded_paths"], paths)

    def test_oversized_non_path_metadata_fails_closed(self):
        with self.assertRaises(StoreError):
            _snapshot_inventory_header({"unexpected": "x" * 9000, "excluded_paths": []})

    async def test_export_stream_keeps_full_manifest_and_exact_digest(self):
        paths = [f"profiles/test/logs/{'x' * 80}-{index}.log" for index in range(200)]
        inventory = {"file_count": 1, "conversation_count": 0, "excluded_paths": paths}
        with tempfile.TemporaryDirectory() as directory:
            transfer_root = Path(directory)

            def export_shareable(agent_id, target, *, owner):
                self.assertEqual(agent_id, "test")
                with ZipFile(target, "w") as archive:
                    archive.writestr("manifest.json", json.dumps({"excluded_paths": paths}))
                data = target.read_bytes()
                return {
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size": len(data),
                    "inventory": inventory,
                }

            handler = PortabilityHandlers()
            handler.service = SimpleNamespace(
                portability=SimpleNamespace(
                    transfer_root=transfer_root,
                    export_shareable=export_shareable,
                )
            )
            request = Request(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/",
                    "headers": [],
                    "route": SimpleNamespace(name="community_snapshot_export"),
                    "path_params": {"agent_id": "test"},
                }
            )
            with patch("xnobrain.handlers.portability.snapshot_authorized", return_value=True):
                response = await handler.community_snapshot(request)
            header = response.headers["X-Snapshot-Inventory"]
            self.assertLessEqual(len(header.encode("ascii")), 8000)
            summary = json.loads(header)
            self.assertTrue(summary["excluded_paths_truncated"])
            self.assertEqual(summary["excluded_path_count"], len(paths))
            self.assertEqual(summary["file_count"], 1)
            payload = b"".join([chunk async for chunk in response.body_iterator])
            self.assertEqual(
                hashlib.sha256(payload).hexdigest(), response.headers["X-Snapshot-SHA256"]
            )
            self.assertEqual(len(payload), int(response.headers["X-Snapshot-Size"]))
            with ZipFile(BytesIO(payload)) as archive:
                self.assertEqual(json.loads(archive.read("manifest.json"))["excluded_paths"], paths)
            self.assertEqual(list(transfer_root.iterdir()), [])
