"""Personal saved references use owner-local coordinators and remote grants."""

import asyncio
import os
from unittest.mock import AsyncMock, patch

from xnobrain.integrations.organization_team_executions import OrganizationTeamExecutionClient
from xnobrain.services.personal_delegated_executions import PersonalDelegatedExecutions
from xnobrain.tests.test_organization_team_executions import OrganizationExecutionTests


class PersonalExecutionTests(OrganizationExecutionTests):
    def payload(self):
        payload = OrganizationExecutionTests.payload(self)
        payload["source_revision"] = "sha256:" + "a" * 64
        payload["source_kind"] = "agent_team"
        payload["team"]["members"][0]["external_agent"] = {
            "organization_id": "org_test",
            "registration_id": "member-registration",
            "grant_id": "grant_test",
        }
        return payload

    async def test_personal_definition_save_and_remote_execution_are_bound(self):
        service = self.composition.service
        definition_path = "/xnobrain/api/runtime/v1/personal-agent-teams/new"
        with (
            patch.dict(
                os.environ,
                {
                    "RUNTIME_INTERNAL_SERVICE_TOKEN": "private-test",
                    "RUNTIME_CONTROL_URL": "http://control",
                    "FT_ENABLE_PERSONAL_ORG_WORKSPACE_EXPERIENCE": "true",
                },
            ),
            patch.object(service.analytics, "require_execution_budget", new=AsyncMock()),
            patch.object(
                service.agents, "chat", new=AsyncMock(return_value={"response": "local answer"})
            ) as local,
            patch.object(
                OrganizationTeamExecutionClient,
                "chat",
                new=AsyncMock(return_value={"response": "delegated answer"}),
            ) as remote,
            patch.object(OrganizationTeamExecutionClient, "record", new=AsyncMock()),
        ):
            async with self.client() as client:
                # A public native save must not bypass Control's grant admission.
                denied = await client.post(
                    "/xnobrain/api/runtime/v1/teams", json=self.payload()["team"]
                )
                self.assertEqual(denied.status_code, 403, denied.text)
                denied = await client.put(definition_path, json=self.payload()["team"])
                self.assertEqual(denied.status_code, 403, denied.text)
                saved = await client.put(
                    definition_path, **self.signed("PUT", definition_path, self.payload()["team"])
                )
                self.assertEqual(saved.status_code, 200, saved.text)
                team = saved.json()["data"]
                self.assertTrue(team["source_revision"].startswith("sha256:"))
                self.assertEqual(team["members"][0]["external_agent"]["grant_id"], "grant_test")
                # Even a persisted foreign alias cannot execute through the native path.
                denied = await client.post(
                    f"/xnobrain/api/runtime/v1/teams/{team['id']}/runs",
                    json={"task": "Synthetic task"},
                )
                self.assertEqual(denied.status_code, 409, denied.text)
                path = (
                    f"/xnobrain/api/runtime/v1/personal-delegated-executions/{team['id']}/pex_test"
                )
                denied = await client.post(path, json=self.payload())
                self.assertEqual(denied.status_code, 403, denied.text)
                result = await client.post(path, **self.signed("POST", path, self.payload()))
                self.assertEqual(result.status_code, 200, result.text)
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
                self.assertTrue(all(step["conversation_id"] is None for step in state["steps"]))
                self.assertEqual(len(service.repository.list_teams()), 1)
                self.assertEqual(service.repository.list_team_runs(team["id"]), [])
                # Restart reconciliation returns the durable result without replay.
                service.personal_delegated_executions = PersonalDelegatedExecutions(service)
                again = await client.post(path, **self.signed("POST", path, self.payload()))
                self.assertEqual(again.json()["data"]["status"], "completed")
                self.assertEqual(remote.call_count, 1)
                # Rollback disables new work but never strands existing receipts
                # or cancellation behind the feature gate.
                with patch.dict(
                    os.environ, {"FT_ENABLE_PERSONAL_ORG_WORKSPACE_EXPERIENCE": "false"}
                ):
                    recovered = await client.post(path, **self.signed("POST", path, self.payload()))
                    self.assertEqual(recovered.status_code, 200, recovered.text)
                    self.assertEqual(recovered.json()["data"]["status"], "completed")
                    for method in ("GET", "DELETE"):
                        receipt = await client.request(method, path, **self.signed(method, path))
                        self.assertEqual(
                            receipt.status_code, 200 if method == "GET" else 409, receipt.text
                        )
                    new_path = path.replace("pex_test", "pex_new")
                    denied = await client.post(
                        new_path, **self.signed("POST", new_path, self.payload())
                    )
                    self.assertEqual(denied.status_code, 404, denied.text)
                self.assertEqual(remote.call_count, 1)
                for file in (self.data_dir / "personal-delegated-executions").rglob("*"):
                    if file.is_file():
                        self.assertNotIn("a" * 64, file.read_text())
