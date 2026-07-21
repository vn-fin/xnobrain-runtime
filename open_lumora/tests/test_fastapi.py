"""Contract tests for the unified FastAPI composition and file persistence."""

from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from zipfile import ZipFile

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import yaml

from open_lumora.app import OpenLumoraApplication
from open_lumora.integrations import AgentManager, GlobalConfigManager


class FakeRouter:
    async def list_connections(self): return {"connections": []}
    async def list_models(self): return {"data": []}


class StudioFastAPITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "root"
        self.profiles = Path(self.temporary.name) / "profiles"
        self.root.mkdir(parents=True)
        self.profiles.mkdir(parents=True)
        (self.root / "config.yaml").write_text(yaml.safe_dump({
            "model": {"provider": "custom:nine-router", "default": "auto"},
            "providers": {}, "agent": {"reasoning_effort": "medium"},
            "approvals": {"mode": "manual"}, "terminal": {"backend": "local"},
        }), encoding="utf-8")
        self.environment = patch.dict(os.environ, {
            "HERMES_HOME": str(self.root), "HERMES_ROOT_PROFILE": str(self.root),
            "HERMES_PROFILES_ROOT": str(self.profiles), "DATA_DIR": self.temporary.name,
        })
        self.environment.start()
        app = FastAPI()
        composition = OpenLumoraApplication(
            AgentManager(root_profile=self.root, profiles_root=self.profiles, legacy_agents_root=Path(self.temporary.name) / "legacy-agents"),
            GlobalConfigManager(root_profile=self.root), FakeRouter(),
        )
        composition.register(app)
        self.composition = composition
        self.app = app

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def client(self):
        return AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test")

    async def test_health_identifies_fastapi_database_free_runtime(self):
        async with self.client() as client:
            response = await client.get("/api/v1/health")
            deployment_response = await client.get("/api/v1/system/deployment")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["api"], "fastapi")
        deployment = deployment_response.json()["data"]
        self.assertFalse(deployment["database"])

    async def test_agent_skill_memory_mcp_and_snapshots_are_profile_local(self):
        async with self.client() as client:
            created = await client.post("/agent-gateway/v1/agents", json={"name": "Researcher", "description": "test"})
        self.assertEqual(created.status_code, 201, created.text)
        agent_id = created.json()["data"]["id"]
        profile = self.profiles / agent_id

        async with self.client() as client:
            skill = await client.post(f"/agent-gateway/v1/agents-skills/{agent_id}", json={
                "skill_id": "notes", "content": "---\nname: notes\n---\n# Notes\n",
            })
            memory = await client.patch(f"/agent-gateway/v1/agents/{agent_id}/memory", json={"memory": "remember this"})
            mcp = await client.put(f"/agent-gateway/v1/agents-mcp/{agent_id}", json={"servers": {"docs": {"command": "docs-mcp"}}})
            snapshot_response = await client.get(f"/agent-gateway/v1/agents/{agent_id}/snapshots")
        self.assertEqual(skill.status_code, 201, skill.text)
        self.assertEqual(memory.status_code, 200, memory.text)
        self.assertEqual(mcp.status_code, 200, mcp.text)

        self.assertTrue((profile / "skills" / "notes" / "SKILL.md").is_file())
        self.assertTrue((profile / "mcp.json").is_file())
        snapshots = snapshot_response.json()["data"]
        self.assertEqual({item["kind"] for item in snapshots}, {"skills", "memory"})
        self.assertFalse((self.root / "skills" / "notes" / "SKILL.md").exists())

    async def test_team_and_cron_persist_without_database(self):
        ids = []
        async with self.client() as client:
            for title in ("Lead", "Worker"):
                result = await client.post("/agent-gateway/v1/agents", json={"name": title})
                ids.append(result.json()["data"]["id"])
            team = await client.post("/api/v1/teams", json={
                "name": "Research", "orchestrator_id": ids[0],
                "members": [{"agent_id": ids[1], "role": "researcher", "allowed_tools": ["web"]}],
            })
            cron = await client.post("/agent-gateway/v1/cron/jobs", json={
                "agent_id": ids[0], "name": "Digest", "prompt": "Summarize", "interval_minutes": 60,
            })
            teams = await client.get("/api/v1/teams")
            crons = await client.get("/agent-gateway/v1/cron/jobs")
        self.assertEqual(team.status_code, 201, team.text)
        self.assertEqual(cron.status_code, 201, cron.text)
        self.assertEqual(len(teams.json()["data"]), 1)
        self.assertEqual(len(crons.json()["data"]), 1)

    async def test_due_profile_cron_executes_in_the_unified_process(self):
        async with self.client() as client:
            created = await client.post("/agent-gateway/v1/agents", json={"name": "Scheduler"})
            agent_id = created.json()["data"]["id"]
            response = await client.post("/agent-gateway/v1/cron/jobs", json={
                "agent_id": agent_id, "name": "Due", "prompt": "Run", "interval_minutes": 60,
            })
        job = response.json()["data"]
        job["next_run_at"] = "2000-01-01T00:00:00Z"
        self.composition.repository.put_cron(job)
        self.composition.service.agents.chat = AsyncMock(return_value={"response": "ok"})

        await self.composition.service._tick_crons()

        self.composition.service.agents.chat.assert_awaited_once()
        updated = self.composition.service._cron(job["id"])
        self.assertIsNotNone(updated.get("last_run_at"))

    async def test_swagger_documents_typed_management_and_stream_requests(self):
        schema = self.app.openapi()
        self.assertEqual(
            schema["paths"]["/agent-gateway/v1/agents"]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"],
            "#/components/schemas/AgentCreate",
        )
        self.assertEqual(
            schema["paths"]["/conversations/v1/conversations/{conversation_id}/chat/stream"]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"],
            "#/components/schemas/ChatRequest",
        )

    async def test_bundle_round_trip_is_checked_and_excludes_credentials(self):
        async with self.client() as client:
            created = await client.post("/agent-gateway/v1/agents", json={"name": "Portable"})
            agent_id = created.json()["data"]["id"]
            (self.profiles / agent_id / ".env").write_text("SECRET=never-export\n", encoding="utf-8")
            exported = await client.post("/api/v1/bundles/export", json={"agent_ids": [agent_id]})
        self.assertEqual(exported.status_code, 200, exported.text)
        with ZipFile(BytesIO(exported.content)) as archive:
            self.assertNotIn(f"profiles/{agent_id}/.env", archive.namelist())

        upload = {"file": ("profile.lumora", exported.content, "application/vnd.open-lumora.bundle")}
        async with self.client() as client:
            inspected = await client.post("/api/v1/bundles/inspect", files=upload)
            preview = await client.post("/api/v1/bundles/dry-run", files=upload)
            applied = await client.post("/api/v1/bundles/apply", files=upload)
        self.assertEqual(inspected.status_code, 200, inspected.text)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()["data"]["collisions"], [agent_id])
        self.assertEqual(applied.status_code, 201, applied.text)
        imported_id = applied.json()["data"]["agent_id_mappings"][agent_id]
        self.assertNotEqual(imported_id, agent_id)
        self.assertTrue((self.profiles / imported_id / "config.yaml").is_file())

    async def test_missing_run_control_and_local_device_have_stable_responses(self):
        async with self.client() as client:
            stopped = await client.post("/conversations/v1/conversations/c/runs/run_00000000000000000000000000000000/stop?agent=a")
            device = await client.get("/api/v1/device")
        self.assertEqual(stopped.status_code, 404)
        self.assertEqual(stopped.json()["error"]["code"], "run_not_found")
        self.assertEqual(device.status_code, 200)
        self.assertFalse(device.json()["data"]["enabled"])


if __name__ == "__main__":
    unittest.main()
