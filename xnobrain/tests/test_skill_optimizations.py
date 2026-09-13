"""Focused API tests for FT0004 optimization authorization and lifecycle."""

from __future__ import annotations

import hashlib
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
from xnobrain.trusted_context import principal_signature


class FakeRouter:
    async def list_connections(self):
        return {"connections": []}

    async def list_models(self):
        return {"data": []}


class SkillOptimizationAPITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
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
                    "providers": {},
                    "approvals": {"mode": "manual"},
                    "terminal": {"backend": "local"},
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
                "DATA_DIR": self.temporary.name,
                "RUNTIME_INTERNAL_SERVICE_TOKEN": "internal-test-token",
            },
        )
        self.environment.start()
        app = FastAPI()
        self.composition = XNOBrainApplication(
            AgentManager(
                root_profile=self.root,
                profiles_root=self.profiles,
                legacy_agents_root=base / "legacy",
            ),
            GlobalConfigManager(root_profile=self.root),
            FakeRouter(),
        )
        self.composition.register(app)
        self.app = app

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary.cleanup()

    def client(self) -> AsyncClient:
        return AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test")

    @staticmethod
    def headers(subject: str = "user:reviewer") -> dict[str, str]:
        return {
            "x-xnobrain-verified-subject": subject,
            "x-xnobrain-principal-signature": principal_signature("internal-test-token", subject),
        }

    async def create_agent(self, client: AsyncClient) -> str:
        response = await client.post(
            "/xnobrain/api/runtime/v1/agents", json={"display_name": "Optimizer"}
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["data"]["id"]

    @staticmethod
    def skill(description: str, body: str) -> str:
        return (
            "---\nname: report-writer\ndescription: "
            + description
            + "\n---\n# Report writer\n"
            + body
            + "\n"
        )

    async def test_full_evaluate_approve_apply_and_rollback(self) -> None:
        async with self.client() as client:
            agent_id = await self.create_agent(client)
            profile = self.profiles / agent_id
            skill = profile / "skills/custom/report-writer/SKILL.md"
            skill.parent.mkdir(parents=True)
            baseline = self.skill("Create monthly reports", "Baseline")
            skill.write_text(baseline, encoding="utf-8")
            baseline_digest = "sha256:" + hashlib.sha256(baseline.encode()).hexdigest()
            candidate = self.skill("Create monthly finance reports", "Candidate")
            created = await client.post(
                f"/xnobrain/api/runtime/v1/agents/{agent_id}/skill-optimizations",
                headers=self.headers(),
                json={
                    "work_context_id": "personal",
                    "range_from": 1,
                    "range_to": 2,
                    "patches": [
                        {
                            "skill_id": "report-writer",
                            "expected_baseline_digest": baseline_digest,
                            "candidate_content": candidate,
                            "enable": True,
                        }
                    ],
                    "max_evaluation_cost_usd": 0,
                },
            )
            self.assertEqual(created.status_code, 201, created.text)
            operation = created.json()["data"]
            operation_id = operation["id"]
            self.assertNotIn("candidate_content", created.text)

            evaluated = await client.post(
                f"/xnobrain/api/runtime/v1/agents/{agent_id}/skill-optimizations/"
                f"{operation_id}/evaluations",
                headers=self.headers(),
                json={
                    "expected_revision": operation["revision"],
                    "candidate_digest": operation["candidate_digest"],
                    "max_cost_usd": 0,
                    "cases": [
                        {
                            "case_id": "positive",
                            "prompt": "create a finance report",
                            "expected_trigger": True,
                            "partition": "development",
                        },
                        {
                            "case_id": "negative",
                            "prompt": "write a poem",
                            "expected_trigger": False,
                            "partition": "held_out",
                        },
                    ],
                },
            )
            self.assertEqual(evaluated.status_code, 200, evaluated.text)
            operation = evaluated.json()["data"]
            self.assertEqual(operation["evaluation"]["actual_cost_usd"], 0)
            self.assertTrue(operation["evaluation"]["matched_settings"])

            untrusted = await client.post(
                f"/xnobrain/api/runtime/v1/agents/{agent_id}/skill-optimizations/"
                f"{operation_id}/approvals",
                json={
                    "expected_revision": operation["revision"],
                    "candidate_digest": operation["candidate_digest"],
                    "evaluation_digest": operation["evaluation"]["digest"],
                    "decision": "approve",
                },
            )
            self.assertEqual(untrusted.status_code, 401)

            approved = await client.post(
                f"/xnobrain/api/runtime/v1/agents/{agent_id}/skill-optimizations/"
                f"{operation_id}/approvals",
                headers=self.headers(),
                json={
                    "expected_revision": operation["revision"],
                    "candidate_digest": operation["candidate_digest"],
                    "evaluation_digest": operation["evaluation"]["digest"],
                    "decision": "approve",
                },
            )
            self.assertEqual(approved.status_code, 200, approved.text)
            operation = approved.json()["data"]

            applied = await client.post(
                f"/xnobrain/api/runtime/v1/agents/{agent_id}/skill-optimizations/"
                f"{operation_id}/apply",
                headers=self.headers(),
                json={
                    "expected_revision": operation["revision"],
                    "candidate_digest": operation["candidate_digest"],
                    "approval_digest": operation["approval"]["digest"],
                    "idempotency_key": "apply-one",
                },
            )
            self.assertEqual(applied.status_code, 200, applied.text)
            operation = applied.json()["data"]
            self.assertEqual(skill.read_text(encoding="utf-8"), candidate)
            self.assertTrue(operation["apply"]["checkpoint_retained"])

            rolled_back = await client.post(
                f"/xnobrain/api/runtime/v1/agents/{agent_id}/skill-optimizations/"
                f"{operation_id}/rollback",
                headers=self.headers(),
                json={
                    "expected_revision": operation["revision"],
                    "expected_applied_digest": operation["candidate_digest"],
                    "idempotency_key": "rollback-one",
                    "reason": "Human requested restoration",
                },
            )
            self.assertEqual(rolled_back.status_code, 200, rolled_back.text)
            self.assertEqual(skill.read_text(encoding="utf-8"), baseline)
            self.assertEqual(rolled_back.json()["data"]["status"], "rolled_back")

    async def test_concurrent_edit_and_protected_skills_fail_closed(self) -> None:
        async with self.client() as client:
            agent_id = await self.create_agent(client)
            profile = self.profiles / agent_id
            skill = profile / "skills/custom/report-writer/SKILL.md"
            skill.parent.mkdir(parents=True)
            baseline = self.skill("Create reports", "Baseline")
            skill.write_text(baseline, encoding="utf-8")
            digest = "sha256:" + hashlib.sha256(baseline.encode()).hexdigest()
            created = await client.post(
                f"/xnobrain/api/runtime/v1/agents/{agent_id}/skill-optimizations",
                headers=self.headers(),
                json={
                    "range_from": 1,
                    "range_to": 2,
                    "patches": [
                        {
                            "skill_id": "report-writer",
                            "expected_baseline_digest": digest,
                            "candidate_content": self.skill("Create finance reports", "New"),
                        }
                    ],
                },
            )
            self.assertEqual(created.status_code, 201, created.text)
            skill.write_text(self.skill("User edit", "Concurrent"), encoding="utf-8")
            operation = created.json()["data"]
            evaluated = await client.post(
                f"/xnobrain/api/runtime/v1/agents/{agent_id}/skill-optimizations/"
                f"{operation['id']}/evaluations",
                headers=self.headers(),
                json={
                    "expected_revision": 1,
                    "candidate_digest": operation["candidate_digest"],
                    "cases": [
                        {
                            "case_id": "p",
                            "prompt": "finance reports",
                            "expected_trigger": True,
                            "partition": "development",
                        },
                        {
                            "case_id": "n",
                            "prompt": "poem",
                            "expected_trigger": False,
                            "partition": "held_out",
                        },
                    ],
                },
            )
            self.assertEqual(evaluated.status_code, 409, evaluated.text)
            protected = await client.post(
                f"/xnobrain/api/runtime/v1/agents/{agent_id}/skill-optimizations",
                headers=self.headers(),
                json={
                    "range_from": 1,
                    "range_to": 2,
                    "patches": [
                        {
                            "skill_id": "big-brother-control",
                            "expected_baseline_digest": None,
                            "candidate_content": "---\nname: big-brother-control\n"
                            "description: protected\n---\n",
                        }
                    ],
                },
            )
            self.assertEqual(protected.status_code, 403)

    async def test_usage_context_authorization_and_digest_filter(self) -> None:
        async with self.client() as client:
            agent_id = await self.create_agent(client)
            event = {
                "schema_version": 1,
                "instrumentation_version": "xnobrain.skill-usage.v1",
                "event_id": "sue_" + "c" * 64,
                "event_type": "skill.loaded",
                "agent_id": agent_id,
                "work_context_id": "organization:private",
                "run_id": "run_one",
                "session_id": "session_one",
                "skill_id": "report-writer",
                "skill_digest": "sha256:" + "1" * 64,
                "attribution": "observed",
                "occurred_at": "2026-09-01T12:00:00Z",
                "duration_ms": 5,
                "outcome": None,
            }
            self.composition.service.repository.append_skill_usage_event(event)
            path = f"/xnobrain/api/runtime/v1/agents/{agent_id}/skills/usage"
            forbidden = await client.get(
                path + "?from=2026-09-01&to=2026-09-02&context=organization:private"
            )
            self.assertEqual(forbidden.status_code, 401)
            personal = await client.get(path + "?from=2026-09-01&to=2026-09-02")
            self.assertEqual(personal.status_code, 200)
            self.assertEqual(personal.json()["data"]["items"], [])
            filtered = await client.get(
                path + "?from=2026-09-01&to=2026-09-02&digest=sha256:" + "2" * 64
            )
            self.assertEqual(filtered.status_code, 200)
            self.assertEqual(filtered.json()["data"]["items"], [])
