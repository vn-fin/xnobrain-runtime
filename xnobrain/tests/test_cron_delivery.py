"""Cron blueprint, target, and execution-history contract tests."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from xnobrain.app import XNOBrainApplication
from xnobrain.integrations import AgentManager, GlobalConfigManager

try:
    import cron.blueprint_catalog  # type: ignore  # noqa: F401
except Exception:  # pragma: no cover - source-only checkout
    HERMES_AVAILABLE = False
else:
    HERMES_AVAILABLE = True


@unittest.skipUnless(HERMES_AVAILABLE, "Hermes runtime is not installed")
class CronDeliveryCompatibilityTests(unittest.TestCase):
    def test_pinned_hermes_cron_contract(self):
        from cron.blueprint_catalog import (  # type: ignore
            CATALOG,
            BlueprintFillError,
            blueprint_catalog_entry,
            blueprint_form_schema,
            fill_blueprint,
            get_blueprint,
        )
        from cron.scheduler import cron_delivery_targets  # type: ignore
        from gateway.config import Platform  # type: ignore
        from gateway.delivery import DeliveryRouter, DeliveryTarget  # type: ignore

        self.assertTrue(CATALOG)
        blueprint = CATALOG[0]
        entry = blueprint_catalog_entry(blueprint)
        self.assertTrue({
            "key", "title", "description", "category", "tags", "fields",
            "schedule", "scheduleHuman", "command", "appUrl",
        }.issubset(entry))
        self.assertIsInstance(blueprint_form_schema(blueprint), dict)
        values = {field["name"]: field.get("default") for field in entry["fields"]}
        filled = fill_blueprint(blueprint, values)
        self.assertTrue({"prompt", "schedule", "name", "deliver"}.issubset(filled))
        self.assertIs(get_blueprint(entry["key"]), blueprint)
        self.assertTrue(issubclass(BlueprintFillError, Exception))
        targets = cron_delivery_targets()
        self.assertIsInstance(targets, list)
        for target in targets:
            self.assertTrue({"id", "name", "home_target_set", "home_env_var"}.issubset(target))
        self.assertIsNotNone(DeliveryRouter)
        self.assertIsNotNone(DeliveryTarget)
        self.assertEqual(Platform.EMAIL.value, "email")
        self.assertEqual(Platform.LOCAL.value, "local")


class _Router:
    async def list_connections(self):
        return {"connections": []}

    async def list_models(self):
        return {"data": []}


@unittest.skipUnless(HERMES_AVAILABLE, "Hermes runtime is not installed")
class CronDeliveryAPITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "root"
        self.profiles = Path(self.temp.name) / "profiles"
        self.root.mkdir()
        self.profiles.mkdir()
        self.env = patch.dict(os.environ, {
            "HERMES_HOME": str(self.root),
            "HERMES_ROOT_PROFILE": str(self.root),
            "HERMES_PROFILES_ROOT": str(self.profiles),
            "DATA_DIR": self.temp.name,
        })
        self.env.start()
        self.app = FastAPI()
        self.composition = XNOBrainApplication(
            AgentManager(root_profile=self.root, profiles_root=self.profiles, legacy_agents_root=Path(self.temp.name) / "legacy"),
            GlobalConfigManager(root_profile=self.root),
            _Router(),
        )
        self.composition.register(self.app)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    async def test_cron_scan_does_not_block_unrelated_requests(self):
        started = threading.Event()

        def slow_list(*_args) -> list[dict]:
            started.set()
            time.sleep(0.2)
            return []

        async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
            with patch.object(self.composition.service, "list_crons", side_effect=slow_list):
                cron_request = asyncio.create_task(client.get("/xnobrain/api/runtime/v1/cron/jobs"))
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                before = asyncio.get_running_loop().time()
                providers = await client.get("/xnobrain/api/runtime/v1/providers")
                elapsed = asyncio.get_running_loop().time() - before
                cron = await cron_request

        self.assertEqual(cron.status_code, 200, cron.text)
        self.assertEqual(providers.status_code, 200, providers.text)
        self.assertLess(elapsed, 0.1)

    def test_cron_profile_discovery_skips_full_agent_dtos(self):
        with patch.object(
            self.composition.service.agents,
            "list_agents",
            side_effect=AssertionError("cron discovery must not scan agent DTOs"),
        ):
            self.assertEqual(self.composition.service.cron.list_jobs(), [])

    async def test_blueprints_targets_and_runs_routes(self):
        async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
            created = await client.post("/xnobrain/api/runtime/v1/agents", json={"name": "Automation"})
            agent_id = created.json()["data"]["id"]
            blueprints = await client.get("/xnobrain/api/runtime/v1/cron/blueprints")
            self.assertEqual(blueprints.status_code, 200, blueprints.text)
            self.assertTrue(blueprints.json()["data"]["blueprints"])

            cron = await client.post("/xnobrain/api/runtime/v1/cron/jobs", json={
                "agent_id": agent_id,
                "name": "Daily file",
                "prompt": "Write a short report",
                "interval_minutes": 60,
            })
            self.assertEqual(cron.status_code, 201, cron.text)
            job_id = cron.json()["data"]["id"]
            scoped = await client.get(f"/xnobrain/api/runtime/v1/cron/jobs?agent_id={agent_id}")
            self.assertEqual([item["id"] for item in scoped.json()["data"]], [job_id])
            with patch.object(
                self.composition.service.cron,
                "_profiles",
                side_effect=AssertionError("scoped detail must not enumerate every profile"),
            ):
                scoped_detail = await client.get(
                    f"/xnobrain/api/runtime/v1/cron/jobs/{job_id}?agent_id={agent_id}"
                )
            self.assertEqual(scoped_detail.status_code, 200, scoped_detail.text)
            added = await client.post(f"/xnobrain/api/runtime/v1/cron/jobs/{job_id}/delivery-targets", json={
                "target_type": "file", "destination": "reports/daily.md",
            })
            self.assertEqual(added.status_code, 201, added.text)
            self.assertEqual(added.json()["data"]["target_type"], "file")
            profile = self.profiles / agent_id
            self.assertTrue(any((profile / "snapshots" / "cron" / "jobs").glob("*.json")))

            now = datetime.now(timezone.utc).isoformat()
            executions = profile / "cron" / "executions.db"
            conn = sqlite3.connect(executions)
            try:
                conn.execute(
                    "CREATE TABLE executions (id TEXT PRIMARY KEY, job_id TEXT, status TEXT, claimed_at TEXT, started_at TEXT, finished_at TEXT, error TEXT)"
                )
                conn.execute(
                    "INSERT INTO executions VALUES (?, ?, 'completed', ?, ?, ?, NULL)",
                    ("execution-1", job_id, now, now, now),
                )
                conn.commit()
            finally:
                conn.close()
            output_dir = profile / "cron" / "output" / job_id
            output_dir.mkdir(parents=True)
            (output_dir / "run.md").write_text("# Cron\n\n## Response\nDaily report ready", encoding="utf-8")
            self.composition.service.cron.reconcile_deliveries()
            self.assertEqual((profile / "workspace" / "reports" / "daily.md").read_text(encoding="utf-8"), "Daily report ready")
            self.composition.service.cron.reconcile_deliveries()
            options = await client.get(f"/xnobrain/api/runtime/v1/cron/delivery-targets?agent_id={agent_id}")
            self.assertEqual(options.status_code, 200, options.text)
            self.assertTrue(any(item["target_type"] == "kanban" for item in options.json()["data"]["options"]))
            runs = await client.get(f"/xnobrain/api/runtime/v1/cron/jobs/{job_id}/runs")
            self.assertEqual(runs.status_code, 200, runs.text)
            self.assertEqual(runs.json()["data"]["runs"][0]["deliveries"][0]["status"], "delivered")
            self.assertEqual(len(runs.json()["data"]["runs"][0]["deliveries"]), 1)
            self.assertNotIn("output", runs.json()["data"]["runs"][0])
            self.assertNotIn(str(profile), runs.text)

            invalid_limit = await client.get(f"/xnobrain/api/runtime/v1/cron/jobs/{job_id}/runs?limit=nope")
            self.assertEqual(invalid_limit.status_code, 400, invalid_limit.text)

            traversal = await client.post(f"/xnobrain/api/runtime/v1/cron/jobs/{job_id}/delivery-targets", json={
                "target_type": "file", "destination": "../outside.md",
            })
            self.assertIn(traversal.status_code, {400, 422}, traversal.text)

            email = await client.post(f"/xnobrain/api/runtime/v1/cron/jobs/{job_id}/delivery-targets", json={
                "target_type": "email", "destination": "owner@example.test",
            })
            self.assertEqual(email.status_code, 422, email.text)
            self.assertEqual(email.json()["error"]["code"], "delivery_target_unavailable")
            detail = await client.get(f"/xnobrain/api/runtime/v1/cron/jobs/{job_id}")
            self.assertEqual(detail.status_code, 200, detail.text)
            self.assertNotIn("email:owner@example.test", detail.json()["data"]["job"]["deliver"])

            kanban = await client.post(f"/xnobrain/api/runtime/v1/cron/jobs/{job_id}/delivery-targets", json={
                "target_type": "kanban", "destination": "default",
            })
            self.assertEqual(kanban.status_code, 201, kanban.text)
            self.composition.service.cron.reconcile_deliveries()
            self.composition.service.cron.reconcile_deliveries()
            tasks = await client.get("/xnobrain/api/runtime/v1/kanban/boards/default/tasks")
            self.assertEqual(tasks.status_code, 200, tasks.text)
            delivered = [item for item in tasks.json()["data"]["tasks"] if item["title"] == "Daily file result"]
            self.assertEqual(len(delivered), 1)

            invalid = await client.post(f"/xnobrain/api/runtime/v1/cron/jobs/{job_id}/delivery-targets", json={
                "target_type": "webhook", "destination": "https://example.test",
            })
            self.assertEqual(invalid.status_code, 422, invalid.text)

    async def test_output_artifacts_reconcile_without_executions_database(self):
        async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
            created = await client.post("/xnobrain/api/runtime/v1/agents", json={"name": "Artifact Cron"})
            agent_id = created.json()["data"]["id"]
            cron = await client.post("/xnobrain/api/runtime/v1/cron/jobs", json={
                "agent_id": agent_id,
                "name": "Artifact delivery",
                "prompt": "Write a report",
                "interval_minutes": 60,
            })
            job_id = cron.json()["data"]["id"]
            target = await client.post(
                f"/xnobrain/api/runtime/v1/cron/jobs/{job_id}/delivery-targets?agent_id={agent_id}",
                json={"target_type": "file", "destination": "reports/artifact.md"},
            )
            self.assertEqual(target.status_code, 201, target.text)
            target_id = target.json()["data"]["id"]

            profile = self.profiles / agent_id
            output_dir = profile / "cron" / "output" / job_id
            output_dir.mkdir(parents=True)
            (output_dir / "2026-08-14_12-00-00.md").write_text(
                "# Cron\n\n## Response\nArtifact-backed result", encoding="utf-8"
            )
            executions_db = profile / "cron" / "executions.db"
            executions_db.unlink(missing_ok=True)
            self.assertFalse(executions_db.exists())

            detail = await client.get(
                f"/xnobrain/api/runtime/v1/cron/jobs/{job_id}?agent_id={agent_id}"
            )
            self.assertEqual(detail.status_code, 200, detail.text)
            self.assertEqual(
                (profile / "workspace" / "reports" / "artifact.md").read_text(encoding="utf-8"),
                "Artifact-backed result",
            )
            run = detail.json()["data"]["runs"][0]
            self.assertEqual(run["state"], "success")
            self.assertEqual(run["deliveries"], [{
                "target_id": target_id,
                "target_type": "file",
                "status": "delivered",
                "at": run["deliveries"][0]["at"],
                "reason": None,
            }])

    async def test_delete_snapshots_output_before_native_cleanup(self):
        async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
            created = await client.post("/xnobrain/api/runtime/v1/agents", json={"name": "Archived Cron"})
            agent_id = created.json()["data"]["id"]
            cron = await client.post("/xnobrain/api/runtime/v1/cron/jobs", json={
                "agent_id": agent_id,
                "name": "Archive before delete",
                "prompt": "Write a report",
                "interval_minutes": 60,
            })
            job_id = cron.json()["data"]["id"]
            profile = self.profiles / agent_id
            output_dir = profile / "cron" / "output" / job_id
            output_dir.mkdir(parents=True)
            (output_dir / "run.md").write_text("retained output", encoding="utf-8")

            deleted = await client.delete(
                f"/xnobrain/api/runtime/v1/cron/jobs/{job_id}?agent_id={agent_id}"
            )
            self.assertEqual(deleted.status_code, 200, deleted.text)
            self.assertFalse(output_dir.exists())
            snapshots = list((profile / "snapshots" / "cron" / "output" / job_id).glob("*/run.md"))
            self.assertEqual(len(snapshots), 1)
            self.assertEqual(snapshots[0].read_text(encoding="utf-8"), "retained output")

    async def test_run_now_dispatches_the_selected_agent_profile(self):
        with patch.object(self.composition.service.cron, "fire_due", return_value=True) as fire_due:
            async with self.app.router.lifespan_context(self.app):
                async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
                    created = await client.post("/xnobrain/api/runtime/v1/agents", json={"name": "Profile Cron"})
                    agent_id = created.json()["data"]["id"]
                    cron = await client.post("/xnobrain/api/runtime/v1/cron/jobs", json={
                        "agent_id": agent_id,
                        "name": "Profile run now",
                        "prompt": "Run in this profile",
                        "interval_minutes": 60,
                    })
                    job_id = cron.json()["data"]["id"]
                    triggered = await client.post(f"/xnobrain/api/runtime/v1/cron/jobs/{job_id}/run")
                    self.assertEqual(triggered.status_code, 200, triggered.text)

                    for _ in range(30):
                        if fire_due.called:
                            break
                        await asyncio.sleep(0.1)

        fire_due.assert_called_once_with(agent_id, job_id)

    async def test_instantiates_real_blueprint_defaults(self):
        async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
            created = await client.post("/xnobrain/api/runtime/v1/agents", json={"name": "Blueprint Owner"})
            agent_id = created.json()["data"]["id"]
            response = await client.post("/xnobrain/api/runtime/v1/cron/blueprints/instantiate", json={
                "blueprint": "morning-brief",
                "agent_id": agent_id,
                "values": {},
            })
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(response.json()["data"]["agent_id"], agent_id)
            self.assertEqual(response.json()["data"]["schedule"], "0 8 * * *")
            job_id = response.json()["data"]["id"]
            detail = await client.get(f"/xnobrain/api/runtime/v1/cron/jobs/{job_id}")
            self.assertEqual(detail.status_code, 200, detail.text)
            self.assertEqual(detail.json()["data"]["job"]["id"], job_id)
