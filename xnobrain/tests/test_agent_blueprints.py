"""Agent Maker blueprint lifecycle and profile-local persistence tests."""

from __future__ import annotations

import json
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
from xnobrain.repositories import FileRepository, StoreError
from xnobrain.trusted_context import (
    TRUSTED_SIGNATURE_HEADER,
    TRUSTED_SUBJECT_HEADER,
    principal_signature,
)


class FakeRouter:
    async def list_connections(self):
        return {"connections": []}

    async def list_models(self):
        return {"data": []}

    async def status(self):
        return {"available": True, "provider_count": 0}


class AgentBlueprintTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        temporary = Path(self.temporary.name)
        self.root = temporary / "root"
        self.profiles = temporary / "profiles"
        self.root.mkdir()
        self.profiles.mkdir()
        (self.root / "config.yaml").write_text(
            yaml.safe_dump({"model": {"default": "auto"}}),
            encoding="utf-8",
        )
        self.environment = patch.dict(
            os.environ,
            {
                "HERMES_HOME": str(self.root),
                "HERMES_ROOT_PROFILE": str(self.root),
                "HERMES_PROFILES_ROOT": str(self.profiles),
                "DATA_DIR": str(temporary / "data"),
                "RUNTIME_INCLUDE_PACKAGED_SKILLS": "false",
                "RUNTIME_INTERNAL_SERVICE_TOKEN": "test-internal-token",
            },
        )
        self.environment.start()
        app = FastAPI()
        composition = XNOBrainApplication(
            AgentManager(
                root_profile=self.root,
                profiles_root=self.profiles,
                legacy_agents_root=temporary / "legacy",
            ),
            GlobalConfigManager(root_profile=self.root),
            FakeRouter(),
        )
        composition.register(app)
        self.app = app
        self.service = composition.service

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def client(self):
        return AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test")

    @staticmethod
    def spec(name: str = "Research analyst") -> dict:
        return {
            "schema_version": 1,
            "name": name,
            "purpose": "Produce cited research briefs",
            "ownership": "personal",
            "persona": {
                "soul": "Be a focused research specialist.",
                "agents_instructions": "Verify sources and separate inference.",
            },
            "model_slot": {"alias": "research-approved", "reasoning_effort": "high"},
            "memory": {
                "policy": "context_isolated",
                "seed_sources": [
                    {"id": "style", "content": "Use concise prose.", "provenance": "user"}
                ],
            },
            "skills": [
                {
                    "id": "source-review",
                    "digest": "sha256:"
                    + __import__("hashlib")
                    .sha256(b"---\nname: source-review\ndescription: Verify sources\n---\n")
                    .hexdigest(),
                    "source": "approved-catalog:source-review@1",
                    "content": ("---\nname: source-review\ndescription: Verify sources\n---\n"),
                }
            ],
            "workspace": {"directories": ["inputs", "work", "outputs"]},
            "tools": {"requested": ["workspace-files"], "mcp_servers": []},
            "automation": {"cron_enabled": False},
            "budget": {"currency": "USD", "expected_cost": 2.5},
            "acceptance": ["Produce a synthetic brief with citations"],
        }

    async def create(self, *, blueprint=None):
        payload = {
            "intent": "Build a research analyst",
            "work_context_id": "personal:owner",
        }
        if blueprint is not None:
            payload["blueprint"] = blueprint
        async with self.client() as client:
            return await client.post(
                "/xnobrain/api/runtime/v1/agent-blueprints?agent=big-brother",
                json=payload,
            )

    async def test_draft_is_profile_local_atomic_and_does_not_create_child(self):
        response = await self.create()
        self.assertEqual(response.status_code, 201, response.text)
        record = response.json()["data"]
        self.assertEqual(record["revision"], 1)
        self.assertEqual(record["status"], "requested")
        self.assertEqual(record["file_manifest"], [])
        self.assertFalse((self.profiles / record["target_profile_id"]).exists())
        path = self.root / ".xnobrain" / "agent-blueprints" / f"{record['id']}.json"
        self.assertTrue(path.is_file())
        self.assertEqual(json.loads(path.read_text())["intent"], record["intent"])
        self.assertFalse(any(path.parent.glob(f".{path.name}.*")))

    async def test_create_get_patch_revision_and_approval_invalidation(self):
        created = (await self.create(blueprint=self.spec())).json()["data"]
        self.assertEqual(created["status"], "blueprint_ready")
        self.assertEqual(
            {item["path"] for item in created["file_manifest"]},
            {
                "SOUL.md",
                "workspace/AGENTS.md",
                "config.yaml",
                "memories/seeds/style.md",
                "skills/custom/source-review/SKILL.md",
                "workspace/inputs/.gitkeep",
                "workspace/work/.gitkeep",
                "workspace/outputs/.gitkeep",
                ".xnobrain/certification.yaml",
            },
        )
        async with self.client() as client:
            fetched = await client.get(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{created['id']}?agent=big-brother"
            )
            approved = await client.post(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{created['id']}"
                "/approvals?agent=big-brother",
                json={
                    "expected_revision": 1,
                    "canonical_digest": created["canonical_digest"],
                    "decision": "approve",
                },
                headers={
                    TRUSTED_SUBJECT_HEADER: "user:kim",
                    TRUSTED_SIGNATURE_HEADER: principal_signature(
                        "test-internal-token", "user:kim"
                    ),
                },
            )
            patched = await client.patch(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{created['id']}?agent=big-brother",
                json={"expected_revision": 1, "intent": "Build a stricter analyst"},
            )
        self.assertEqual(fetched.json()["data"]["canonical_digest"], created["canonical_digest"])
        self.assertEqual(approved.status_code, 200, approved.text)
        approval = approved.json()["data"]
        self.assertEqual(approval["status"], "approved")
        self.assertEqual(approval["revision"], 1)
        self.assertEqual(approval["approval"]["approved_revision"], 1)
        self.assertEqual(approval["approval"]["canonical_digest"], created["canonical_digest"])
        self.assertEqual(approval["canonical_digest"], created["canonical_digest"])
        self.assertEqual(approval["approval"]["binding"], created["approval_binding"])
        self.assertEqual(approval["approval"]["approved_by"], "user:kim")
        self.assertEqual(patched.status_code, 200, patched.text)
        updated = patched.json()["data"]
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(updated["status"], "blueprint_ready")
        self.assertIsNone(updated["approval"])
        self.assertNotEqual(updated["canonical_digest"], created["canonical_digest"])

    async def test_stale_patch_and_digest_mismatch_are_conflicts(self):
        created = (await self.create(blueprint=self.spec())).json()["data"]
        async with self.client() as client:
            mismatch = await client.post(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{created['id']}"
                "/approvals?agent=big-brother",
                json={
                    "expected_revision": 1,
                    "canonical_digest": "sha256:" + "0" * 64,
                    "decision": "approve",
                },
                headers={
                    TRUSTED_SUBJECT_HEADER: "user:kim",
                    TRUSTED_SIGNATURE_HEADER: principal_signature(
                        "test-internal-token", "user:kim"
                    ),
                },
            )
            first = await client.patch(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{created['id']}?agent=big-brother",
                json={"expected_revision": 1, "intent": "first update"},
            )
            stale = await client.patch(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{created['id']}?agent=big-brother",
                json={"expected_revision": 1, "intent": "stale update"},
            )
        self.assertEqual(mismatch.status_code, 409, mismatch.text)
        self.assertEqual(mismatch.json()["error"]["code"], "blueprint_digest_mismatch")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["error"]["code"], "blueprint_revision_conflict")

    async def test_approval_fails_closed_without_verified_facade_subject(self):
        created = (await self.create(blueprint=self.spec())).json()["data"]
        payload = {
            "expected_revision": 1,
            "canonical_digest": created["canonical_digest"],
            "decision": "approve",
        }
        async with self.client() as client:
            missing = await client.post(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{created['id']}"
                "/approvals?agent=big-brother",
                json=payload,
            )
            forged = await client.post(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{created['id']}"
                "/approvals?agent=big-brother",
                json={**payload, "approved_by": "user:attacker"},
                headers={TRUSTED_SUBJECT_HEADER: "user:attacker"},
            )
        self.assertEqual(missing.status_code, 401, missing.text)
        self.assertEqual(missing.json()["error"]["code"], "trusted_subject_required")
        self.assertEqual(forged.status_code, 422, forged.text)

    async def test_owner_isolation_validation_and_no_scaffold_route(self):
        other = self.service.create_agent({"display_name": "Other"})
        created = (await self.create()).json()["data"]
        async with self.client() as client:
            hidden = await client.get(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{created['id']}?agent={other['id']}"
            )
            traversal = await client.get(
                "/xnobrain/api/runtime/v1/agent-blueprints/..%2Foutside?agent=big-brother"
            )
            invalid = await client.post(
                "/xnobrain/api/runtime/v1/agent-blueprints?agent=big-brother",
                json={
                    "intent": "x",
                    "work_context_id": "personal:owner",
                    "blueprint": {**self.spec(), "workspace": {"directories": ["../escape"]}},
                },
            )
            scaffold = await client.post(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{created['id']}"
                "/scaffold?agent=big-brother",
                json={},
            )
        self.assertEqual(hidden.status_code, 404, hidden.text)
        self.assertIn(traversal.status_code, {400, 404})
        self.assertEqual(invalid.status_code, 422, invalid.text)
        self.assertEqual(scaffold.status_code, 404)

    def test_repository_rejects_symlinked_blueprint_store(self):
        repository = FileRepository(Path(self.temporary.name) / "data2", self.profiles)
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        metadata = self.root / ".xnobrain"
        metadata.mkdir(exist_ok=True)
        (metadata / "agent-blueprints").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(StoreError) as caught:
            repository.get_agent_blueprint(self.root, "abp_safe")
        self.assertEqual(caught.exception.code, "unsafe_blueprint_store")
