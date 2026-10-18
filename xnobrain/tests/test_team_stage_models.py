"""Stage model persistence, forwarding and Control failure contracts."""

import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from xnobrain.integrations.organization_team_executions import OrganizationTeamExecutionClient
from xnobrain.services.base import ServiceError
from xnobrain.tests.test_team_runs import _TeamRunBase


class StageModelTests(_TeamRunBase):
    async def test_pins_persist_and_all_stages_forward_independently(self):
        async with self.client() as client:
            team_id, ids = await self._make_team(client)
            url = f"/xnobrain/api/runtime/v1/teams/{team_id}"
            team = (await client.get(url)).json()["data"]
            team.update(
                coordinator_model=" coordinator/pinned ",
                synthesis_model="synthesis/pinned",
                workflow=[
                    {"id": "one", "task": "work", "agent_id": ids[1], "model": " worker/one "},
                    {"id": "two", "task": "review", "agent_id": ids[1], "model": "worker/two"},
                ],
            )
            response = await client.put(url, json=team)
            self.assertEqual(response.status_code, 200, response.text)
            saved = (await client.get(url)).json()["data"]
            self.assertEqual(saved["coordinator_model"], "coordinator/pinned")
            self.assertEqual(saved["workflow"][0]["model"], "worker/one")
            chat = self._mock_completing_chat(ids)
            response = await client.post(url + "/runs", json={"task": "work"})
            record = await self._poll_until_terminal(client, team_id, response.json()["data"]["id"])
            self.assertEqual(record["status"], "completed", record)
            self.assertEqual(
                sorted(call.args[1]["model"] for call in chat.await_args_list),
                ["coordinator/pinned", "synthesis/pinned", "worker/one", "worker/two"],
            )
            self.assertEqual(
                [step["model"] for step in record["steps"]], ["worker/one", "worker/two"]
            )

    async def test_unpinned_stages_do_not_override_agent_and_invalid_save_is_atomic(self):
        async with self.client() as client:
            team_id, ids = await self._make_team(client)
            url = f"/xnobrain/api/runtime/v1/teams/{team_id}"
            team = (await client.get(url)).json()["data"]
            for field in ("coordinator_model", "synthesis_model"):
                invalid = {**team, field: "x" * 201}
                self.assertEqual((await client.put(url, json=invalid)).status_code, 422)
            invalid = {
                **team,
                "workflow": [{"id": "one", "task": "work", "agent_id": ids[1], "model": "x" * 201}],
            }
            self.assertEqual((await client.put(url, json=invalid)).status_code, 422)
            self.assertEqual((await client.get(url)).json()["data"], team)
            chat = self._mock_completing_chat(ids)
            response = await client.post(url + "/runs", json=self._dag_body())
            record = await self._poll_until_terminal(client, team_id, response.json()["data"]["id"])
            self.assertEqual(record["status"], "completed")
            self.assertTrue(all("model" not in call.args[1] for call in chat.await_args_list))
            self.assertTrue(all(step.get("model") is None for step in record["steps"]))


class OrganizationStageModelClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_body_and_422_code(self):
        client = OrganizationTeamExecutionClient("http://control", "org_test", "run_test", "cap")
        captured = []

        def handle(request):
            captured.append(json.loads(request.content))
            return httpx.Response(
                422, json={"success": False, "message": "stage_model_not_allowed"}
            )

        transport = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        with patch("httpx.AsyncClient", return_value=transport):
            with self.assertRaises(ServiceError) as error:
                await client.chat("worker", {"message": "work", "model": "personal/pinned"})
        self.assertEqual(error.exception.code, "stage_model_not_allowed")
        self.assertEqual(captured[0]["model"], "personal/pinned")

    async def test_validation_and_funding_error_envelopes(self):
        cases = [
            (422, {"message": "stage_model_not_allowed"}, "stage_model_not_allowed"),
            (422, {"message": "validation_failed"}, "validation_failed"),
            (422, {"message": "Invalid worker request"}, "organization_execution_invalid"),
            (422, {"message": "x" * 101}, "organization_execution_invalid"),
            (422, {"message": 123}, "organization_execution_invalid"),
            (422, [], "organization_execution_invalid"),
            (422, None, "organization_execution_invalid"),
            (
                409,
                {"message": "organization_funding_unavailable"},
                "organization_funding_unavailable",
            ),
            (409, {"message": "revoked"}, "organization_execution_revoked"),
        ]
        for status, envelope, code in cases:
            with self.subTest(status=status, envelope=envelope):
                client = OrganizationTeamExecutionClient(
                    "http://control", "org_test", "run_test", "cap"
                )
                response = (
                    httpx.Response(status, text="not JSON")
                    if envelope is None
                    else httpx.Response(status, json=envelope)
                )
                transport = httpx.AsyncClient(
                    transport=httpx.MockTransport(lambda request: response)
                )
                with patch("httpx.AsyncClient", return_value=transport):
                    with self.assertRaises(ServiceError) as error:
                        await client.request("POST", "/workers", {})
                self.assertEqual(error.exception.code, code)
                self.assertEqual(
                    error.exception.status,
                    403 if code == "organization_execution_revoked" else status,
                )

    async def test_revocation_codes_remain_revocations(self):
        for status in (403, 409):
            client = OrganizationTeamExecutionClient(
                "http://control", "org_test", "run_test", "cap"
            )
            transport = httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(status, json={"message": "revoked"})
                )
            )
            with patch("httpx.AsyncClient", return_value=transport):
                with self.assertRaises(ServiceError) as error:
                    await client.request("POST", "/workers", {})
            self.assertEqual(error.exception.code, "organization_execution_revoked")

    async def test_legacy_worker_omits_pin_and_terminal_failure_keeps_code(self):
        client = OrganizationTeamExecutionClient("http://control", "org_test", "run_test", "cap")
        request = AsyncMock(
            side_effect=[
                {"id": "worker"},
                {
                    "status": "failed",
                    "steps": [{"status": "failed", "error_code": "stage_model_not_allowed"}],
                },
            ]
        )
        with patch.object(client, "request", request):
            with self.assertRaises(ServiceError) as error:
                await client.chat("worker", {"message": "work"})
        self.assertIsNone(request.await_args_list[0].args[2]["model"])
        self.assertEqual(error.exception.code, "stage_model_not_allowed")
