"""Automatic creation through the native registry, and certification recovery."""

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import yaml

from xnobrain.integrations.agent_certification import RuntimeCertificationExecutor
from xnobrain.integrations.agent_maker_tools import bind_run, install_tools
from xnobrain.integrations.llm_router_support import LLMRouterAPIError
from xnobrain.tests import test_agent_blueprints as fixtures
from xnobrain.trusted_context import TrustedRequestContext


class AgentMakerAutoTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixtures.AgentBlueprintTests.setUp(self)
        self.service.router.list_models = AsyncMock(
            return_value={
                "default_model": "cc/claude-sonnet-5-5",
                "data": [
                    {"id": "cc/claude-sonnet-5-5", "provider": "claude"},
                    {"id": "research-approved", "provider": "xnobrain"},
                ],
            }
        )

    tearDown = fixtures.AgentBlueprintTests.tearDown
    client = fixtures.AgentBlueprintTests.client
    create = fixtures.AgentBlueprintTests.create
    spec = staticmethod(fixtures.AgentBlueprintTests.spec)
    certification_cases = staticmethod(fixtures.AgentBlueprintTests.certification_cases)
    trusted_headers = fixtures.AgentBlueprintTests.trusted_headers
    approved_scaffold = fixtures.AgentBlueprintTests.approved_scaffold

    def scope(self):
        trusted = TrustedRequestContext("user:kim")
        session = self.service.create_conversation("big-brother", {}, trusted)["id"]
        run = {
            "id": "run_" + "a" * 32,
            "agent_id": "big-brother",
            "conversation_id": session,
            "status": "running",
            "actor_user_id": trusted.subject,
            "actor_tenant_id": "",
            "ownership_context": self.service._personal_context(),
        }
        self.service.repository.put_conversation_run(run)
        return trusted, session, run

    @staticmethod
    def build_request(record, key="certify-auto"):
        return {
            "blueprint_id": record["id"],
            "expected_revision": record["revision"],
            "canonical_digest": record["canonical_digest"],
            "idempotency_key": key,
            "cases": fixtures.AgentBlueprintTests.certification_cases(),
            "max_cost_usd": 0.1,
            "timeout_seconds": 30,
            "max_turns_per_case": 4,
            "decision": "certify",
        }

    async def dispatch(self, tool, args, session):
        from tools.registry import registry

        raw = await asyncio.to_thread(registry.dispatch, tool, args, session_id=session)
        return json.loads(raw)

    async def test_native_tools_create_one_active_agent_without_manual_approval(self):
        trusted, session, run = self.scope()
        executor = fixtures.FakeCertificationExecutor()
        self.service.agent_certification_executor = executor
        with bind_run(self.service.agent_maker, "big-brother", session, run["id"], trusted):
            parent = SimpleNamespace(tools=[], valid_tool_names=set())
            child = SimpleNamespace(tools=[], valid_tool_names=set(), _delegate_depth=1)
            install_tools(parent, session)
            install_tools(child, session)
            self.assertEqual(len(parent.tools), 3)
            self.assertEqual(child.tools, [])
            inspected = await self.dispatch("agent_maker_inspect", {}, session)
            self.assertTrue(inspected["data"]["auto_accept"])
            model_id = inspected["data"]["model_catalog"]["data"][0]["id"]
            spec = self.spec()
            spec["model_slot"]["alias"] = model_id
            args = {
                "intent": "Create a researcher",
                "blueprint": spec,
                "idempotency_key": "researcher",
            }
            prepared = await self.dispatch("agent_maker_prepare", args, session)
            self.assertTrue(prepared["success"], prepared)
            record = prepared["data"]
            repeated = await self.dispatch("agent_maker_prepare", args, session)
            self.assertEqual(record["id"], repeated["data"]["id"])
            built = await self.dispatch("agent_maker_build", self.build_request(record), session)
            self.assertTrue(built["success"], built)
            active = built["data"]
            self.assertEqual(active["status"], "active")
            self.assertEqual(active["approval"]["mode"], "auto")
            self.assertEqual(active["approval"]["approved_by"], trusted.subject)
            self.assertEqual(len(executor.calls), 4)
            self.assertEqual(
                {call["profile_id"] for call in executor.calls}, {record["target_profile_id"]}
            )
            self.service.router.list_models.side_effect = LLMRouterAPIError("offline")
            replay = await self.dispatch("agent_maker_build", self.build_request(record), session)
            self.assertEqual(replay["data"]["status"], "active")
            self.assertEqual(len(executor.calls), 4)
            config = yaml.safe_load(
                (self.profiles / record["target_profile_id"] / "config.yaml").read_text()
            )
            self.assertFalse(config["xnobrain"]["paused"])
            self.assertFalse(config["cron"]["enabled"])
            self.assertFalse(config["mcp"]["enabled"])
            self.assertEqual(config["approvals"]["mode"], "manual")
            self.assertEqual(config["model"]["default"], model_id)
            self.assertEqual(
                self.service.get_agent(record["target_profile_id"])["config"]["model"], model_id
            )
            self.assertEqual(len(list(self.profiles.glob("agent-*"))), 1)
        denied = await self.dispatch("agent_maker_inspect", {}, session)
        self.assertFalse(denied["success"])

    async def test_prepare_rejects_model_names_absent_from_router_catalog(self):
        trusted, session, run = self.scope()
        with bind_run(self.service.agent_maker, "big-brother", session, run["id"], trusted):
            for alias in ("claude-sonnet-5-5", "cc/nonexistent", "auto"):
                with self.subTest(alias=alias):
                    spec = self.spec()
                    spec["model_slot"]["alias"] = alias
                    result = await self.dispatch(
                        "agent_maker_prepare",
                        {"intent": "Create", "blueprint": spec, "idempotency_key": "invalid"},
                        session,
                    )
                    self.assertEqual(result["error"]["code"], "agent_maker_model_unavailable")
        self.assertEqual(self.service.list_agent_blueprints("big-brother")["blueprints"], [])
        self.assertEqual(len(list(self.profiles.glob("agent-*"))), 0)

    async def test_catalog_failure_does_not_fallback_or_save_blueprint(self):
        trusted, session, run = self.scope()
        self.service.router.list_models.side_effect = LLMRouterAPIError("private upstream detail")
        with bind_run(self.service.agent_maker, "big-brother", session, run["id"], trusted):
            for tool, args in (
                ("agent_maker_inspect", {}),
                (
                    "agent_maker_prepare",
                    {"intent": "Create", "blueprint": self.spec(), "idempotency_key": "offline"},
                ),
            ):
                result = await self.dispatch(tool, args, session)
                self.assertEqual(result["error"]["code"], "agent_maker_model_catalog_unavailable")
                self.assertNotIn("private upstream detail", json.dumps(result))
        self.assertEqual(self.service.list_agent_blueprints("big-brother")["blueprints"], [])

    async def test_removed_model_blocks_build_without_approving_or_scaffolding(self):
        trusted, session, run = self.scope()
        with bind_run(self.service.agent_maker, "big-brother", session, run["id"], trusted):
            prepared = await self.dispatch(
                "agent_maker_prepare",
                {"intent": "Create", "blueprint": self.spec(), "idempotency_key": "removed"},
                session,
            )
            record = prepared["data"]
            self.service.router.list_models.return_value = {"data": []}
            result = await self.dispatch("agent_maker_build", self.build_request(record), session)
            self.assertEqual(result["error"]["code"], "agent_maker_model_unavailable")
            stored = self.service.get_agent_blueprint("big-brother", record["id"])
            self.assertEqual(stored["status"], "blueprint_ready")
            self.assertIsNone(stored["approval"])
            self.assertEqual(stored["canonical_digest"], record["canonical_digest"])
            self.assertFalse((self.profiles / record["target_profile_id"]).exists())

    async def test_stop_during_catalog_lookup_prevents_preparation(self):
        trusted, session, run = self.scope()

        async def stop_and_return_catalog():
            self.service.repository.put_conversation_run(
                {**run, "cancellation": {"requested": True}}
            )
            return {"data": [{"id": "research-approved"}]}

        self.service.router.list_models.side_effect = stop_and_return_catalog
        with bind_run(self.service.agent_maker, "big-brother", session, run["id"], trusted):
            result = await self.dispatch(
                "agent_maker_prepare",
                {"intent": "Create", "blueprint": self.spec(), "idempotency_key": "cancel"},
                session,
            )
            self.assertEqual(result["error"]["code"], "agent_maker_run_inactive")
        self.assertEqual(self.service.list_agent_blueprints("big-brother")["blueprints"], [])

    async def test_missing_foreign_and_cancelled_authority_cannot_create(self):
        trusted, session, run = self.scope()
        with bind_run(self.service.agent_maker, "big-brother", session, run["id"], trusted):
            self.assertFalse((await self.dispatch("agent_maker_inspect", {}, "other"))["success"])
            injected = await self.dispatch(
                "agent_maker_prepare",
                {
                    "intent": "Create",
                    "blueprint": self.spec(),
                    "idempotency_key": "spoof",
                    "trusted_subject": "someone-else",
                },
                session,
            )
            self.assertEqual(injected["error"]["code"], "invalid_agent_maker_request")
            self.service.repository.put_conversation_run(
                {**run, "cancellation": {"requested": True}}
            )
            stopped = await self.dispatch(
                "agent_maker_prepare",
                {
                    "intent": "Create",
                    "blueprint": self.spec(),
                    "idempotency_key": "cancelled",
                },
                session,
            )
            self.assertEqual(stopped["error"]["code"], "agent_maker_run_inactive")
            self.assertEqual(self.service.list_agent_blueprints("big-brother")["blueprints"], [])
        with self.assertRaises(Exception) as caught:
            with bind_run(
                self.service.agent_maker,
                "big-brother",
                session,
                run["id"],
                TrustedRequestContext("foreign"),
            ):
                pass
        self.assertEqual(caught.exception.code, "conversation_owner_forbidden")

    async def test_stopping_chat_during_certification_never_activates(self):
        trusted, session, run = self.scope()
        started = asyncio.Event()

        async def stalled(**kwargs):
            started.set()
            await asyncio.Event().wait()

        self.service.agent_certification_executor = SimpleNamespace(execute=stalled)
        with bind_run(self.service.agent_maker, "big-brother", session, run["id"], trusted):
            prepared = await self.dispatch(
                "agent_maker_prepare",
                {
                    "intent": "Create",
                    "blueprint": self.spec(),
                    "idempotency_key": "stopped",
                },
                session,
            )
            record = prepared["data"]
            build = asyncio.create_task(
                self.dispatch("agent_maker_build", self.build_request(record), session)
            )
            await asyncio.wait_for(started.wait(), 3)
            self.service.repository.put_conversation_run(
                {**run, "cancellation": {"requested": True}}
            )
            result = await asyncio.wait_for(build, 3)
            self.assertEqual(result["error"]["code"], "agent_maker_run_inactive")
            stored = self.service.get_agent_blueprint("big-brother", record["id"])
            self.assertEqual(stored["status"], "certification_failed")
            self.assertEqual(stored["certification"]["status"], "cancelled")
            self.assertIsNone(stored["activation"])

    async def test_personal_chat_cannot_accept_a_foreign_context_blueprint(self):
        trusted, session, run = self.scope()
        record = self.service.create_agent_blueprint(
            "big-brother",
            {
                "intent": "Foreign specialist",
                "blueprint": self.spec(),
                "work_context_id": "personal",
            },
            trusted,
        )
        owner = self.service._blueprint_owner_profile("big-brother")
        stored = self.service.repository.get_agent_blueprint(owner, record["id"])
        self.service.repository.update_agent_blueprint(
            owner,
            record["id"],
            1,
            {
                **stored,
                "work_context_id": "organization:other",
                "ownership_context": {**stored["ownership_context"], "id": "organization:other"},
            },
        )
        with bind_run(self.service.agent_maker, "big-brother", session, run["id"], trusted):
            inspection = await self.dispatch("agent_maker_inspect", {}, session)
            self.assertEqual(inspection["data"]["blueprints"], [])
            built = await self.dispatch("agent_maker_build", self.build_request(record), session)
            self.assertEqual(built["error"]["code"], "blueprint_context_not_verified")
            self.assertEqual(len(list(self.profiles.glob("agent-*"))), 0)

    async def test_real_session_database_runs_four_cases_and_retries_same_agent(self):
        record = await self.approved_scaffold()
        target = record["target_profile_id"]
        trusted = TrustedRequestContext("user:kim")
        self.service.agent_certification_executor = RuntimeCertificationExecutor(
            self.service.agents,
            lambda agent, body: self.service.create_conversation(agent, body, trusted),
        )
        calls = []

        async def run(prepared, **kwargs):
            calls.append(prepared)
            if len(calls) == 2:
                raise RuntimeError("synthetic interrupted case")
            if "TOOL" in prepared["message"]:
                kwargs["tool_progress_callback"]("tool.completed", "workspace-files")
            response = "I refuse: unauthorized" if "REFUSE" in prepared["message"] else "COMPLETE"
            return {"final_response": response}, {"total_tokens": 5}

        body = self.build_request(record)
        body.pop("blueprint_id")
        with (
            patch.object(
                self.service.agents,
                "_prepare_chat_command",
                side_effect=lambda agent, data, **kwargs: data,
            ),
            patch.object(self.service.agents, "_run_session_agent", new=AsyncMock(side_effect=run)),
        ):
            async with self.client() as client:
                url = f"/xnobrain/api/runtime/v1/agent-blueprints/{record['id']}/certifications?agent=big-brother"
                failed = await client.post(url, json=body, headers=self.trusted_headers())
                self.assertEqual(failed.status_code, 200, failed.text)
                self.assertEqual(failed.json()["data"]["status"], "certification_failed")
                self.assertEqual(len(calls), 2)
                retry = {**body, "idempotency_key": "retry-after-fix"}
                passed = await client.post(url, json=retry, headers=self.trusted_headers())
                self.assertEqual(passed.status_code, 200, passed.text)
                ready = passed.json()["data"]
                self.assertEqual(ready["status"], "ready_to_activate")
                self.assertEqual(len(ready["certification"]["results"]), 4)
                self.assertEqual(len(ready["certification_history"]), 1)
                self.assertEqual(ready["target_profile_id"], target)
                await client.post(url, json=retry, headers=self.trusted_headers())
                await client.post(url, json=body, headers=self.trusted_headers())
                self.assertEqual(len(calls), 6)
                changed = {**body, "max_cost_usd": 0.2}
                conflict = await client.post(url, json=changed, headers=self.trusted_headers())
                self.assertEqual(conflict.status_code, 409)
        sessions = self.service.agents.list_conversations(target)["conversations"]
        self.assertEqual(len(sessions), 6)
        self.assertEqual(len({item["title"] for item in sessions}), 6)
        self.assertEqual(len(list(self.profiles.glob("agent-*"))), 1)
