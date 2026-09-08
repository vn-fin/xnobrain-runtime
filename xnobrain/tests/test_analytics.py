"""Contract and unit tests for usage analytics and advisory budgets."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import yaml
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from xnobrain.app import XNOBrainApplication
from xnobrain.integrations import AgentManager, GlobalConfigManager
from xnobrain.integrations.analytics import (
    aggregate_profile,
    period_spend,
)
from xnobrain.services.analytics import _sunday_start


class FakeRouter:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    async def list_connections(self):
        return {"connections": []}

    async def list_models(self):
        return {"data": []}

    async def usage(self, _model):
        return {
            "available": False,
            "provider": "",
            "model": "auto",
            "plan": "",
            "message": "",
            "quotas": [],
        }


_SESSION_COLUMNS = (
    "id, source, model, billing_provider, started_at, input_tokens, output_tokens, "
    "cache_read_tokens, cache_write_tokens, reasoning_tokens, estimated_cost_usd, "
    "actual_cost_usd, api_call_count"
)


class AnalyticsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.root = base / "root"
        self.profiles = base / "profiles"
        self.router_data = base / "central-router-placeholder"
        self.root.mkdir(parents=True)
        self.profiles.mkdir(parents=True)
        (self.root / "config.yaml").write_text(
            yaml.safe_dump(
                {
                    "model": {"provider": "custom:xnobrain", "default": "auto"},
                    "providers": {},
                    "agent": {"reasoning_effort": "medium"},
                    "approvals": {"mode": "manual"},
                    "terminal": {"backend": "local"},
                }
            ),
            encoding="utf-8",
        )
        from unittest.mock import patch

        self.environment = patch.dict(
            os.environ,
            {
                "HERMES_HOME": str(self.root),
                "HERMES_ROOT_PROFILE": str(self.root),
                "HERMES_PROFILES_ROOT": str(self.profiles),
                "DATA_DIR": self.temporary.name,
            },
        )
        self.environment.start()
        app = FastAPI()
        composition = XNOBrainApplication(
            AgentManager(
                root_profile=self.root,
                profiles_root=self.profiles,
                legacy_agents_root=base / "legacy-agents",
            ),
            GlobalConfigManager(root_profile=self.root),
            FakeRouter(self.router_data),
        )
        composition.register(app)
        self.analytics = composition.service.analytics
        self.app = app

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def client(self):
        return AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test")

    async def _create_agent(self, client, display_name):
        response = await client.post(
            "/xnobrain/api/runtime/v1/agents", json={"display_name": display_name}
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["data"]["id"]

    def _insert(
        self, agent_id, *, model, inp, out, est, act=0.0, started_at=None, provider="anthropic"
    ):
        db = self.profiles / agent_id / "state.db"
        session_id = uuid.uuid4().hex
        conn = sqlite3.connect(db)
        try:
            conn.execute(
                f"INSERT INTO sessions ({_SESSION_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    session_id,
                    "api",
                    model,
                    provider,
                    started_at if started_at is not None else time.time() - 3600,
                    inp,
                    out,
                    0,
                    0,
                    0,
                    est,
                    act,
                    1,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return session_id

    # ---- unit: the read-only aggregator ------------------------------------

    def test_aggregate_profile_windows_and_buckets(self):
        profile = self.profiles / "unit"
        profile.mkdir(parents=True)
        conn = sqlite3.connect(profile / "state.db")
        conn.execute(
            "CREATE TABLE sessions (id TEXT, source TEXT, model TEXT, "
            "billing_provider TEXT, started_at REAL, input_tokens INTEGER, "
            "output_tokens INTEGER, cache_read_tokens INTEGER, "
            "cache_write_tokens INTEGER, reasoning_tokens INTEGER, "
            "estimated_cost_usd REAL, actual_cost_usd REAL, api_call_count INTEGER)"
        )
        now = time.time()
        rows = [
            (
                uuid.uuid4().hex,
                "api",
                "m1",
                "anthropic",
                now - 3600,
                100,
                50,
                0,
                0,
                0,
                0.10,
                0.0,
                1,
            ),
            (uuid.uuid4().hex, "api", "m1", "anthropic", now - 7200, 40, 20, 0, 0, 0, 0.04, 0.0, 1),
            (
                uuid.uuid4().hex,
                "api",
                "m2",
                "openai",
                now - 90 * 86400,
                999,
                999,
                0,
                0,
                0,
                9.99,
                0.0,
                1,
            ),
        ]
        conn.executemany(
            f"INSERT INTO sessions ({_SESSION_COLUMNS}) VALUES ({','.join('?' * 13)})", rows
        )
        conn.commit()
        conn.close()

        out = aggregate_profile(profile, start_epoch=now - 30 * 86400, end_epoch=now, bucket="day")
        self.assertEqual(out["totals"]["input_tokens"], 140)  # 100 + 40, old row excluded
        self.assertEqual(out["totals"]["output_tokens"], 70)
        self.assertEqual(out["totals"]["sessions"], 2)
        self.assertAlmostEqual(out["totals"]["estimated_cost_usd"], 0.14, places=6)
        self.assertEqual({row["model"] for row in out["by_model"]}, {"m1"})

        spend = period_spend(
            profile,
            since_epoch=now - 30 * 86400,
            until_epoch=now,
            cost_basis="estimated",
        )
        self.assertAlmostEqual(spend, 0.14, places=6)

    # ---- integration: routes end-to-end ------------------------------------

    async def test_agents_list_and_cross_agent_totals(self):
        async with self.client() as client:
            a = await self._create_agent(client, "Agent A")
            b = await self._create_agent(client, "Agent B")
            self._insert(a, model="m1", inp=100, out=50, est=0.10)
            self._insert(b, model="m2", inp=200, out=100, est=0.20)

            agents = (await client.get("/xnobrain/api/runtime/v1/analytics/agents")).json()["data"][
                "agents"
            ]
            self.assertEqual(
                {row["agent_id"] for row in agents},
                {"big-brother", a, b},
            )

            data = (await client.get("/xnobrain/api/runtime/v1/analytics/usage")).json()["data"]
            self.assertEqual(data["totals"]["input_tokens"], 300)
            self.assertEqual(data["totals"]["output_tokens"], 150)
            self.assertEqual(data["totals"]["total_tokens"], 450)
            self.assertEqual(data["totals"]["sessions"], 2)
            self.assertAlmostEqual(data["totals"]["cost_usd"], 0.30, places=6)
            self.assertEqual(data["totals"]["cost_basis"], "estimated")
            self.assertEqual({row["model"] for row in data["by_model"]}, {"m1", "m2"})
            self.assertEqual(len(data["agents"]), 3)
            self.assertEqual(data["agents_available"], 3)

    async def test_combined_usage_lists_agents_once(self):
        async with self.client() as client:
            await self._create_agent(client, "Agent A")
            with patch.object(
                self.analytics,
                "_agents",
                wraps=self.analytics._agents,
            ) as list_agents:
                response = await client.get("/xnobrain/api/runtime/v1/analytics/usage")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(list_agents.call_count, 1)

    async def test_parallel_dashboard_endpoints_share_one_computation(self):
        async with self.client() as client:
            agent_id = await self._create_agent(client, "Agent A")
            self._insert(
                agent_id,
                model="cx/gpt-5",
                provider="codex",
                inp=200,
                out=50,
                est=0.25,
                started_at=datetime(2026, 7, 15, 12, tzinfo=timezone.utc).timestamp(),
            )
            query = "?from=2026-07-01&to=2026-08-01&bucket=hour"
            with patch(
                "xnobrain.services.analytics.aggregate_profile",
                wraps=aggregate_profile,
            ) as aggregate:
                overview_response, models_response, series_response = await asyncio.gather(
                    client.get(f"/xnobrain/api/runtime/v1/analytics/overview{query}"),
                    client.get(f"/xnobrain/api/runtime/v1/analytics/models{query}"),
                    client.get(f"/xnobrain/api/runtime/v1/analytics/timeseries{query}"),
                )

            self.assertEqual(overview_response.status_code, 200)
            self.assertEqual(models_response.status_code, 200)
            self.assertEqual(series_response.status_code, 200)
            overview = overview_response.json()["data"]
            models = models_response.json()["data"]
            series = series_response.json()["data"]
            self.assertNotIn("series", overview)
            self.assertNotIn("by_model", overview)
            self.assertEqual(models["by_model"][0]["model"], "cx/gpt-5")
            self.assertEqual(models["by_provider"][0]["provider"], "codex")
            self.assertEqual(series["bucket"], "hour")
            # The root profile and the created profile are each read once.
            self.assertEqual(aggregate.call_count, 2)

    async def test_agent_multiselect_scopes_totals_and_reads(self):
        async with self.client() as client:
            a = await self._create_agent(client, "Agent A")
            b = await self._create_agent(client, "Agent B")
            self._insert(a, model="m1", inp=100, out=50, est=0.10)
            self._insert(b, model="m2", inp=200, out=100, est=0.20)

            data = (
                await client.get(f"/xnobrain/api/runtime/v1/analytics/usage?agents={a}")
            ).json()["data"]
            self.assertEqual(data["totals"]["input_tokens"], 100)
            self.assertEqual(data["totals"]["sessions"], 1)
            self.assertEqual(data["agents_selected"], [a])
            self.assertEqual(len(data["agents"]), 1)
            # subset totals equal that agent's per-agent totals alone
            self.assertEqual(data["agents"][0]["agent_id"], a)
            self.assertEqual(data["agents"][0]["totals"]["input_tokens"], 100)

    async def test_workspace_usage_uses_only_current_runtime_profiles(self):
        async with self.client() as client:
            deleted = await self._create_agent(client, "Disposable")
            kept = await self._create_agent(client, "Kept")
            self._insert(deleted, model="gpt-5", inp=100, out=20, est=0.10)

            before = (await client.get("/xnobrain/api/runtime/v1/analytics/usage?days=30")).json()[
                "data"
            ]
            self.assertEqual(before["totals"]["total_tokens"], 120)
            self.assertEqual(before["source"]["kind"], "live_profiles")
            self.assertFalse(before["source"]["durable"])

            shutil.rmtree(self.profiles / deleted)
            self.analytics.agents.sync_profiles_registry()

            after = (await client.get("/xnobrain/api/runtime/v1/analytics/usage?days=30")).json()[
                "data"
            ]
            self.assertEqual(after["totals"]["total_tokens"], 0)
            self.assertEqual(after["totals"]["cost_usd"], 0)
            self.assertEqual(
                {row["agent_id"] for row in after["agents"]},
                {"big-brother", kept},
            )
            self.assertFalse(after["attribution"]["deleted_usage_included"])

    async def test_time_range_and_bucket(self):
        async with self.client() as client:
            a = await self._create_agent(client, "Agent A")
            now = time.time()
            self._insert(a, model="m1", inp=100, out=0, est=0.1, started_at=now - 3600)
            self._insert(a, model="m1", inp=999, out=0, est=0.9, started_at=now - 60 * 86400)

            recent = (await client.get("/xnobrain/api/runtime/v1/analytics/usage?days=30")).json()[
                "data"
            ]
            self.assertEqual(recent["totals"]["input_tokens"], 100)  # 60-day row excluded

            wide = (
                await client.get("/xnobrain/api/runtime/v1/analytics/usage?days=90&bucket=week")
            ).json()["data"]
            self.assertEqual(wide["totals"]["input_tokens"], 1099)
            self.assertTrue(all("T00:00:00" in row["bucket"] for row in wide["series"]))

            bad = await client.get(
                "/xnobrain/api/runtime/v1/analytics/usage?from=2099-01-01&to=2000-01-01"
            )
            self.assertEqual(bad.status_code, 400)

    async def test_24h_hour_route_uses_current_profile_history(self):
        now = datetime.now(timezone.utc).replace(minute=20, second=0, microsecond=0)

        async with self.client() as client:
            agent_id = await self._create_agent(client, "Hourly")
            self._insert(
                agent_id,
                model="gpt-5",
                inp=100,
                out=10,
                est=0.1,
                started_at=(now - timedelta(hours=2)).timestamp(),
            )
            self._insert(
                agent_id,
                model="gpt-5",
                inp=200,
                out=20,
                est=0.2,
                started_at=(now - timedelta(hours=1)).timestamp(),
            )
            response = await client.get(
                "/xnobrain/api/runtime/v1/analytics/usage?days=1&bucket=hour"
            )

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(data["bucket"], "hour")
        self.assertEqual(data["timezone"], "UTC")
        self.assertTrue(data["range_from"].endswith("Z"))
        self.assertEqual(data["source"]["kind"], "live_profiles")
        self.assertEqual(
            [row["bucket"] for row in data["series"] if row["total_tokens"]],
            [
                (now - timedelta(hours=2)).replace(minute=0).isoformat().replace("+00:00", "Z"),
                (now - timedelta(hours=1)).replace(minute=0).isoformat().replace("+00:00", "Z"),
            ],
        )

    async def test_weekly_budget_defaults_thresholds_and_clear(self):
        async with self.client() as client:
            a = await self._create_agent(client, "Agent A")
            self._insert(a, model="m1", inp=100, out=50, est=14.00)

            default = (
                await client.get(f"/xnobrain/api/runtime/v1/analytics/agents/{a}/budget")
            ).json()["data"]
            self.assertEqual(default["weekly_usd"], 20)
            self.assertFalse(default["configured"])
            self.assertEqual(default["severity"], "yellow")
            self.assertTrue(default["accepting_chats"])
            self.assertTrue(default["period_start"].endswith("Z"))
            self.assertTrue(default["period_end"].endswith("Z"))

            ok = (
                await client.put(
                    f"/xnobrain/api/runtime/v1/analytics/agents/{a}/budget",
                    json={"weekly_usd": 17.5},
                )
            ).json()["data"]
            self.assertEqual(ok["status"], "ok")
            self.assertEqual(ok["severity"], "orange")
            self.assertTrue(ok["configured"])
            self.assertAlmostEqual(ok["spend_usd"], 14.00, places=6)

            over = (
                await client.put(
                    f"/xnobrain/api/runtime/v1/analytics/agents/{a}/budget", json={"weekly_usd": 14}
                )
            ).json()["data"]
            self.assertEqual(over["status"], "exceeded")
            self.assertEqual(over["severity"], "red")
            self.assertFalse(over["accepting_chats"])

            # config.yaml carries the weekly block; a config snapshot was written
            config = yaml.safe_load((self.profiles / a / "config.yaml").read_text("utf-8"))
            self.assertIn("xnobrain_budget", config)

            cleared = (
                await client.put(
                    f"/xnobrain/api/runtime/v1/analytics/agents/{a}/budget",
                    json={"weekly_usd": None},
                )
            ).json()["data"]
            self.assertEqual(cleared["weekly_usd"], 20)
            self.assertFalse(cleared["configured"])
            config = yaml.safe_load((self.profiles / a / "config.yaml").read_text("utf-8"))
            self.assertNotIn("xnobrain_budget", config)

    async def test_weekly_and_conversation_costs_use_profile_ledger(self):
        async with self.client() as client:
            agent_id = await self._create_agent(client, "Cost Agent")
            session_id = self._insert(
                agent_id,
                model="ocz/deepseek-v4-flash",
                inp=100,
                out=10,
                est=0,
            )
            db = self.profiles / agent_id / "state.db"
            conn = sqlite3.connect(db)
            try:
                now = time.time()
                conn.execute(
                    "CREATE TABLE session_model_usage ("
                    "session_id TEXT, model TEXT, api_call_count INTEGER, "
                    "input_tokens INTEGER, output_tokens INTEGER, "
                    "cache_read_tokens INTEGER, cache_write_tokens INTEGER DEFAULT 0, "
                    "reasoning_tokens INTEGER, estimated_cost_usd REAL DEFAULT 0, "
                    "actual_cost_usd REAL DEFAULT 0, first_seen REAL, last_seen REAL)"
                )
                conn.execute(
                    "INSERT INTO session_model_usage "
                    "(session_id, model, api_call_count, input_tokens, output_tokens, "
                    "cache_read_tokens, reasoning_tokens, estimated_cost_usd, "
                    "first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (session_id, "ocz/deepseek-v4-flash", 2, 100, 10, 40, 5, 0.5, now - 60, now),
                )
                conn.commit()
            finally:
                conn.close()
            budget = await self.analytics.get_budget(agent_id)
            conversation_cost = await self.analytics.conversation_estimated_cost(
                agent_id,
                session_id,
            )

            self.assertEqual(budget["spend_usd"], 0.5)
            self.assertEqual(conversation_cost, 0.5)

    async def test_weekly_budget_minimum_and_chat_acceptance_gate(self):
        async with self.client() as client:
            a = await self._create_agent(client, "Agent A")
            invalid = await client.put(
                f"/xnobrain/api/runtime/v1/analytics/agents/{a}/budget",
                json={"weekly_usd": 0.99},
            )
            self.assertEqual(invalid.status_code, 422)

            self._insert(a, model="m1", inp=100, out=50, est=1.00)
            budget = await client.put(
                f"/xnobrain/api/runtime/v1/analytics/agents/{a}/budget",
                json={"weekly_usd": 1},
            )
            self.assertEqual(budget.status_code, 200, budget.text)
            session = await client.post(
                f"/xnobrain/api/runtime/v1/sessions?agent={a}",
                json={"title": "Budget gate"},
            )
            self.assertEqual(session.status_code, 201, session.text)
            session_id = session.json()["data"]["id"]
            usage = await client.get(
                f"/xnobrain/api/runtime/v1/sessions/{session_id}/usage?agent={a}"
            )
            self.assertEqual(usage.status_code, 200, usage.text)
            self.assertEqual(usage.json()["data"]["weekly_budget"]["weekly_usd"], 1)

            rejected = await client.post(
                f"/xnobrain/api/runtime/v1/sessions/{session_id}/runs?agent={a}",
                json={"input": "hello", "model": "auto"},
            )
            self.assertEqual(rejected.status_code, 429, rejected.text)
            self.assertEqual(rejected.json()["error"]["code"], "weekly_budget_exceeded")
            self.assertIn("Weekly budget reached", rejected.json()["message"])

    def test_week_starts_sunday_at_midnight_utc(self):
        value = datetime(2026, 8, 19, 18, 45, tzinfo=timezone.utc)  # Wednesday
        self.assertEqual(
            _sunday_start(value),
            datetime(2026, 8, 16, 0, 0, tzinfo=timezone.utc),
        )

    async def test_usage_is_read_only(self):
        async with self.client() as client:
            a = await self._create_agent(client, "Agent A")
            self._insert(a, model="m1", inp=100, out=50, est=0.10)
            db = self.profiles / a / "state.db"
            before = db.stat().st_mtime_ns
            await client.get("/xnobrain/api/runtime/v1/analytics/usage")
            await client.get(f"/xnobrain/api/runtime/v1/analytics/agents/{a}/usage")
            self.assertEqual(db.stat().st_mtime_ns, before)

    async def test_unknown_agent_is_404(self):
        async with self.client() as client:
            response = await client.get("/xnobrain/api/runtime/v1/analytics/agents/ghost/usage")
            self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()


class SkillUsageContractTests(unittest.TestCase):
    def test_historical_requests_do_not_claim_loads_or_runs(self):
        from xnobrain.integrations.analytics import skill_usage_profile

        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            conn = sqlite3.connect(profile / "state.db")
            conn.execute("CREATE TABLE sessions (id TEXT, started_at REAL)")
            conn.execute(
                "CREATE TABLE messages (session_id TEXT, role TEXT, tool_calls TEXT, timestamp REAL)"
            )
            now = time.time()
            conn.executemany(
                "INSERT INTO sessions VALUES (?,?)",
                [("run-a", now - 1000), ("run-b", now - 5)],
            )
            call = json.dumps(
                [{"function": {"name": "skill_view", "arguments": '{"name":"report-writer"}'}}]
            )
            conn.executemany(
                "INSERT INTO messages VALUES (?,?,?,?)",
                [
                    ("run-a", "assistant", call, now - 101),
                    ("run-b", "assistant", call, now + 2),
                    ("run-a", "assistant", call, now - 9),
                    ("run-a", "assistant", call, now - 8),
                    ("run-b", "assistant", call, now - 4),
                    (
                        "run-b",
                        "assistant",
                        json.dumps([{"function": {"name": "terminal", "arguments": "{}"}}]),
                        now - 3,
                    ),
                ],
            )
            conn.commit()
            conn.close()

            result = skill_usage_profile(profile, start_epoch=now - 100, end_epoch=now + 1)

        self.assertFalse(result["coverage"]["instrumented"])
        self.assertEqual(result["coverage"]["attribution"], "historical_requests")
        self.assertEqual(result["items"][0]["requested_count"], 3)
        self.assertIsNone(result["items"][0]["loaded_count"])
        self.assertIsNone(result["items"][0]["distinct_runs"])
        self.assertEqual(result["items"][0]["distinct_sessions"], 2)
        self.assertIsNone(result["items"][0]["tool_invocations"])
        self.assertIsNone(result["items"][0]["errors"])

    def test_missing_instrumentation_reports_unknown_not_zero(self):
        from xnobrain.integrations.analytics import skill_usage_profile

        with tempfile.TemporaryDirectory() as directory:
            result = skill_usage_profile(Path(directory), start_epoch=0, end_epoch=time.time())
        self.assertFalse(result["coverage"]["instrumented"])
        self.assertEqual(result["coverage"]["message"], "No measured data")
