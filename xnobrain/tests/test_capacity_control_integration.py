"""Real Control HTTP/PostgreSQL with synthetic telemetry and a deterministic agent."""

import asyncio
import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from xnobrain.integrations.run_admission import AdmissionClient
from xnobrain.services.run_admission import RunAdmissionService
from xnobrain.tests import test_conversation_runs as fixtures


@unittest.skipUnless(os.getenv("RUN_ADMISSION_HTTP_URL"), "Control test fixture not configured")
class ControlIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_pressure_existing_run_cancel_and_exact_boundary_recovery(self):
        await fixtures.ConversationRunServiceTests.asyncSetUp(self)
        url = os.environ["RUN_ADMISSION_HTTP_URL"]
        async with httpx.AsyncClient(base_url=url) as http:
            identity = (await http.get("/test/identity")).json()
            with patch.dict(
                os.environ,
                {
                    "RUNTIME_CONTROL_URL": url,
                    "RUNTIME_WORKSPACE_ID": "w1",
                    "RUNTIME_INTERNAL_SERVICE_TOKEN": identity["token"],
                    "DATA_DIR": str(self.repository.data_dir),
                },
            ):
                client = AdmissionClient()
                capacity = RunAdmissionService(
                    SimpleNamespace(
                        conversation_runs=self.service,
                        repository=self.repository,
                        agents=self.agents,
                        runtime_updates=SimpleNamespace(require_dispatch=lambda: None),
                    ),
                    client,
                )
                self.service.capacity = capacity

                async def sample(sequence, available, cpu_busy=50):
                    observed = datetime.now(timezone.utc)
                    response = await http.post(
                        "/test/sample",
                        json={
                            "sequence": sequence,
                            "total_bytes": 1200,
                            "available_bytes": available,
                            "observed_at": observed.isoformat(),
                            "cpu_cores": 20,
                            "cpu_busy_percent": cpu_busy,
                            "cpu_window_started_at": (observed - timedelta(seconds=5)).isoformat(),
                        },
                    )
                    self.assertEqual(response.status_code, 204)

                try:
                    await sample(1, 600)
                    first = await self.service.start_run(
                        "agent-one", "session-one", {"input": "synthetic A"}
                    )
                    await capacity._tick(capacity.store.pending()[0])
                    await asyncio.sleep(0.1)
                    self.assertIn(first["id"], self.service._active)
                    await asyncio.sleep(7.2)
                    await sample(2, 479)
                    second = await self.service.start_run(
                        "agent-one", "session-two", {"input": "synthetic B"}
                    )
                    await capacity._tick(
                        next(row for row in capacity.store.pending() if row["id"] == second["id"])
                    )
                    self.assertEqual(
                        self.service.get_run("agent-one", "session-two", second["id"])["capacity"][
                            "reason_code"
                        ],
                        "pool_memory_pressure",
                    )
                    await sample(3, 600, 60.1)
                    await capacity._tick(
                        next(row for row in capacity.store.pending() if row["id"] == second["id"])
                    )
                    self.assertEqual(
                        self.service.get_run("agent-one", "session-two", second["id"])["capacity"][
                            "reason_code"
                        ],
                        "pool_cpu_pressure",
                    )
                    self.assertNotIn(second["id"], self.service._active)
                    self.assertIn(first["id"], self.service._active)
                    cancelled = await self.service.start_run(
                        "agent-one", "session-three", {"input": "synthetic C"}
                    )
                    await self.service.cancel_run("agent-one", "session-three", cancelled["id"])
                    await capacity._tick(
                        next(
                            row for row in capacity.store.pending() if row["id"] == cancelled["id"]
                        )
                    )
                    await asyncio.sleep(7.2)
                    await sample(4, 480, 60)
                    await capacity._tick(
                        next(row for row in capacity.store.pending() if row["id"] == second["id"])
                    )
                    await asyncio.sleep(0.1)
                    self.assertIn(second["id"], self.service._active)
                    self.assertNotIn(cancelled["id"], self.service._active)
                    self.agents.release.set()
                    await asyncio.sleep(0.1)
                    self.assertEqual(
                        self.service.get_run("agent-one", "session-two", second["id"])["status"],
                        "completed",
                    )
                    for row in capacity.store.pending():
                        await capacity._tick(row)
                finally:
                    await capacity.close()
                    await fixtures.ConversationRunServiceTests.asyncTearDown(self)
                    await http.post("/test/done")
