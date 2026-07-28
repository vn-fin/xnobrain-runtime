"""Contract and unit tests for usage analytics and advisory budgets."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import yaml

from brain4all.app import Brain4AllApplication
from brain4all.integrations import AgentManager, GlobalConfigManager
from brain4all.integrations.analytics import (
    aggregate_profile,
    aggregate_router_usage,
    period_spend,
)


class FakeRouter:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    async def list_connections(self):
        return {"connections": []}

    async def list_models(self):
        return {"data": []}

    async def usage(self, _model):
        return {"available": False, "provider": "", "model": "auto",
                "plan": "", "message": "", "quotas": []}


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
        self.router_data = base / "nine-router"
        self.root.mkdir(parents=True)
        self.profiles.mkdir(parents=True)
        (self.root / "config.yaml").write_text(yaml.safe_dump({
            "model": {"provider": "custom:nine-router", "default": "auto"},
            "providers": {}, "agent": {"reasoning_effort": "medium"},
            "approvals": {"mode": "manual"}, "terminal": {"backend": "local"},
        }), encoding="utf-8")
        from unittest.mock import patch
        self.environment = patch.dict(os.environ, {
            "HERMES_HOME": str(self.root), "HERMES_ROOT_PROFILE": str(self.root),
            "HERMES_PROFILES_ROOT": str(self.profiles), "DATA_DIR": self.temporary.name,
        })
        self.environment.start()
        app = FastAPI()
        composition = Brain4AllApplication(
            AgentManager(root_profile=self.root, profiles_root=self.profiles,
                         legacy_agents_root=base / "legacy-agents"),
            GlobalConfigManager(root_profile=self.root), FakeRouter(self.router_data),
        )
        composition.register(app)
        self.app = app

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def client(self):
        return AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test")

    async def _create_agent(self, client, display_name):
        response = await client.post(
            "/agent-gateway/v1/agents", json={"display_name": display_name})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["data"]["id"]

    def _insert(self, agent_id, *, model, inp, out, est, act=0.0,
                started_at=None, provider="anthropic"):
        db = self.profiles / agent_id / "state.db"
        conn = sqlite3.connect(db)
        try:
            conn.execute(
                f"INSERT INTO sessions ({_SESSION_COLUMNS}) "
                f"VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, "api", model, provider,
                 started_at if started_at is not None else time.time() - 3600,
                 inp, out, 0, 0, 0, est, act, 1),
            )
            conn.commit()
        finally:
            conn.close()

    def _insert_router(
        self,
        *,
        model: str,
        inp: int,
        out: int,
        cost: float,
        provider: str = "codex",
        status: str = "success",
        timestamp: str | None = None,
    ):
        db_dir = self.router_data / "db"
        db_dir.mkdir(parents=True, exist_ok=True)
        db = db_dir / "data.sqlite"
        conn = sqlite3.connect(db)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS usageHistory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT, provider TEXT, model TEXT,
                    connectionId TEXT, apiKey TEXT, endpoint TEXT,
                    promptTokens INTEGER, completionTokens INTEGER,
                    cost REAL, status TEXT, tokens TEXT, meta TEXT
                )
                """
            )
            stamp = timestamp or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            conn.execute(
                """
                INSERT INTO usageHistory (
                    timestamp, provider, model, connectionId, apiKey, endpoint,
                    promptTokens, completionTokens, cost, status, tokens, meta
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    stamp, provider, model, "", "", "/v1/chat/completions",
                    inp, out, cost, status, "{}", "{}",
                ),
            )
            conn.commit()
        finally:
            conn.close()

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
            "estimated_cost_usd REAL, actual_cost_usd REAL, api_call_count INTEGER)")
        now = time.time()
        rows = [
            (uuid.uuid4().hex, "api", "m1", "anthropic", now - 3600, 100, 50, 0, 0, 0, 0.10, 0.0, 1),
            (uuid.uuid4().hex, "api", "m1", "anthropic", now - 7200, 40, 20, 0, 0, 0, 0.04, 0.0, 1),
            (uuid.uuid4().hex, "api", "m2", "openai", now - 90 * 86400, 999, 999, 0, 0, 0, 9.99, 0.0, 1),
        ]
        conn.executemany(f"INSERT INTO sessions ({_SESSION_COLUMNS}) VALUES ({','.join('?'*13)})", rows)
        conn.commit(); conn.close()

        out = aggregate_profile(profile, start_epoch=now - 30 * 86400, end_epoch=now, bucket="day")
        self.assertEqual(out["totals"]["input_tokens"], 140)   # 100 + 40, old row excluded
        self.assertEqual(out["totals"]["output_tokens"], 70)
        self.assertEqual(out["totals"]["sessions"], 2)
        self.assertAlmostEqual(out["totals"]["estimated_cost_usd"], 0.14, places=6)
        self.assertEqual({row["model"] for row in out["by_model"]}, {"m1"})

        spend = period_spend(profile, since_epoch=now - 30 * 86400, cost_basis="estimated")
        self.assertAlmostEqual(spend, 0.14, places=6)

    def test_aggregate_router_usage_models_providers_and_status(self):
        self._insert_router(model="gpt-5", inp=200, out=50, cost=0.25)
        self._insert_router(
            model="claude", provider="claude", inp=100, out=20,
            cost=0.10, status="error",
        )
        result = aggregate_router_usage(
            self.router_data,
            start_epoch=time.time() - 86400,
            end_epoch=time.time() + 1,
            bucket="day",
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["totals"]["input_tokens"], 300)
        self.assertEqual(result["totals"]["output_tokens"], 70)
        self.assertEqual(result["totals"]["api_calls"], 2)
        self.assertEqual({row["model"] for row in result["by_model"]}, {"gpt-5", "claude"})
        self.assertEqual(
            {row["provider"] for row in result["by_provider"]}, {"codex", "claude"})
        self.assertEqual(result["request_status"]["successful"], 1)
        self.assertEqual(result["request_status"]["failed"], 1)

    # ---- integration: routes end-to-end ------------------------------------

    async def test_agents_list_and_cross_agent_totals(self):
        async with self.client() as client:
            a = await self._create_agent(client, "Agent A")
            b = await self._create_agent(client, "Agent B")
            self._insert(a, model="m1", inp=100, out=50, est=0.10)
            self._insert(b, model="m2", inp=200, out=100, est=0.20)

            agents = (await client.get("/agent-gateway/v1/analytics/agents")).json()["data"]["agents"]
            self.assertEqual(
                {row["agent_id"] for row in agents},
                {"big-brother", a, b},
            )

            data = (await client.get("/agent-gateway/v1/analytics/usage")).json()["data"]
            self.assertEqual(data["totals"]["input_tokens"], 300)
            self.assertEqual(data["totals"]["output_tokens"], 150)
            self.assertEqual(data["totals"]["total_tokens"], 450)
            self.assertEqual(data["totals"]["sessions"], 2)
            self.assertAlmostEqual(data["totals"]["cost_usd"], 0.30, places=6)
            self.assertEqual(data["totals"]["cost_basis"], "estimated")
            self.assertEqual({row["model"] for row in data["by_model"]}, {"m1", "m2"})
            self.assertEqual(len(data["agents"]), 3)
            self.assertEqual(data["agents_available"], 3)

    async def test_parallel_dashboard_endpoints_share_one_computation(self):
        async with self.client() as client:
            await self._create_agent(client, "Agent A")
            self._insert_router(model="gpt-5", inp=200, out=50, cost=0.25)
            query = "?from=2026-07-01&to=2026-08-01&bucket=hour"
            with patch(
                "brain4all.services.analytics.aggregate_router_usage",
                wraps=aggregate_router_usage,
            ) as aggregate:
                overview_response, models_response, series_response = await asyncio.gather(
                    client.get(f"/agent-gateway/v1/analytics/overview{query}"),
                    client.get(f"/agent-gateway/v1/analytics/models{query}"),
                    client.get(f"/agent-gateway/v1/analytics/timeseries{query}"),
                )

            self.assertEqual(overview_response.status_code, 200)
            self.assertEqual(models_response.status_code, 200)
            self.assertEqual(series_response.status_code, 200)
            overview = overview_response.json()["data"]
            models = models_response.json()["data"]
            series = series_response.json()["data"]
            self.assertNotIn("series", overview)
            self.assertNotIn("by_model", overview)
            self.assertEqual(models["by_model"][0]["model"], "gpt-5")
            self.assertEqual(models["by_provider"][0]["provider"], "codex")
            self.assertEqual(series["bucket"], "hour")
            self.assertEqual(aggregate.call_count, 1)

    async def test_agent_multiselect_scopes_totals_and_reads(self):
        async with self.client() as client:
            a = await self._create_agent(client, "Agent A")
            b = await self._create_agent(client, "Agent B")
            self._insert(a, model="m1", inp=100, out=50, est=0.10)
            self._insert(b, model="m2", inp=200, out=100, est=0.20)

            data = (await client.get(f"/agent-gateway/v1/analytics/usage?agents={a}")).json()["data"]
            self.assertEqual(data["totals"]["input_tokens"], 100)
            self.assertEqual(data["totals"]["sessions"], 1)
            self.assertEqual(data["agents_selected"], [a])
            self.assertEqual(len(data["agents"]), 1)
            # subset totals equal that agent's per-agent totals alone
            self.assertEqual(data["agents"][0]["agent_id"], a)
            self.assertEqual(data["agents"][0]["totals"]["input_tokens"], 100)

    async def test_workspace_usage_survives_agent_deletion(self):
        async with self.client() as client:
            deleted = await self._create_agent(client, "Disposable")
            kept = await self._create_agent(client, "Kept")
            self._insert(deleted, model="gpt-5", inp=100, out=20, est=0.10)
            self._insert_router(model="gpt-5", inp=500, out=80, cost=0.42)

            before = (await client.get(
                "/agent-gateway/v1/analytics/usage?days=30"
            )).json()["data"]
            self.assertEqual(before["totals"]["total_tokens"], 580)
            self.assertEqual(before["source"]["kind"], "nine_router")
            self.assertTrue(before["source"]["durable"])

            removed = await client.delete(f"/agent-gateway/v1/agents/{deleted}/delete")
            self.assertEqual(removed.status_code, 200, removed.text)

            after = (await client.get(
                "/agent-gateway/v1/analytics/usage?days=30"
            )).json()["data"]
            self.assertEqual(after["totals"]["total_tokens"], 580)
            self.assertEqual(after["totals"]["cost_usd"], 0.42)
            self.assertEqual(
                {row["agent_id"] for row in after["agents"]},
                {"big-brother", kept},
            )
            self.assertTrue(after["attribution"]["deleted_usage_included"])

    async def test_time_range_and_bucket(self):
        async with self.client() as client:
            a = await self._create_agent(client, "Agent A")
            now = time.time()
            self._insert(a, model="m1", inp=100, out=0, est=0.1, started_at=now - 3600)
            self._insert(a, model="m1", inp=999, out=0, est=0.9, started_at=now - 60 * 86400)

            recent = (await client.get("/agent-gateway/v1/analytics/usage?days=30")).json()["data"]
            self.assertEqual(recent["totals"]["input_tokens"], 100)  # 60-day row excluded

            wide = (await client.get("/agent-gateway/v1/analytics/usage?days=90&bucket=week")).json()["data"]
            self.assertEqual(wide["totals"]["input_tokens"], 1099)
            self.assertTrue(all("-W" in row["bucket"] for row in wide["series"]))

            bad = await client.get("/agent-gateway/v1/analytics/usage?from=2099-01-01&to=2000-01-01")
            self.assertEqual(bad.status_code, 400)

    async def test_budget_advisory_set_get_and_clear(self):
        async with self.client() as client:
            a = await self._create_agent(client, "Agent A")
            self._insert(a, model="m1", inp=100, out=50, est=0.30)

            ok = (await client.put(
                f"/agent-gateway/v1/analytics/agents/{a}/budget",
                json={"monthly_usd": 1000})).json()["data"]
            self.assertEqual(ok["status"], "ok")
            self.assertTrue(ok["advisory"])
            self.assertAlmostEqual(ok["spend_usd"], 0.30, places=6)

            over = (await client.put(
                f"/agent-gateway/v1/analytics/agents/{a}/budget",
                json={"monthly_usd": 0.01})).json()["data"]
            self.assertEqual(over["status"], "exceeded")

            # config.yaml carries the advisory block; a config snapshot was written
            config = yaml.safe_load((self.profiles / a / "config.yaml").read_text("utf-8"))
            self.assertIn("brain4all_budget", config)

            cleared = (await client.put(
                f"/agent-gateway/v1/analytics/agents/{a}/budget",
                json={"monthly_usd": None})).json()["data"]
            self.assertEqual(cleared["status"], "unset")
            config = yaml.safe_load((self.profiles / a / "config.yaml").read_text("utf-8"))
            self.assertNotIn("brain4all_budget", config)

    async def test_usage_is_read_only(self):
        async with self.client() as client:
            a = await self._create_agent(client, "Agent A")
            self._insert(a, model="m1", inp=100, out=50, est=0.10)
            db = self.profiles / a / "state.db"
            before = db.stat().st_mtime_ns
            await client.get("/agent-gateway/v1/analytics/usage")
            await client.get(f"/agent-gateway/v1/analytics/agents/{a}/usage")
            self.assertEqual(db.stat().st_mtime_ns, before)

    async def test_unknown_agent_is_404(self):
        async with self.client() as client:
            response = await client.get("/agent-gateway/v1/analytics/agents/ghost/usage")
            self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
