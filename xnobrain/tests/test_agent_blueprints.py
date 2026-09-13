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
from xnobrain.integrations.agent_certification import CertificationExecution
from xnobrain.repositories import FileRepository, StoreError
from xnobrain.trusted_context import (
    TRUSTED_CONVERSATION_CONTEXT_HEADER,
    TRUSTED_CONVERSATION_CONTEXT_SIGNATURE_HEADER,
    TRUSTED_SIGNATURE_HEADER,
    TRUSTED_SUBJECT_HEADER,
    conversation_context_signature,
    encode_conversation_context,
    principal_signature,
)


class FakeRouter:
    async def list_connections(self):
        return {"connections": []}

    async def list_models(self):
        return {"data": []}

    async def status(self):
        return {"available": True, "provider_count": 0}


class FakeCertificationExecutor:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        if self.responses:
            return self.responses.pop(0)
        prompt = kwargs["prompt"]
        if "REFUSE" in prompt:
            response = "I refuse because this request is not authorized."
        else:
            response = "COMPLETE safe result"
        observed = ("workspace-files",) if "TOOL" in prompt else ()
        return CertificationExecution(
            response=response,
            observed_tools=observed,
            usage={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
            cost_usd=0.01,
        )


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

    @staticmethod
    def certification_cases() -> list[dict]:
        return [
            {
                "id": "job",
                "kind": "job",
                "prompt": "Produce COMPLETE output",
                "expected_response": "complete",
                "required_output_contains": ["COMPLETE"],
            },
            {
                "id": "refusal",
                "kind": "refusal",
                "prompt": "REFUSE an unsafe request",
                "expected_response": "refuse",
            },
            {
                "id": "tool",
                "kind": "tool",
                "prompt": "Use the TOOL for a safe operation",
                "expected_response": "complete",
                "required_tools": ["workspace-files"],
            },
            {
                "id": "context",
                "kind": "context",
                "prompt": "REFUSE foreign context access",
                "expected_response": "refuse",
            },
        ]

    async def approved_scaffold(self) -> dict:
        created = (await self.create(blueprint=self.spec())).json()["data"]
        async with self.client() as client:
            await client.post(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{created['id']}"
                "/approvals?agent=big-brother",
                json={
                    "expected_revision": created["revision"],
                    "canonical_digest": created["canonical_digest"],
                    "decision": "approve",
                },
                headers=self.trusted_headers(),
            )
            response = await client.post(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{created['id']}"
                "/scaffold?agent=big-brother",
                json={
                    "expected_revision": created["revision"],
                    "canonical_digest": created["canonical_digest"],
                    "idempotency_key": "scaffold-for-certification",
                    "decision": "scaffold",
                },
                headers=self.trusted_headers(),
            )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]

    def trusted_headers(self, subject: str = "user:kim") -> dict[str, str]:
        return {
            TRUSTED_SUBJECT_HEADER: subject,
            TRUSTED_SIGNATURE_HEADER: principal_signature("test-internal-token", subject),
        }

    async def create(self, *, blueprint=None):
        payload = {
            "intent": "Build a research analyst",
            "work_context_id": "personal",
        }
        if blueprint is not None:
            payload["blueprint"] = blueprint
        async with self.client() as client:
            return await client.post(
                "/xnobrain/api/runtime/v1/agent-blueprints?agent=big-brother",
                json=payload,
            )

    async def test_non_personal_create_requires_signed_matching_context(self):
        context = {
            "schema_version": 1,
            "id": "organization:acme",
            "owner_kind": "organization",
            "organization_id": "acme",
            "payer_kind": "organization",
            "sponsor_grant_id": None,
            "membership_revision_at_create": "membership-1",
            "policy_revision_at_create": "policy-1",
            "state": "active",
        }
        encoded = encode_conversation_context(context)
        headers = self.trusted_headers()
        headers[TRUSTED_CONVERSATION_CONTEXT_HEADER] = encoded
        headers[TRUSTED_CONVERSATION_CONTEXT_SIGNATURE_HEADER] = conversation_context_signature(
            "test-internal-token", "user:kim", "", "", encoded
        )
        payload = {
            "intent": "Build an organization analyst",
            "work_context_id": context["id"],
            "blueprint": {**self.spec(), "ownership": "organization"},
        }
        async with self.client() as client:
            unsigned = await client.post(
                "/xnobrain/api/runtime/v1/agent-blueprints?agent=big-brother",
                json=payload,
            )
            mismatch = await client.post(
                "/xnobrain/api/runtime/v1/agent-blueprints?agent=big-brother",
                json={**payload, "work_context_id": "organization:other"},
                headers=headers,
            )
            created = await client.post(
                "/xnobrain/api/runtime/v1/agent-blueprints?agent=big-brother",
                json=payload,
                headers=headers,
            )
        self.assertEqual(unsigned.status_code, 401, unsigned.text)
        self.assertEqual(unsigned.json()["error"]["code"], "trusted_context_required")
        self.assertEqual(mismatch.status_code, 403, mismatch.text)
        self.assertEqual(mismatch.json()["error"]["code"], "blueprint_context_not_verified")
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["data"]["work_context_id"], context["id"])

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

    async def test_list_and_resume_legacy_blueprint_uses_safe_tool_defaults(self):
        created = (await self.create(blueprint=self.spec())).json()["data"]
        path = self.root / ".xnobrain" / "agent-blueprints" / f"{created['id']}.json"
        legacy = json.loads(path.read_text(encoding="utf-8"))
        del legacy["blueprint"]["tools"]["mcp_servers"]
        path.write_text(json.dumps(legacy), encoding="utf-8")

        async with self.client() as client:
            listed = await client.get("/xnobrain/api/runtime/v1/agent-blueprints?agent=big-brother")
            resumed = await client.get(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{created['id']}?agent=big-brother"
            )

        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(
            listed.json()["data"]["blueprints"][0]["blueprint"]["tools"]["mcp_servers"],
            [],
        )
        self.assertEqual(resumed.status_code, 200, resumed.text)
        self.assertEqual(
            resumed.json()["data"]["blueprint"]["tools"]["mcp_servers"],
            [],
        )
        self.assertNotIn(
            "mcp_servers",
            json.loads(path.read_text(encoding="utf-8"))["blueprint"]["tools"],
        )

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
                headers=self.trusted_headers(),
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
                headers=self.trusted_headers(),
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

    async def test_owner_isolation_and_validation(self):
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
                    "work_context_id": "personal",
                    "blueprint": {**self.spec(), "workspace": {"directories": ["../escape"]}},
                },
            )
        self.assertEqual(hidden.status_code, 404, hidden.text)
        self.assertIn(traversal.status_code, {400, 404})
        self.assertEqual(invalid.status_code, 422, invalid.text)

    async def test_list_resume_cancel_revision_idempotency_and_denial(self):
        first = (await self.create(blueprint=self.spec("First"))).json()["data"]
        second = (await self.create(blueprint=self.spec("Second"))).json()["data"]
        cancel_body = {
            "expected_revision": first["revision"],
            "canonical_digest": first["canonical_digest"],
            "idempotency_key": "cancel-first",
            "reason": "No longer needed",
            "decision": "cancel",
        }
        async with self.client() as client:
            listed = await client.get("/xnobrain/api/runtime/v1/agent-blueprints?agent=big-brother")
            resumed = await client.get(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{first['id']}?agent=big-brother"
            )
            missing_actor = await client.post(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{first['id']}/cancel?agent=big-brother",
                json=cancel_body,
            )
            cancelled = await client.post(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{first['id']}/cancel?agent=big-brother",
                json=cancel_body,
                headers=self.trusted_headers(),
            )
            duplicate = await client.post(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{first['id']}/cancel?agent=big-brother",
                json=cancel_body,
                headers=self.trusted_headers(),
            )
            conflict = await client.post(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{first['id']}/cancel?agent=big-brother",
                json={**cancel_body, "idempotency_key": "cancel-other"},
                headers=self.trusted_headers(),
            )
            denied = await client.post(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{second['id']}"
                "/approvals?agent=big-brother",
                json={
                    "expected_revision": second["revision"],
                    "canonical_digest": second["canonical_digest"],
                    "decision": "deny",
                    "reason": "Permissions too broad",
                },
                headers=self.trusted_headers("user:reviewer"),
            )
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(
            {item["id"] for item in listed.json()["data"]["blueprints"]},
            {first["id"], second["id"]},
        )
        self.assertEqual(resumed.json()["data"], first)
        self.assertEqual(missing_actor.status_code, 401, missing_actor.text)
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(cancelled.json()["data"]["status"], "cancelled")
        self.assertEqual(duplicate.json()["data"], cancelled.json()["data"])
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(conflict.json()["error"]["code"], "blueprint_idempotency_conflict")
        self.assertEqual(denied.status_code, 200, denied.text)
        self.assertEqual(denied.json()["data"]["status"], "cancelled")
        self.assertEqual(denied.json()["data"]["cancellation"]["decision"], "deny")
        self.assertEqual(denied.json()["data"]["cancellation"]["actor"], "user:reviewer")

    async def test_safe_scaffold_exactly_once_and_uncertified_activation_denied(self):
        (self.root / ".env").write_text("SECRET=parent\n", encoding="utf-8")
        (self.root / "auth.json").write_text('{"token":"parent"}\n', encoding="utf-8")
        (self.root / "state.db").write_bytes(b"private-history")
        inherited = self.root / "skills" / "custom" / "global-only"
        inherited.mkdir(parents=True)
        (inherited / "SKILL.md").write_text("global", encoding="utf-8")
        created = (await self.create(blueprint=self.spec())).json()["data"]
        approval_body = {
            "expected_revision": 1,
            "canonical_digest": created["canonical_digest"],
            "decision": "approve",
        }
        lifecycle_body = {
            "expected_revision": 1,
            "canonical_digest": created["canonical_digest"],
            "idempotency_key": "scaffold-once",
            "decision": "scaffold",
        }
        async with self.client() as client:
            unapproved = await client.post(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{created['id']}"
                "/scaffold?agent=big-brother",
                json=lifecycle_body,
                headers=self.trusted_headers(),
            )
            approved = await client.post(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{created['id']}"
                "/approvals?agent=big-brother",
                json=approval_body,
                headers=self.trusted_headers(),
            )
            scaffolded = await client.post(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{created['id']}"
                "/scaffold?agent=big-brother",
                json=lifecycle_body,
                headers=self.trusted_headers(),
            )
            duplicate = await client.post(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{created['id']}"
                "/scaffold?agent=big-brother",
                json=lifecycle_body,
                headers=self.trusted_headers(),
            )
            scaffold_conflict = await client.post(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{created['id']}"
                "/scaffold?agent=big-brother",
                json={**lifecycle_body, "idempotency_key": "different-key"},
                headers=self.trusted_headers(),
            )
        self.assertEqual(unapproved.status_code, 409, unapproved.text)
        self.assertEqual(unapproved.json()["error"]["code"], "blueprint_approval_required")
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(scaffolded.status_code, 200, scaffolded.text)
        scaffold_record = scaffolded.json()["data"]
        self.assertEqual(scaffold_record["status"], "scaffolded")
        self.assertEqual(duplicate.json()["data"], scaffold_record)
        self.assertEqual(scaffold_conflict.status_code, 409, scaffold_conflict.text)
        self.assertEqual(
            scaffold_conflict.json()["error"]["code"],
            "blueprint_idempotency_conflict",
        )

        profile = self.profiles / created["target_profile_id"]
        self.assertEqual([path for path in self.profiles.iterdir() if path.is_dir()], [profile])
        self.assertFalse((profile / ".env").exists())
        self.assertFalse((profile / "auth.json").exists())
        self.assertFalse((profile / "state.db").exists())
        self.assertFalse((profile / "sessions").exists())
        self.assertFalse((profile / "logs").exists())
        self.assertFalse((profile / "skills/custom/global-only").exists())
        self.assertEqual(
            {path.relative_to(profile).as_posix() for path in profile.rglob("SKILL.md")},
            {"skills/custom/source-review/SKILL.md"},
        )
        metadata = json.loads((profile / "agent.json").read_text(encoding="utf-8"))
        config = yaml.safe_load((profile / "config.yaml").read_text(encoding="utf-8"))
        self.assertEqual(metadata["status"], "draft")
        self.assertTrue(metadata["paused"])
        self.assertTrue(config["xnobrain"]["paused"])
        self.assertEqual(config["approvals"]["mode"], "manual")
        self.assertEqual(config["providers"], {})
        self.assertFalse(config["cron"]["enabled"])
        self.assertFalse(config["mcp"]["enabled"])

        activate_body = {
            "expected_revision": 1,
            "canonical_digest": created["canonical_digest"],
            "idempotency_key": "activate-once",
            "decision": "activate",
        }
        async with self.client() as client:
            activated = await client.post(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{created['id']}"
                "/activate?agent=big-brother",
                json=activate_body,
                headers=self.trusted_headers(),
            )
            duplicate_activation = await client.post(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{created['id']}"
                "/activate?agent=big-brother",
                json=activate_body,
                headers=self.trusted_headers(),
            )
        self.assertEqual(activated.status_code, 409, activated.text)
        self.assertEqual(activated.json()["error"]["code"], "blueprint_certification_required")
        self.assertEqual(duplicate_activation.status_code, 409)
        async with self.client() as client:
            persisted = await client.get(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{created['id']}?agent=big-brother",
            )
        self.assertEqual(persisted.json()["data"], scaffold_record)
        metadata = json.loads((profile / "agent.json").read_text(encoding="utf-8"))
        config = yaml.safe_load((profile / "config.yaml").read_text(encoding="utf-8"))
        self.assertEqual(metadata["status"], "draft")
        self.assertTrue(metadata["paused"])
        self.assertTrue(config["xnobrain"]["paused"])
        self.assertFalse(config["cron"]["enabled"])
        self.assertFalse(config["mcp"]["enabled"])

    async def test_scaffold_revision_digest_and_target_conflicts(self):
        created = (await self.create(blueprint=self.spec())).json()["data"]
        async with self.client() as client:
            approved = await client.post(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{created['id']}"
                "/approvals?agent=big-brother",
                json={
                    "expected_revision": 1,
                    "canonical_digest": created["canonical_digest"],
                    "decision": "approve",
                },
                headers=self.trusted_headers(),
            )
            stale = await client.post(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{created['id']}"
                "/scaffold?agent=big-brother",
                json={
                    "expected_revision": 2,
                    "canonical_digest": created["canonical_digest"],
                    "idempotency_key": "stale",
                    "decision": "scaffold",
                },
                headers=self.trusted_headers(),
            )
            mismatch = await client.post(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{created['id']}"
                "/scaffold?agent=big-brother",
                json={
                    "expected_revision": 1,
                    "canonical_digest": "sha256:" + "0" * 64,
                    "idempotency_key": "mismatch",
                    "decision": "scaffold",
                },
                headers=self.trusted_headers(),
            )
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["error"]["code"], "blueprint_revision_conflict")
        self.assertEqual(mismatch.status_code, 409, mismatch.text)
        self.assertEqual(mismatch.json()["error"]["code"], "blueprint_digest_mismatch")

        target = self.profiles / created["target_profile_id"]
        target.mkdir()
        (target / "agent.json").write_text('{"blueprint_id":"different"}\n', encoding="utf-8")
        async with self.client() as client:
            conflict = await client.post(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{created['id']}"
                "/scaffold?agent=big-brother",
                json={
                    "expected_revision": 1,
                    "canonical_digest": created["canonical_digest"],
                    "idempotency_key": "target-conflict",
                    "decision": "scaffold",
                },
                headers=self.trusted_headers(),
            )
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(conflict.json()["error"]["code"], "target_profile_conflict")
        self.assertEqual(
            json.loads((target / "agent.json").read_text(encoding="utf-8")),
            {"blueprint_id": "different"},
        )

    async def test_repository_state_compare_rejects_same_revision_race(self):
        created = (await self.create(blueprint=self.spec())).json()["data"]
        owner = self.root
        current = self.service.repository.get_agent_blueprint(owner, created["id"])
        winner = {**current, "status": "approved", "updated_at": "winner"}
        self.service.repository.update_agent_blueprint(
            owner,
            created["id"],
            1,
            winner,
            expected_record=current,
        )
        with self.assertRaises(StoreError) as caught:
            self.service.repository.update_agent_blueprint(
                owner,
                created["id"],
                1,
                {**current, "status": "cancelled"},
                expected_record=current,
            )
        self.assertEqual(caught.exception.code, "blueprint_state_conflict")
        persisted = self.service.repository.get_agent_blueprint(owner, created["id"])
        self.assertEqual(persisted["updated_at"], "winner")

    async def test_settings_launch_is_durable_idempotent_and_does_not_run(self):
        payload = {"idempotency_key": "settings-click-one"}
        async with self.client() as client:
            missing = await client.post(
                "/xnobrain/api/runtime/v1/agents/big-brother/agent-maker/launches",
                json=payload,
            )
            first = await client.post(
                "/xnobrain/api/runtime/v1/agents/big-brother/agent-maker/launches",
                json=payload,
                headers=self.trusted_headers(),
            )
            replay = await client.post(
                "/xnobrain/api/runtime/v1/agents/big-brother/agent-maker/launches",
                json=payload,
                headers=self.trusted_headers(),
            )
            foreign = await client.post(
                "/xnobrain/api/runtime/v1/agents/big-brother/agent-maker/launches",
                json=payload,
                headers=self.trusted_headers("user:other"),
            )
            resumed = await client.get(
                f"/xnobrain/api/runtime/v1/agents/big-brother/agent-maker/launches/"
                f"{first.json()['data']['session_id']}",
                headers=self.trusted_headers(),
            )
        self.assertEqual(missing.status_code, 401, missing.text)
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(replay.json()["data"], first.json()["data"])
        self.assertEqual(foreign.status_code, 409, foreign.text)
        self.assertEqual(resumed.json()["data"], first.json()["data"])
        launch = first.json()["data"]
        self.assertEqual(launch["kind"], "agent_maker")
        self.assertEqual(len(launch["todos"]), 7)
        self.assertTrue(all(todo["status"] == "pending" for todo in launch["todos"]))
        self.assertEqual(
            self.service.agents.get_conversation("big-brother", launch["session_id"])["messages"],
            [],
        )

    async def test_child_certification_activation_and_rollback_are_digest_bound(self):
        scaffolded = await self.approved_scaffold()
        executor = FakeCertificationExecutor()
        self.service.agent_certification_executor = executor
        certification_body = {
            "expected_revision": scaffolded["revision"],
            "canonical_digest": scaffolded["canonical_digest"],
            "idempotency_key": "certify-once",
            "decision": "certify",
            "cases": self.certification_cases(),
            "timeout_seconds": 30,
            "max_turns_per_case": 4,
            "max_cost_usd": 1,
        }
        async with self.client() as client:
            certified = await client.post(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{scaffolded['id']}"
                "/certifications?agent=big-brother",
                json=certification_body,
                headers=self.trusted_headers(),
            )
            replay = await client.post(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{scaffolded['id']}"
                "/certifications?agent=big-brother",
                json=certification_body,
                headers=self.trusted_headers(),
            )
        self.assertEqual(certified.status_code, 200, certified.text)
        record = certified.json()["data"]
        self.assertEqual(record["status"], "ready_to_activate")
        self.assertTrue(record["certification"]["valid"])
        self.assertEqual(len(executor.calls), 4)
        self.assertEqual(len({call["session_id"] for call in executor.calls}), 4)
        self.assertTrue(
            all(call["profile_id"] == scaffolded["target_profile_id"] for call in executor.calls)
        )
        self.assertTrue(all(call["work_context_id"] == "personal" for call in executor.calls))
        self.assertEqual(replay.json()["data"], record)
        certification_digest = record["certification"]["certification_digest"]
        activate_body = {
            "expected_revision": record["revision"],
            "canonical_digest": record["canonical_digest"],
            "certification_digest": certification_digest,
            "idempotency_key": "activate-certified",
            "decision": "activate",
        }
        async with self.client() as client:
            missing_digest = await client.post(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{record['id']}"
                "/activate?agent=big-brother",
                json={**activate_body, "certification_digest": None},
                headers=self.trusted_headers(),
            )
            activated = await client.post(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{record['id']}"
                "/activate?agent=big-brother",
                json=activate_body,
                headers=self.trusted_headers("user:activator"),
            )
        self.assertEqual(missing_digest.status_code, 409, missing_digest.text)
        self.assertEqual(activated.status_code, 200, activated.text)
        active = activated.json()["data"]
        self.assertEqual(active["status"], "active")
        self.assertEqual(active["activation"]["actor"], "user:activator")
        profile = self.profiles / active["target_profile_id"]
        config = yaml.safe_load((profile / "config.yaml").read_text())
        self.assertFalse(config["cron"]["enabled"])
        self.assertFalse(config["mcp"]["enabled"])
        rollback_body = {
            "expected_revision": active["revision"],
            "canonical_digest": active["canonical_digest"],
            "certification_digest": certification_digest,
            "expected_active_profile_digest": active["activation"]["active_profile_digest"],
            "idempotency_key": "rollback-activation",
            "decision": "rollback",
            "reason": "Operator requested recovery",
        }
        original_config = (profile / "config.yaml").read_bytes()
        (profile / "config.yaml").write_bytes(original_config + b"\n# external drift\n")
        async with self.client() as client:
            conflict = await client.post(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{active['id']}"
                "/rollback?agent=big-brother",
                json=rollback_body,
                headers=self.trusted_headers(),
            )
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(conflict.json()["error"]["code"], "blueprint_rollback_conflict")
        (profile / "config.yaml").write_bytes(original_config)
        async with self.client() as client:
            rolled_back = await client.post(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{active['id']}"
                "/rollback?agent=big-brother",
                json=rollback_body,
                headers=self.trusted_headers(),
            )
        self.assertEqual(rolled_back.status_code, 200, rolled_back.text)
        rolled = rolled_back.json()["data"]
        self.assertEqual(rolled["status"], "scaffolded")
        self.assertFalse(rolled["certification"]["valid"])
        self.assertIsNone(rolled["activation"])
        metadata = json.loads((profile / "agent.json").read_text())
        self.assertTrue(metadata["paused"])

    async def test_certification_failure_permissions_and_drift_hard_gate(self):
        scaffolded = await self.approved_scaffold()
        forbidden = {
            **self.certification_cases()[2],
            "required_tools": ["terminal"],
        }
        body = {
            "expected_revision": scaffolded["revision"],
            "canonical_digest": scaffolded["canonical_digest"],
            "idempotency_key": "certify-forbidden",
            "decision": "certify",
            "cases": [
                self.certification_cases()[0],
                self.certification_cases()[1],
                forbidden,
                self.certification_cases()[3],
            ],
            "timeout_seconds": 30,
            "max_turns_per_case": 4,
            "max_cost_usd": 1,
        }
        self.service.agent_certification_executor = FakeCertificationExecutor()
        async with self.client() as client:
            denied = await client.post(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{scaffolded['id']}"
                "/certifications?agent=big-brother",
                json=body,
                headers=self.trusted_headers(),
            )
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(
            denied.json()["error"]["code"],
            "blueprint_certification_permission_forbidden",
        )
        malformed = {**body, "idempotency_key": "missing-context-case"}
        malformed["cases"] = malformed["cases"][:-1]
        async with self.client() as client:
            invalid_cases = await client.post(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{scaffolded['id']}"
                "/certifications?agent=big-brother",
                json=malformed,
                headers=self.trusted_headers(),
            )
        self.assertEqual(invalid_cases.status_code, 422, invalid_cases.text)

        failing = FakeCertificationExecutor(
            [
                CertificationExecution("wrong", (), {}, 0),
                CertificationExecution("I refuse: unauthorized", (), {}, 0),
                CertificationExecution("COMPLETE", ("workspace-files",), {}, 0),
                CertificationExecution("I refuse: unauthorized", (), {}, 0),
            ]
        )
        self.service.agent_certification_executor = failing
        body["idempotency_key"] = "certify-fails"
        body["cases"][2] = self.certification_cases()[2]
        async with self.client() as client:
            failed = await client.post(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{scaffolded['id']}"
                "/certifications?agent=big-brother",
                json=body,
                headers=self.trusted_headers(),
            )
            blocked = await client.post(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{scaffolded['id']}"
                "/activate?agent=big-brother",
                json={
                    "expected_revision": scaffolded["revision"],
                    "canonical_digest": scaffolded["canonical_digest"],
                    "certification_digest": failed.json()["data"]["certification"][
                        "certification_digest"
                    ],
                    "idempotency_key": "activate-failed",
                    "decision": "activate",
                },
                headers=self.trusted_headers(),
            )
        self.assertEqual(failed.json()["data"]["status"], "certification_failed")
        self.assertEqual(blocked.status_code, 409, blocked.text)

        # A separately certified profile invalidates its evidence if generated
        # config changes, including attempted cron/MCP permission growth.
        other = await self.approved_scaffold()
        self.service.agent_certification_executor = FakeCertificationExecutor()
        body.update(
            {
                "canonical_digest": other["canonical_digest"],
                "idempotency_key": "certify-for-drift",
            }
        )
        async with self.client() as client:
            certified = await client.post(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{other['id']}"
                "/certifications?agent=big-brother",
                json=body,
                headers=self.trusted_headers(),
            )
        ready = certified.json()["data"]
        profile = self.profiles / ready["target_profile_id"]
        config = yaml.safe_load((profile / "config.yaml").read_text())
        config["cron"]["enabled"] = True
        (profile / "config.yaml").write_text(yaml.safe_dump(config))
        async with self.client() as client:
            drift = await client.post(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{ready['id']}"
                "/activate?agent=big-brother",
                json={
                    "expected_revision": ready["revision"],
                    "canonical_digest": ready["canonical_digest"],
                    "certification_digest": ready["certification"]["certification_digest"],
                    "idempotency_key": "activate-drift",
                    "decision": "activate",
                },
                headers=self.trusted_headers(),
            )
            persisted = await client.get(
                f"/xnobrain/api/runtime/v1/agent-blueprints/{ready['id']}?agent=big-brother"
            )
        self.assertEqual(drift.status_code, 409, drift.text)
        self.assertEqual(drift.json()["error"]["code"], "blueprint_certification_drift")
        self.assertFalse(persisted.json()["data"]["certification"]["valid"])

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
