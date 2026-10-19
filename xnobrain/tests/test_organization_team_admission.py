"""Managed org coordinators wait for admission without persisting capabilities."""

import asyncio
import os
from unittest.mock import AsyncMock, patch

from xnobrain.integrations.organization_team_executions import OrganizationTeamExecutionClient
from xnobrain.integrations.run_admission import current_admission, require_admitted
from xnobrain.repositories.organization_team_executions import OrganizationTeamExecutionRepository
from xnobrain.services.base import ServiceError
from xnobrain.services.organization_team_executions import OrganizationTeamExecutions
from xnobrain.services.run_admission import RunAdmissionService
from xnobrain.tests import test_organization_team_executions as organization_fixtures
from xnobrain.tests.test_team_runs import _TeamRunBase


class OrganizationTeamAdmissionTests(_TeamRunBase):
    signed = organization_fixtures.OrganizationExecutionTests.signed
    payload = organization_fixtures.OrganizationExecutionTests.payload
    path = "/xnobrain/api/runtime/v1/organization-team-executions/org_test/atm_test/atr_test"

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.platform = self.composition.service
        self.admissions = AsyncMock()
        self.wait = {"id": "admission-test", "state": "waiting", "reason_code": "pool_cpu_pressure"}
        self.admissions.register.return_value = self.wait
        self.admissions.observe.return_value = self.wait
        self.admissions.claim.return_value = {**self.wait, "state": "starting", "nonce": "test"}
        self.capacity = RunAdmissionService(self.platform, self.admissions)
        self.platform.run_admission = self.capacity
        self.repo = OrganizationTeamExecutionRepository(self.platform.repository, "org_test")
        self.lineages = []

        async def coordinator(*_args):
            require_admitted()
            self.lineages.append(current_admission()["id"])
            return {"response": "Synthetic coordinator output"}

        self.local = AsyncMock(side_effect=coordinator)
        self.remote = AsyncMock(return_value={"response": "Synthetic worker output"})
        for context in (
            patch.dict(
                os.environ,
                {
                    "RUNTIME_WORKSPACE_ID": "workspace-test",
                    "RUNTIME_INTERNAL_SERVICE_TOKEN": "private-test",
                    "RUNTIME_CONTROL_URL": "http://control",
                },
            ),
            patch.object(self.platform.analytics, "require_execution_budget", new=AsyncMock()),
            patch.object(self.platform.agents, "chat", new=self.local),
            patch.object(OrganizationTeamExecutionClient, "chat", new=self.remote),
            patch.object(OrganizationTeamExecutionClient, "record", new=AsyncMock()),
        ):
            context.start()
            self.addCleanup(context.stop)

    async def asyncTearDown(self):
        await self.platform.organization_team_executions.shutdown()
        await self.capacity.close()
        await super().asyncTearDown()

    async def request(self, method):
        payload = self.payload() if method == "POST" else None
        async with self.client() as client:
            response = await client.request(
                method, self.path, **self.signed(method, self.path, payload)
            )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]

    async def tick(self):
        await self.capacity._tick(self.capacity.store.pending()[0])

    def offer(self):
        offered = {**self.wait, "state": "offered", "nonce": "test"}
        self.admissions.register.return_value = offered
        self.admissions.observe.return_value = offered

    def record(self):
        return self.repo.get_team_run("atm_test", "atr_test")

    async def finish(self):
        engine = self.platform.organization_team_executions.executions[
            ("org_test", "atm_test", "atr_test")
        ]
        entry = engine.registry_entry("atr_test")
        await asyncio.wait_for(entry.task, 3)
        await self.tick()

    async def test_wait_then_admit_runs_coordinator_and_synthesis_once(self):
        run = await self.request("POST")
        self.assertEqual(run["status"], "pending")
        self.assertEqual(run["capacity"]["state"], "waiting")
        self.assertIsNone(run["started_at"])
        await self.tick()
        self.assertEqual(self.record()["capacity"]["reason_code"], "pool_cpu_pressure")
        self.local.assert_not_awaited()
        self.remote.assert_not_awaited()
        self.assertFalse(self.capacity.store.team_active("atr_test"))
        self.assertEqual((await self.request("GET"))["status"], "pending")
        self.assertEqual((await self.request("POST"))["id"], run["id"])
        self.offer()
        await self.tick()
        await self.finish()
        self.assertEqual(self.record()["status"], "completed")
        self.assertEqual(self.lineages, ["admission-test", "admission-test"])
        self.admissions.claim.assert_awaited_once()
        self.remote.assert_awaited_once()
        self.admissions.receipt.assert_awaited_with(self.admissions.claim.return_value, "finished")
        self.assertFalse(self.capacity.store.pending())
        self.assertFalse(self.platform.organization_team_executions.executions)
        self.assertEqual(self.platform.repository.list_teams(), [])
        self.assertFalse(self.capacity.store.team_active("atr_test"))
        self.assertIsNone(current_admission())

    async def test_personal_team_still_uses_its_own_repository(self):
        async with self.client() as client:
            team_id, _agents = await self._make_team(client)
        run = await self.platform.team_runs.start_run(team_id, {"task": "Synthetic task"})
        self.assertEqual(run["capacity"]["state"], "waiting")
        self.offer()
        await self.tick()
        entry = self.platform.team_runs.registry_entry(run["id"])
        await asyncio.wait_for(entry.task, 3)
        await self.tick()
        record = self.platform.repository.get_team_run(team_id, run["id"])
        self.assertEqual(record["status"], "completed")
        self.assertFalse(self.capacity.store.pending())
        self.remote.assert_not_awaited()
        self.assertEqual(self.repo.list_teams(), [])

    async def test_cancel_while_waiting_never_dispatches(self):
        await self.request("POST")
        await self.tick()
        self.assertEqual((await self.request("DELETE"))["status"], "cancelled")
        await self.tick()
        self.local.assert_not_awaited()
        self.admissions.claim.assert_not_awaited()
        self.admissions.receipt.assert_awaited_with(self.wait, "cancelled")
        self.assertFalse(self.capacity.store.pending())

    async def test_cancel_during_claim_prevents_execution(self):
        await self.request("POST")
        self.offer()

        async def claim(_offer):
            await self.request("DELETE")
            return self.admissions.claim.return_value

        self.admissions.claim.side_effect = claim
        await self.tick()
        await self.tick()
        self.assertEqual(self.record()["status"], "cancelled")
        self.local.assert_not_awaited()
        self.assertFalse(self.capacity.store.pending())

    async def test_cancel_running_releases_worker_and_admission(self):
        entered = asyncio.Event()
        stopped = asyncio.Event()

        async def worker(*_args):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()

        self.remote.side_effect = worker
        await self.request("POST")
        self.offer()
        await self.tick()
        await asyncio.wait_for(entered.wait(), 3)
        self.assertEqual((await self.request("DELETE"))["status"], "cancelled")
        self.assertTrue(stopped.is_set())
        await self.tick()
        self.assertFalse(self.capacity.store.pending())
        self.assertFalse(self.capacity.store.team_active("atr_test"))

    async def test_failure_releases_admission(self):
        self.local.side_effect = ServiceError("Synthetic failure", code="synthetic_failure")
        await self.request("POST")
        self.offer()
        await self.tick()
        await self.finish()
        self.assertEqual(self.record()["error"], "synthetic_failure")
        self.admissions.receipt.assert_awaited_with(self.admissions.claim.return_value, "finished")
        self.assertFalse(self.capacity.store.pending())

    async def test_revocation_while_waiting_does_not_cancel_workspace_queue(self):
        await self.request("POST")
        with patch.object(
            OrganizationTeamExecutionClient,
            "record",
            new=AsyncMock(
                side_effect=ServiceError(
                    "Revoked", status=403, code="organization_execution_revoked"
                )
            ),
        ):
            await self.tick()
        self.assertEqual(self.record()["status"], "cancelled")
        self.assertEqual(self.record()["error"], "organization_execution_revoked")
        self.local.assert_not_awaited()
        self.admissions.receipt.assert_awaited_with(self.wait, "cancelled")
        self.assertFalse(self.capacity.store.pending())

    async def test_missing_capability_does_not_interrupt_another_live_executor(self):
        await self.request("POST")
        descriptor = self.capacity.store.team_fence("atr_test")
        self.platform.organization_team_executions = OrganizationTeamExecutions(self.platform)
        try:
            await self.tick()
            self.assertEqual(self.record()["status"], "pending")
            self.admissions.receipt.assert_not_awaited()
        finally:
            from xnobrain.repositories.custom_page_locks import release

            release(descriptor)
        await self.tick()
        self.assertEqual(self.record()["error"], "interrupted_by_restart")

    async def test_restart_finalizes_wait_without_persisting_capability(self):
        await self.request("POST")
        await self.tick()
        self.assertNotIn(
            self.payload()["capability"],
            self.capacity.store.path.read_bytes().decode(errors="ignore"),
        )
        self.platform.organization_team_executions = OrganizationTeamExecutions(self.platform)
        await self.tick()
        self.assertEqual(self.record()["error"], "interrupted_by_restart")
        self.admissions.receipt.assert_awaited_with(self.wait, "cancelled")
        self.local.assert_not_awaited()
        self.assertFalse(self.capacity.store.pending())
        self.assertEqual((await self.request("POST"))["status"], "failed")

    async def test_restart_does_not_replay_ambiguous_dispatch(self):
        await self.request("POST")
        self.capacity.store.update("atr_test", "starting", self.admissions.claim.return_value)
        self.platform.organization_team_executions = OrganizationTeamExecutions(self.platform)
        await self.tick()
        self.assertEqual(self.record()["error"], "interrupted_by_restart")
        self.local.assert_not_awaited()
        self.remote.assert_not_awaited()
        self.assertFalse(self.capacity.store.pending())
