"""Control conflict codes reach the team step instead of revoking the run."""

import unittest
from unittest.mock import patch

import httpx

from xnobrain.integrations import organization_team_executions as module
from xnobrain.integrations.organization_team_executions import (
    OrganizationTeamExecutionClient,
)
from xnobrain.services.base import ServiceError


class ExecutionClientErrorTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, status, body):
        def respond(request):
            return httpx.Response(status, json=body)

        real = httpx.AsyncClient

        def client(**kwargs):
            return real(transport=httpx.MockTransport(respond), **kwargs)

        execution = OrganizationTeamExecutionClient(
            "https://control.example", "org_test", "atr_test", "capability"
        )
        with patch.object(module.httpx, "AsyncClient", side_effect=client):
            with self.assertRaises(ServiceError) as raised:
                await execution.request("POST", "/workers", {"agent_id": "ab"})
        return raised.exception

    async def test_specific_conflicts_keep_control_code(self):
        for message in (
            "source_agent_offline",
            "organization_funding_unavailable",
            "no_assignable_agent",
        ):
            with self.subTest(message=message):
                error = await self.call(409, {"success": False, "message": message})
                self.assertEqual(error.code, message)
                self.assertEqual(error.status, 409)

    async def test_forbidden_superseded_or_unreadable_conflicts_revoke(self):
        for status, body in (
            (403, {"success": False, "message": "forbidden"}),
            (409, {"success": False, "message": "revision_conflict"}),
            (409, {"success": False, "message": "revoked"}),
            (409, {"success": False, "message": "Not A Code"}),
            (409, {"success": False}),
        ):
            with self.subTest(status=status, body=body):
                error = await self.call(status, body)
                self.assertEqual(error.code, "organization_execution_revoked")

    async def test_invalid_input_keeps_safe_code_only(self):
        error = await self.call(422, {"success": False, "message": "stage_model_unavailable"})
        self.assertEqual(error.code, "stage_model_unavailable")
        error = await self.call(422, {"success": False, "message": "free text detail"})
        self.assertEqual(error.code, "organization_execution_invalid")


if __name__ == "__main__":
    unittest.main()
