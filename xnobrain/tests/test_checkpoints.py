"""Restore-point API and workspace mutation coverage."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from xnobrain.app import XNOBrainApplication
from xnobrain.integrations import AgentManager, GlobalConfigManager


class FakeRouter:
    async def list_connections(self):
        return {"connections": []}

    async def list_models(self):
        return {"data": []}

    async def status(self):
        return {"available": True, "provider_count": 0}


class CheckpointAPITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.root = base / "root"
        self.profiles = base / "profiles"
        self.root.mkdir()
        self.profiles.mkdir()
        (self.root / "config.yaml").write_text(
            yaml.safe_dump(
                {
                    "model": {"provider": "custom:xnobrain", "default": "auto"},
                    "approvals": {"mode": "off"},
                }
            ),
            encoding="utf-8",
        )
        self.environment = patch.dict(
            os.environ,
            {
                "HERMES_HOME": str(self.root),
                "HERMES_ROOT_PROFILE": str(self.root),
                "HERMES_PROFILES_ROOT": str(self.profiles),
                "DATA_DIR": str(base),
            },
        )
        self.environment.start()
        self.app = FastAPI()
        self.composition = XNOBrainApplication(
            AgentManager(
                root_profile=self.root,
                profiles_root=self.profiles,
                legacy_agents_root=base / "legacy",
            ),
            GlobalConfigManager(root_profile=self.root),
            FakeRouter(),
        )
        self.composition.register(self.app)

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def client(self):
        return AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test")

    async def _agent(self, client: AsyncClient, name: str = "Versioned") -> str:
        response = await client.post("/xnobrain/api/runtime/v1/agents", json={"display_name": name})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["data"]["id"]

    async def test_setting_is_profile_local_and_global_patch_rejects_it(self):
        async with self.client() as client:
            first = await self._agent(client, "First")
            second = await self._agent(client, "Second")
            changed = await client.patch(
                f"/xnobrain/api/runtime/v1/agents-configs/{first}",
                json={"checkpoints_enabled": True},
            )
            agents = await client.get("/xnobrain/api/runtime/v1/agents")
            global_change = await client.patch(
                "/xnobrain/api/runtime/v1/agents-configs/global",
                json={"checkpoints_enabled": True},
            )
        self.assertEqual(changed.status_code, 200, changed.text)
        by_id = {item["id"]: item for item in agents.json()["data"]}
        self.assertTrue(by_id[first]["config"]["checkpoints_enabled"])
        self.assertFalse(by_id[second]["config"]["checkpoints_enabled"])
        self.assertEqual(global_change.status_code, 400)
        self.assertNotIn("checkpoints", yaml.safe_load((self.root / "config.yaml").read_text()))

    async def test_new_profile_does_not_inherit_big_brother_enablement(self):
        async with self.client() as client:
            enabled = await client.patch(
                "/xnobrain/api/runtime/v1/agents-configs/big-brother",
                json={"checkpoints_enabled": True},
            )
            agent = await self._agent(client, "Fresh")
            status = await client.get(f"/xnobrain/api/runtime/v1/agents/{agent}/checkpoints/status")
        self.assertEqual(enabled.status_code, 200, enabled.text)
        self.assertFalse(status.json()["data"]["enabled"])

    async def test_write_list_diff_file_versions_and_restore(self):
        async with self.client() as client:
            agent = await self._agent(client)
            disabled = await client.get(
                f"/xnobrain/api/runtime/v1/agents/{agent}/checkpoints/status"
            )
            self.assertFalse(disabled.json()["data"]["enabled"])
            await client.patch(
                f"/xnobrain/api/runtime/v1/agents-configs/{agent}",
                json={"checkpoints_enabled": True},
            )
            await client.post(
                f"/xnobrain/api/runtime/v1/agents-workspaces/{agent}/write",
                json={"path": "notes.txt", "content": "one\n"},
            )
            await client.post(
                f"/xnobrain/api/runtime/v1/agents-workspaces/{agent}/write",
                json={"path": "notes.txt", "content": "two\n"},
            )
            listed = await client.get(f"/xnobrain/api/runtime/v1/agents/{agent}/checkpoints")
            self.assertEqual(listed.status_code, 200, listed.text)
            items = listed.json()["data"]["items"]
            self.assertGreaterEqual(len(items), 2)
            target = items[0]["id"]
            diff = await client.get(
                f"/xnobrain/api/runtime/v1/agents/{agent}/checkpoints/{target}/diff"
            )
            versions = await client.get(
                f"/xnobrain/api/runtime/v1/agents/{agent}/checkpoints/file-versions",
                params={"path": "notes.txt"},
            )
            restored = await client.post(
                f"/xnobrain/api/runtime/v1/agents/{agent}/checkpoints/{target}/restore",
                json={"path": "notes.txt"},
            )
        self.assertEqual(diff.status_code, 200, diff.text)
        self.assertEqual(diff.json()["data"]["files"][0]["path"], "notes.txt")
        self.assertEqual(versions.status_code, 200, versions.text)
        self.assertTrue(any(item["exists"] for item in versions.json()["data"]["items"]))
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual((self.profiles / agent / "workspace" / "notes.txt").read_text(), "one\n")

    async def test_rename_rejects_overwrite_and_is_checkpointed(self):
        async with self.client() as client:
            agent = await self._agent(client)
            await client.patch(
                f"/xnobrain/api/runtime/v1/agents-configs/{agent}",
                json={"checkpoints_enabled": True},
            )
            await client.post(
                f"/xnobrain/api/runtime/v1/agents-workspaces/{agent}/write",
                json={"path": "old.txt", "content": "old"},
            )
            renamed = await client.post(
                f"/xnobrain/api/runtime/v1/agents-workspaces/{agent}/rename",
                json={"path": "old.txt", "new_name": "new.txt"},
            )
            await client.post(
                f"/xnobrain/api/runtime/v1/agents-workspaces/{agent}/write",
                json={"path": "taken.txt", "content": "x"},
            )
            conflict = await client.post(
                f"/xnobrain/api/runtime/v1/agents-workspaces/{agent}/rename",
                json={"path": "new.txt", "new_name": "taken.txt"},
            )
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertEqual(renamed.json()["data"]["path"], "new.txt")
        self.assertEqual(conflict.status_code, 409)

    async def test_checkpoint_hashes_are_confined_to_the_owning_workspace(self):
        async with self.client() as client:
            first = await self._agent(client, "Owner")
            second = await self._agent(client, "Other")
            for agent in (first, second):
                await client.patch(
                    f"/xnobrain/api/runtime/v1/agents-configs/{agent}",
                    json={"checkpoints_enabled": True},
                )
                await client.post(
                    f"/xnobrain/api/runtime/v1/agents-workspaces/{agent}/write",
                    json={"path": "file.txt", "content": agent},
                )
            await client.post(
                f"/xnobrain/api/runtime/v1/agents-workspaces/{first}/write",
                json={"path": "file.txt", "content": "owner-new"},
            )
            listed = await client.get(f"/xnobrain/api/runtime/v1/agents/{first}/checkpoints")
            foreign_hash = listed.json()["data"]["items"][0]["id"]
            response = await client.get(
                f"/xnobrain/api/runtime/v1/agents/{second}/checkpoints/{foreign_hash}/diff"
            )
            malformed = await client.get(
                f"/xnobrain/api/runtime/v1/agents/{first}/checkpoints/not-a-hash/diff"
            )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(malformed.status_code, 400)
