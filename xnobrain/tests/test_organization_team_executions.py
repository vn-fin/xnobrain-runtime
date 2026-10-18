"""Private signed execution, member routing, isolation, and cancellation."""

import asyncio
import hashlib
import hmac
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

from xnobrain.integrations.organization_team_executions import OrganizationTeamExecutionClient
from xnobrain.services.base import ServiceError
from xnobrain.services.organization_team_executions import OrganizationTeamExecutions
from xnobrain.tests.test_team_runs import _TeamRunBase


class OrganizationExecutionTests(_TeamRunBase):
    def signed(self, method, path, payload=None):
        raw = json.dumps(payload).encode()
        digest = hashlib.sha256(raw).hexdigest()
        signature = hmac.new(
            b"private-test", f"{method}\n{path}\n{digest}".encode(), hashlib.sha256
        ).hexdigest()
        return {
            "content": raw,
            "headers": {"Content-Type": "application/json", "X-XNOBrain-Team-Signature": signature},
        }

    def payload(self):
        return {
            "team": {
                "name": "Org workflow",
                "orchestrator_id": "big-brother",
                "members": [{"agent_id": "member-registration", "role": "writer"}],
                "communication_level": 2,
            },
            "input": {"task": "Synthetic task"},
            "capability": "a" * 64,
        }

    async def test_signed_run_routes_workers_remotely_and_never_creates_personal_team(self):
        service = self.composition.service
        path = "/xnobrain/api/runtime/v1/organization-team-executions/org_test/atm_test/atr_test"
        snapshots = []

        async def record(_client, record):
            snapshots.append(dict(record))

        with (
            patch.dict(
                os.environ,
                {
                    "RUNTIME_INTERNAL_SERVICE_TOKEN": "private-test",
                    "RUNTIME_CONTROL_URL": "http://control",
                },
            ),
            patch.object(service.analytics, "require_execution_budget", new=AsyncMock()),
            patch.object(
                service.agents,
                "chat",
                new=AsyncMock(return_value={"response": "coordinator output"}),
            ) as local,
            patch.object(
                OrganizationTeamExecutionClient,
                "chat",
                new=AsyncMock(return_value={"response": "member output"}),
            ) as remote,
            patch.object(OrganizationTeamExecutionClient, "record", new=record),
        ):
            async with self.client() as client:
                denied = await client.post(path, json=self.payload())
                self.assertEqual(denied.status_code, 403)
                tampered = self.signed("POST", path, self.payload())
                tampered["content"] = tampered["content"].replace(
                    b"Synthetic task", b"Modified task"
                )
                denied = await client.post(path, **tampered)
                self.assertEqual(denied.status_code, 403)
                denied = await client.post(
                    path + "_other", **self.signed("POST", path, self.payload())
                )
                self.assertEqual(denied.status_code, 403)
                started = await client.post(path, **self.signed("POST", path, self.payload()))
                self.assertEqual(started.status_code, 200, started.text)
                for _ in range(100):
                    response = await client.request("GET", path, **self.signed("GET", path))
                    state = response.json()["data"]
                    if state["status"] in {"completed", "failed"}:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(state["status"], "completed", state)
                self.assertEqual(
                    [call.args[0] for call in local.call_args_list], ["big-brother", "big-brother"]
                )
                self.assertEqual(remote.call_args.args[0], "member-registration")
                self.assertNotIn(str(self.data_dir), remote.call_args.args[1]["message"])
                self.assertEqual(service.repository.list_teams(), [])
                again = await client.post(path, **self.signed("POST", path, self.payload()))
                self.assertEqual(again.json()["data"]["id"], "atr_test")
                self.assertEqual(remote.call_count, 1)
                self.assertEqual(snapshots[-1]["status"], "completed")
                # No execution capability is persisted in snapshots.
                for file in (self.data_dir / "organization-team-executions").rglob("*"):
                    if file.is_file():
                        self.assertNotIn("a" * 64, file.read_text())

    async def test_cancellation_and_restart_do_not_replay_worker_side_effects(self):
        service = self.composition.service
        path = "/xnobrain/api/runtime/v1/organization-team-executions/org_test/atm_test/atr_cancel"
        entered = asyncio.Event()
        stopped = asyncio.Event()

        async def worker(*_args):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()

        with (
            patch.dict(
                os.environ,
                {
                    "RUNTIME_INTERNAL_SERVICE_TOKEN": "private-test",
                    "RUNTIME_CONTROL_URL": "http://control",
                },
            ),
            patch.object(service.analytics, "require_execution_budget", new=AsyncMock()),
            patch.object(
                service.agents, "chat", new=AsyncMock(return_value={"response": "guidance"})
            ),
            patch.object(OrganizationTeamExecutionClient, "chat", new=worker),
            patch.object(OrganizationTeamExecutionClient, "record", new=AsyncMock()),
        ):
            async with self.client() as client:
                await client.post(path, **self.signed("POST", path, self.payload()))
                await asyncio.wait_for(entered.wait(), 3)
                response = await client.request("DELETE", path, **self.signed("DELETE", path))
                self.assertEqual(response.json()["data"]["status"], "cancelled")
                self.assertTrue(stopped.is_set())
                service.organization_team_executions = OrganizationTeamExecutions(service)
                response = await client.post(path, **self.signed("POST", path, self.payload()))
                self.assertEqual(response.json()["data"]["status"], "cancelled")


class ExecutionClientFailureTests(unittest.IsolatedAsyncioTestCase):
    async def failed_worker(self, step):
        client = OrganizationTeamExecutionClient("https://control.example", "org_1", "atr_1", "cap")
        replies = [{"id": "run_w"}, {"status": "failed", "steps": [step]}]

        async def request(method, suffix, body=None):
            return replies.pop(0) if replies else {}

        with patch.object(client, "request", side_effect=request):
            with self.assertRaises(ServiceError) as raised:
                await client.chat("agent-1", {"message": "hi"})
        return raised.exception.code

    async def test_failed_worker_keeps_the_member_reason(self):
        code = await self.failed_worker(
            {"status": "failed", "error_code": "runtime_execution_failed"}
        )
        self.assertEqual(code, "runtime_execution_failed")

    async def test_failed_worker_without_reason_uses_generic_code(self):
        self.assertEqual(await self.failed_worker({"status": "failed"}), "member_worker_failed")
