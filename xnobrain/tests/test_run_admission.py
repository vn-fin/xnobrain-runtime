"""Capacity waits must survive restarts without holding execution fences."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from xnobrain.integrations.run_admission import AdmissionClient, immediate_submission
from xnobrain.repositories.custom_page_locks import execution_active, release
from xnobrain.services.base import ServiceError
from xnobrain.services.conversation_runs import ConversationRunService
from xnobrain.services.run_admission import RunAdmissionService
from xnobrain.tests import test_conversation_runs as fixtures


class CapacityQueueTests(unittest.IsolatedAsyncioTestCase):
    pressure_reason = "pool_memory_pressure"

    async def test_cancel_reconciles_registration_whose_response_was_lost(self):
        record = await self.service.start_run("agent-one", "session-one", {"input": "hello"})
        self.client.register.side_effect = ServiceError("unavailable", status=503)
        await self.capacity._tick(self.capacity.store.pending()[0])
        await self.service.cancel_run("agent-one", "session-one", record["id"])
        await self.capacity._tick(self.capacity.store.pending()[0])
        self.client.observe.assert_awaited_with(record["id"])
        self.client.receipt.assert_awaited_with(self.offer, "cancelled")
        self.assertFalse(self.capacity.store.pending())
        self.assertFalse(self.agents.received)

    async def test_other_coordinator_does_not_heal_live_team_as_interrupted(self):
        from xnobrain.services.team_runs import TeamRunService

        repository = SimpleNamespace(put_team_run=Mock())
        teams = TeamRunService(repository, None, SimpleNamespace(run_admission=self.capacity))
        record = {"id": "team-run", "status": "running", "steps": []}
        fence = self.capacity.store.team_fence(record["id"])
        try:
            self.assertEqual(teams._heal_if_stale(record)["status"], "running")
            repository.put_team_run.assert_not_called()
        finally:
            release(fence)
        self.assertEqual(teams._heal_if_stale(record)["status"], "failed")

    async def test_local_disable_blocks_existing_offer_but_allows_receipts(self):
        client = AdmissionClient(AsyncMock())
        client.call = AsyncMock()
        with patch("xnobrain.integrations.run_admission.enabled", return_value=False):
            with self.assertRaises(ServiceError) as error:
                await client.claim({"id": "grant", "nonce": "n"})
            self.assertEqual(error.exception.code, "capacity_feature_disabled")
            client.call.assert_not_awaited()
            await client.receipt({"id": "grant"}, "finished")
            client.call.assert_awaited_once()

    async def asyncSetUp(self):
        await fixtures.ConversationRunServiceTests.asyncSetUp(self)
        self.client = AsyncMock()
        self.offer = {"id": "admission1", "state": "waiting", "reason_code": self.pressure_reason}
        self.client.register.return_value = self.offer
        self.client.observe.return_value = self.offer
        self.client.claim.return_value = {
            **self.offer,
            "state": "starting",
            "nonce": "nonce",
            "runtime_boot": "boot",
        }
        self.platform = SimpleNamespace(
            conversation_runs=self.service,
            repository=self.repository,
            agents=self.agents,
            runtime_updates=SimpleNamespace(require_dispatch=lambda: None),
        )
        self.capacity = RunAdmissionService(self.platform, self.client)
        self.service.capacity = self.capacity

    async def asyncTearDown(self):
        await fixtures.ConversationRunServiceTests.asyncTearDown(self)

    async def test_wait_is_durable_without_activity_and_dispatches_once(self):
        record = await self.service.start_run(
            "agent-one", "session-one", {"input": "hello", "idempotency_key": "once"}
        )
        self.assertEqual(record["capacity"]["state"], "waiting")
        self.assertIsNone(record["started_at"])
        self.assertFalse(execution_active(self.repository.data_dir, "agent-one"))
        await self.capacity._tick(self.capacity.store.pending()[0])
        self.assertFalse(self.agents.received)
        self.assertEqual(
            self.service.get_run("agent-one", "session-one", record["id"])["capacity"][
                "reason_code"
            ],
            self.pressure_reason,
        )
        # A fresh service recovers the accepted identity and private payload.
        self.service = ConversationRunService(self.repository, self.agents, self.analytics)
        self.platform.conversation_runs = self.service
        self.capacity = RunAdmissionService(self.platform, self.client)
        self.service.capacity = self.capacity
        replay = await self.service.start_run(
            "agent-one", "session-one", {"input": "hello", "idempotency_key": "once"}
        )
        self.assertEqual(record["id"], replay["id"])
        self.client.observe.return_value = {**self.offer, "state": "offered", "nonce": "nonce"}
        await self.capacity._tick(self.capacity.store.pending()[0])
        await asyncio.sleep(0.01)
        self.assertEqual(self.agents.received["run_id"], record["id"])
        await self.capacity._tick(self.capacity.store.pending()[0])
        self.client.claim.assert_awaited_once()
        self.agents.release.set()
        await self.agents.finished.wait()

    async def test_cancel_wait_never_dispatches(self):
        record = await self.service.start_run("agent-one", "session-one", {"input": "hello"})
        await self.service.cancel_run("agent-one", "session-one", record["id"])
        await self.capacity._tick(self.capacity.store.pending()[0])
        self.assertEqual(self.capacity.store.pending(), [])
        self.assertFalse(self.agents.received)
        self.client.claim.assert_not_awaited()

    async def test_ambiguous_start_is_not_replayed(self):
        record = await self.service.start_run("agent-one", "session-one", {"input": "hello"})
        self.capacity.store.update(record["id"], "starting", self.client.claim.return_value)
        await self.capacity._tick(self.capacity.store.pending()[0])
        current = self.service.get_run("agent-one", "session-one", record["id"])
        self.assertEqual(current["status"], "failed")
        self.assertFalse(self.agents.received)

    async def test_immediate_rejection_leaves_no_executable_hidden_queue(self):
        with immediate_submission(), self.assertRaises(ServiceError) as caught:
            await self.service.start_run("agent-one", "session-one", {"input": "hello"})
        self.assertEqual(caught.exception.status, 503)
        await self.capacity._tick(self.capacity.store.pending()[0])
        self.assertEqual(self.capacity.store.pending(), [])
        self.client.claim.assert_not_awaited()

    async def test_lost_claim_response_recovers_without_execution(self):
        record = await self.service.start_run("agent-one", "session-one", {"input": "hello"})
        self.capacity.store.update(record["id"], "claiming", self.client.claim.return_value)
        self.client.observe.return_value = self.client.claim.return_value
        await self.capacity._tick(self.capacity.store.pending()[0])
        self.client.receipt.assert_awaited_with(self.client.claim.return_value, "aborted")
        self.assertEqual(self.capacity.store.pending()[0]["state"], "waiting")
        self.assertFalse(self.agents.received)

    async def test_browser_parent_id_does_not_skip_admission(self):
        record = await self.service.start_run(
            "agent-one", "session-one", {"input": "hello", "parent_run_id": "forged"}
        )
        self.assertEqual(record["capacity"]["state"], "waiting")
        self.assertFalse(self.agents.received)

    async def test_cancel_during_claim_prevents_executor_handoff(self):
        record = await self.service.start_run("agent-one", "session-one", {"input": "hello"})
        self.client.register.return_value = {**self.offer, "state": "offered", "nonce": "nonce"}

        async def claim(_offer):
            await self.capacity.cancel(record)
            return self.client.claim.return_value

        self.client.claim.side_effect = claim
        await self.capacity._tick(self.capacity.store.pending()[0])
        self.assertFalse(self.agents.received)
        self.client.receipt.assert_awaited_with(self.client.claim.return_value, "cancelled")


class CPUCapacityQueueTests(CapacityQueueTests):
    pressure_reason = "pool_cpu_pressure"
