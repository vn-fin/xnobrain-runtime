"""Personal delegated teams retain capacity waiting and cancel guarantees."""

import asyncio
import os
from unittest.mock import patch

from xnobrain.repositories.organization_team_executions import OrganizationTeamExecutionRepository
from xnobrain.services.personal_delegated_executions import PersonalDelegatedExecutions
from xnobrain.tests import test_organization_team_admission as fixtures
from xnobrain.tests.test_personal_delegated_executions import PersonalExecutionTests


class PersonalTeamAdmissionTests(fixtures.OrganizationTeamAdmissionTests):
    path = "/xnobrain/api/runtime/v1/personal-delegated-executions/atm_test/atr_test"
    payload = PersonalExecutionTests.payload

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.repo = OrganizationTeamExecutionRepository(
            self.platform.repository, "personal", namespace="personal-delegated-executions"
        )

    async def asyncTearDown(self):
        await self.platform.personal_delegated_executions.shutdown()
        await super().asyncTearDown()

    async def finish(self):
        engine = self.platform.personal_delegated_executions.executions[
            ("personal", "atm_test", "atr_test")
        ]
        await asyncio.wait_for(engine.registry_entry("atr_test").task, 3)
        await self.tick()

    async def test_wait_then_admit_runs_coordinator_and_synthesis_once(self):
        run = await self.request("POST")
        self.assertEqual(run["status"], "pending")
        await self.tick()
        self.local.assert_not_awaited()
        self.remote.assert_not_awaited()
        self.offer()
        await self.tick()
        await self.finish()
        self.assertEqual(self.record()["status"], "completed")
        self.assertEqual(self.lineages, ["admission-test", "admission-test"])
        self.remote.assert_awaited_once()
        self.assertFalse(self.capacity.store.pending())
        self.assertFalse(self.platform.personal_delegated_executions.executions)

    async def test_restart_finalizes_wait_without_persisting_capability(self):
        await self.request("POST")
        self.platform.personal_delegated_executions = PersonalDelegatedExecutions(self.platform)
        await self.tick()
        self.assertEqual(self.record()["status"], "failed")
        self.assertEqual(self.record()["error"], "interrupted_by_restart")
        self.remote.assert_not_awaited()

    async def test_cancel_while_waiting_never_dispatches(self):
        await self.request("POST")
        with patch.dict(os.environ, {"FT_ENABLE_PERSONAL_ORG_WORKSPACE_EXPERIENCE": "false"}):
            cancelled = await self.request("DELETE")
            self.assertEqual(cancelled["status"], "cancelled")
            await self.tick()
        self.local.assert_not_awaited()
        self.remote.assert_not_awaited()

    # These fixtures intentionally exercise the organization service registry;
    # personal admission-specific variants above cover our separate namespace.
    test_missing_capability_does_not_interrupt_another_live_executor = None
    test_restart_does_not_replay_ambiguous_dispatch = None
    test_revocation_while_waiting_does_not_cancel_workspace_queue = None
