"""Focused API tests for FT0007 Skill Doctor contracts and recovery."""

from __future__ import annotations

import hashlib
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
from xnobrain.trusted_context import (
    conversation_context_signature,
    encode_conversation_context,
    principal_signature,
)


class FakeRouter:
    async def list_connections(self):
        return {"connections": []}

    async def list_models(self):
        return {"data": []}


class SkillDoctorAPITests(unittest.IsolatedAsyncioTestCase):
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
                "RUNTIME_INCLUDE_PACKAGED_SKILLS": "0",
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
    def personal_headers(subject: str = "user:reviewer") -> dict[str, str]:
        return {
            "x-xnobrain-verified-subject": subject,
            "x-xnobrain-principal-signature": principal_signature("internal-test-token", subject),
        }

    @staticmethod
    def organization_headers(
        context: dict[str, object],
        subject: str = "user:reviewer",
    ) -> dict[str, str]:
        encoded = encode_conversation_context(context)
        return {
            "x-xnobrain-verified-subject": subject,
            "x-xnobrain-verified-organization": "org-one",
            "x-xnobrain-principal-signature": principal_signature(
                "internal-test-token", subject, "", "org-one"
            ),
            "x-xnobrain-verified-conversation-context": encoded,
            "x-xnobrain-conversation-context-signature": conversation_context_signature(
                "internal-test-token", subject, "", "org-one", encoded
            ),
        }

    async def create_agent(self, client: AsyncClient) -> str:
        response = await client.post(
            "/xnobrain/api/runtime/v1/agents", json={"display_name": "Doctor"}
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["data"]["id"]

    @staticmethod
    def skill(skill_id: str, description: str, body: str = "Instructions") -> str:
        return (
            f"---\nname: {skill_id}\ndescription: {description}\nversion: 1.0.0\n"
            f"---\n# {skill_id}\n{body}\n"
        )

    def add_skill(
        self,
        agent_id: str,
        skill_id: str,
        description: str,
        body: str = "Instructions",
    ) -> Path:
        path = self.profiles / agent_id / "skills/custom" / skill_id / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.skill(skill_id, description, body), encoding="utf-8")
        return path

    async def static_report(
        self,
        client: AsyncClient,
        agent_id: str,
        key: str = "report-one",
        **overrides,
    ):
        body = {
            "work_context_id": "personal",
            "range_from": 1788220800,
            "range_to": 1788307200,
            "mode": "static",
            "idempotency_key": key,
            **overrides,
        }
        return await client.post(
            f"/xnobrain/api/runtime/v1/agents/{agent_id}/skill-doctor/reports",
            headers=self.personal_headers(),
            json=body,
        )

    async def test_launch_is_idempotent_persisted_and_does_not_start_a_run(self) -> None:
        async with self.client() as client:
            agent_id = await self.create_agent(client)
            path = f"/xnobrain/api/runtime/v1/agents/{agent_id}/skill-doctor/launches"
            first = await client.post(
                path,
                headers=self.personal_headers(),
                json={"idempotency_key": "launch-one"},
            )
            self.assertEqual(first.status_code, 201, first.text)
            replay = await client.post(
                path,
                headers=self.personal_headers(),
                json={"idempotency_key": "launch-one"},
            )
            self.assertEqual(replay.status_code, 201, replay.text)
            launch = first.json()["data"]
            self.assertEqual(replay.json()["data"]["session_id"], launch["session_id"])
            self.assertEqual(len(launch["todos"]), 7)
            self.assertEqual(launch["capabilities"], ["todo"])
            self.assertNotIn("idempotency_key", first.text)
            runs = self.composition.repository.list_conversation_runs(
                agent_id, launch["session_id"]
            )
            self.assertEqual(runs, [])
            record = self.composition.repository.find_skill_doctor_launch_by_session(
                agent_id, launch["session_id"]
            )
            self.assertIsNotNone(record)

    async def test_static_report_is_bounded_and_reports_honest_usage(self) -> None:
        async with self.client() as client:
            agent_id = await self.create_agent(client)
            first = self.add_skill(
                agent_id,
                "alpha",
                "Create monthly finance reports for selected records",
                "Use references/missing.md.",
            )
            self.add_skill(
                agent_id,
                "beta",
                "Create monthly finance reports for selected records",
            )
            event = {
                "schema_version": 1,
                "instrumentation_version": "xnobrain.skill-usage.v1",
                "event_id": "sue_" + "a" * 64,
                "event_type": "skill.requested",
                "agent_id": agent_id,
                "work_context_id": "personal",
                "run_id": "run_one",
                "session_id": "session_one",
                "skill_id": "alpha",
                "skill_digest": "sha256:" + hashlib.sha256(first.read_bytes()).hexdigest(),
                "attribution": "observed",
                "occurred_at": "2026-09-01T12:00:00Z",
                "duration_ms": None,
                "outcome": None,
            }
            self.composition.repository.append_skill_usage_event(event)
            response = await self.static_report(client, agent_id, inventory_limit=2)
            self.assertEqual(response.status_code, 201, response.text)
            report = response.json()["data"]
            self.assertEqual(report["totals"]["skills"], 2)
            self.assertEqual(report["totals"]["requested"], 1)
            self.assertEqual(report["totals"]["observed_loads"], 0)
            self.assertEqual(report["totals"]["distinct_sessions"], 1)
            self.assertEqual(report["totals"]["distinct_runs"], 0)
            self.assertEqual(
                report["context_cost"]["tokenizer"],
                "unicode-codepoint-estimate-v1",
            )
            codes = {finding["code"] for finding in report["findings"]}
            self.assertIn("missing_reference", codes)
            self.assertIn("duplicate_description", codes)
            self.assertIn("no_observed_use", codes)
            self.assertTrue(all(not item["automatic_disable"] for item in report["findings"]))
            self.assertTrue(all(todo["finding_ids"] for todo in report["todos"]), report["todos"])
            self.assertNotIn(str(first), response.text)
            replay = await self.static_report(client, agent_id, inventory_limit=2)
            self.assertEqual(replay.status_code, 201)
            self.assertEqual(replay.json()["data"]["id"], report["id"])

    async def test_selected_sessions_require_consent_owner_and_exact_context(self) -> None:
        async with self.client() as client:
            agent_id = await self.create_agent(client)
            created = await client.post(
                f"/xnobrain/api/runtime/v1/sessions?agent={agent_id}",
                headers=self.personal_headers(),
                json={"title": "Evidence"},
            )
            self.assertEqual(created.status_code, 201, created.text)
            session_id = created.json()["data"]["id"]
            path = f"/xnobrain/api/runtime/v1/agents/{agent_id}/skill-doctor/reports"
            no_consent = await client.post(
                path,
                headers=self.personal_headers(),
                json={
                    "range_from": 1788220800,
                    "range_to": 1788307200,
                    "mode": "selected_sessions",
                    "session_ids": [session_id],
                    "idempotency_key": "selected-no-consent",
                },
            )
            self.assertEqual(no_consent.status_code, 422)
            wrong_user = await client.post(
                path,
                headers=self.personal_headers("user:other"),
                json={
                    "range_from": 1788220800,
                    "range_to": 1788307200,
                    "mode": "selected_sessions",
                    "session_ids": [session_id],
                    "consent_to_read_session_content": True,
                    "idempotency_key": "selected-wrong-user",
                },
            )
            self.assertEqual(wrong_user.status_code, 403, wrong_user.text)
            allowed = await client.post(
                path,
                headers=self.personal_headers(),
                json={
                    "range_from": 1788220800,
                    "range_to": 1788307200,
                    "mode": "selected_sessions",
                    "session_ids": [session_id],
                    "consent_to_read_session_content": True,
                    "idempotency_key": "selected-allowed",
                },
            )
            self.assertEqual(allowed.status_code, 201, allowed.text)
            analysis = allowed.json()["data"]["session_analysis"]
            self.assertTrue(analysis["consented"])
            self.assertFalse(analysis["content_retained"])
            self.assertFalse(analysis["model_used"])

            organization = {
                "schema_version": 1,
                "id": "organization:org-one",
                "owner_kind": "organization",
                "organization_id": "org-one",
                "owner_label": "Org One",
                "payer_kind": "organization_sponsor",
                "sponsor_grant_id": "grant-one",
                "membership_revision_at_create": 1,
                "policy_revision_at_create": 1,
                "state": "active",
            }
            denied = await client.post(
                path,
                headers=self.organization_headers(organization),
                json={
                    "work_context_id": "organization:org-one",
                    "range_from": 1788220800,
                    "range_to": 1788307200,
                    "mode": "selected_sessions",
                    "session_ids": [session_id],
                    "consent_to_read_session_content": True,
                    "idempotency_key": "selected-foreign-context",
                },
            )
            self.assertEqual(denied.status_code, 403, denied.text)

    async def test_disable_reenable_and_rollback_are_reversible_and_idempotent(self) -> None:
        async with self.client() as client:
            agent_id = await self.create_agent(client)
            self.add_skill(agent_id, "alpha", "A" * 501)
            report_response = await self.static_report(client, agent_id)
            self.assertEqual(report_response.status_code, 201, report_response.text)
            report = report_response.json()["data"]
            finding = next(item for item in report["findings"] if item["skill_ids"] == ["alpha"])
            plan_response = await client.post(
                f"/xnobrain/api/runtime/v1/agents/{agent_id}/skill-doctor/reports/"
                f"{report['id']}/plans",
                headers=self.personal_headers(),
                json={
                    "expected_report_revision": report["revision"],
                    "expected_inventory_digest": report["inventory_digest"],
                    "actions": [
                        {
                            "action": "disable",
                            "skill_id": "alpha",
                            "finding_ids": [finding["id"]],
                        }
                    ],
                },
            )
            self.assertEqual(plan_response.status_code, 201, plan_response.text)
            plan = plan_response.json()["data"]
            apply_path = (
                f"/xnobrain/api/runtime/v1/agents/{agent_id}/skill-doctor/plans/{plan['id']}/apply"
            )
            apply_body = {
                "expected_revision": plan["revision"],
                "plan_digest": plan["digest"],
                "confirmation": "confirm",
                "idempotency_key": "apply-disable",
            }
            applied = await client.post(
                apply_path, headers=self.personal_headers(), json=apply_body
            )
            self.assertEqual(applied.status_code, 200, applied.text)
            applied_plan = applied.json()["data"]
            skills = self.composition.service.agents.list_skills(agent_id)["skills"]
            self.assertFalse(
                next(item for item in skills if item["skill_id"] == "alpha")["enabled"]
            )
            replay = await client.post(apply_path, headers=self.personal_headers(), json=apply_body)
            self.assertEqual(replay.status_code, 200, replay.text)
            self.assertEqual(replay.json()["data"]["revision"], applied_plan["revision"])

            rollback = await client.post(
                f"/xnobrain/api/runtime/v1/agents/{agent_id}/skill-doctor/operations/"
                f"{plan['id']}/rollback",
                headers=self.personal_headers(),
                json={
                    "expected_revision": applied_plan["revision"],
                    "expected_applied_digest": applied_plan["apply"]["applied_digest"],
                    "confirmation": "confirm",
                    "idempotency_key": "rollback-disable",
                    "reason": "Restore after review",
                },
            )
            self.assertEqual(rollback.status_code, 200, rollback.text)
            skills = self.composition.service.agents.list_skills(agent_id)["skills"]
            self.assertTrue(next(item for item in skills if item["skill_id"] == "alpha")["enabled"])

            next_report = await self.static_report(client, agent_id, key="report-two")
            current = next_report.json()["data"]
            next_finding = next(
                item for item in current["findings"] if item["skill_ids"] == ["alpha"]
            )
            disabled_config = yaml.safe_load(
                (self.profiles / agent_id / "config.yaml").read_text(encoding="utf-8")
            )
            disabled_config.setdefault("skills", {})["disabled"] = ["alpha"]
            (self.profiles / agent_id / "config.yaml").write_text(
                yaml.safe_dump(disabled_config), encoding="utf-8"
            )
            stale = await client.post(
                f"/xnobrain/api/runtime/v1/agents/{agent_id}/skill-doctor/reports/"
                f"{current['id']}/plans",
                headers=self.personal_headers(),
                json={
                    "expected_report_revision": current["revision"],
                    "expected_inventory_digest": current["inventory_digest"],
                    "actions": [
                        {
                            "action": "re_enable",
                            "skill_id": "alpha",
                            "finding_ids": [next_finding["id"]],
                        }
                    ],
                },
            )
            self.assertEqual(stale.status_code, 409)

    async def test_protected_dependencies_and_rollback_conflicts_fail_closed(self) -> None:
        async with self.client() as client:
            agent_id = await self.create_agent(client)
            self.add_skill(agent_id, "skill-optimizer", "B" * 501)
            self.add_skill(agent_id, "alpha", "A" * 501)
            report_response = await self.static_report(client, agent_id)
            report = report_response.json()["data"]
            protected = next(
                item for item in report["findings"] if item["skill_ids"] == ["skill-optimizer"]
            )
            protected_plan = await client.post(
                f"/xnobrain/api/runtime/v1/agents/{agent_id}/skill-doctor/reports/"
                f"{report['id']}/plans",
                headers=self.personal_headers(),
                json={
                    "expected_report_revision": report["revision"],
                    "expected_inventory_digest": report["inventory_digest"],
                    "actions": [
                        {
                            "action": "disable",
                            "skill_id": "skill-optimizer",
                            "finding_ids": [protected["id"]],
                        }
                    ],
                },
            )
            self.assertEqual(protected_plan.status_code, 403, protected_plan.text)

            alpha = next(item for item in report["findings"] if item["skill_ids"] == ["alpha"])
            self.composition.repository.put_cron(
                {
                    "id": "cron-one",
                    "agent_id": agent_id,
                    "skills": ["alpha"],
                    "enabled": True,
                }
            )
            blocked_response = await client.post(
                f"/xnobrain/api/runtime/v1/agents/{agent_id}/skill-doctor/reports/"
                f"{report['id']}/plans",
                headers=self.personal_headers(),
                json={
                    "expected_report_revision": report["revision"],
                    "expected_inventory_digest": report["inventory_digest"],
                    "actions": [
                        {
                            "action": "disable",
                            "skill_id": "alpha",
                            "finding_ids": [alpha["id"]],
                        }
                    ],
                },
            )
            self.assertEqual(blocked_response.status_code, 201, blocked_response.text)
            blocked = blocked_response.json()["data"]
            self.assertEqual(blocked["status"], "blocked")
            self.assertEqual(blocked["actions"][0]["dependencies"][0]["kind"], "schedule")
            apply_blocked = await client.post(
                f"/xnobrain/api/runtime/v1/agents/{agent_id}/skill-doctor/plans/"
                f"{blocked['id']}/apply",
                headers=self.personal_headers(),
                json={
                    "expected_revision": blocked["revision"],
                    "plan_digest": blocked["digest"],
                    "confirmation": "confirm",
                    "idempotency_key": "blocked-apply",
                },
            )
            self.assertEqual(apply_blocked.status_code, 409)

    async def test_openapi_uses_typed_doctor_bodies(self) -> None:
        async with self.client() as client:
            schema = (await client.get("/openapi.json")).json()
        paths = schema["paths"]
        root = "/xnobrain/api/runtime/v1/agents/{agent_id}/skill-doctor"
        self.assertIn("SkillDoctorReportCreate", json.dumps(paths[f"{root}/reports"]))
        self.assertIn(
            "SkillDoctorPlanCreate", json.dumps(paths[f"{root}/reports/{{report_id}}/plans"])
        )
        self.assertIn("SkillDoctorApply", json.dumps(paths[f"{root}/plans/{{plan_id}}/apply"]))
        self.assertIn(
            "SkillDoctorRollback",
            json.dumps(paths[f"{root}/operations/{{plan_id}}/rollback"]),
        )
