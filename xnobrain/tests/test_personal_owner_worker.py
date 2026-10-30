"""Real owner AgentManager/conversation boundary with a deterministic provider seam."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from xnobrain.integrations import organization_funding
from xnobrain.services.organization_connector import OrganizationConnector
from xnobrain.tests.test_team_runs import _TeamRunBase


class PersonalOwnerWorkerTests(_TeamRunBase):
    async def test_owner_profile_keeps_personal_context_and_private_outputs(self):
        await self.assert_owner_execution("owner")

    async def test_organization_funding_does_not_change_personal_ownership(self):
        await self.assert_owner_execution("organization")

    async def assert_owner_execution(self, funding_mode):
        platform = self.composition.service
        observations = []

        async def provider(prepared):
            observations.append(dict(prepared))
            # Only inference is stubbed; real AgentManager prepared the local
            # profile and the persistent ConversationRunService handles events.
            for event in (
                {"event": "run.started", "run_id": prepared["run_id"], "timestamp": 100.0},
                {
                    "event": "run.completed",
                    "run_id": prepared["run_id"],
                    "timestamp": 101.0,
                    "output": "Synthetic owner output",
                    "usage": {},
                },
            ):
                yield ("data: " + json.dumps(event) + "\n\n").encode()
            yield b"data: [DONE]\n\n"

        with (
            patch.dict(
                os.environ,
                {"RUNTIME_CONTROL_URL": "http://control", "RUNTIME_ACCOUNTING_MODE": "legacy"},
            ),
            patch.object(platform.analytics, "require_execution_budget", new=AsyncMock()),
            patch.object(platform.agents, "_chat_stream_events_scoped", new=provider),
        ):
            connector = OrganizationConnector(platform)
            link = connector.link("org_aaaaaaaa11111111")
            command = {
                "command_id": "cmd_personal_owner",
                "run_id": "run_worker",
                "idempotency_key": "cmd_personal_owner",
                "work_context_id": "cctx_personal",
                "work_owner_kind": "personal",
                "subject_user_id": "initiator",
                "funding": {
                    "mode": funding_mode,
                    "model": "org/fixture/synthetic" if funding_mode == "organization" else "",
                },
                "work_payer_organization_id": "org_aaaaaaaa11111111"
                if funding_mode == "organization"
                else "",
                "allowed_skills": [],
                "allowed_toolsets": [],
                "approval_mode": "auto",
                "limits": {},
            }
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(200, json={"data": {}})
                )
            ) as client:
                with (
                    patch.object(link, "import_inputs", new=AsyncMock()) as imported,
                    patch.object(link, "publish_outputs", new=AsyncMock()) as published,
                ):
                    await link.execute_acknowledged(
                        client,
                        {},
                        command,
                        "synthetic-digest",
                        "big-brother",
                        "Synthetic task",
                        datetime(2099, 1, 1, tzinfo=timezone.utc),
                    )
                    imported.assert_not_awaited()
                    published.assert_not_awaited()
            self.assertEqual(len(observations), 1)
            prepared = observations[0]
            self.assertEqual(Path(prepared["profile_dir"]), self.root)
            self.assertEqual(prepared["name"], "big-brother")
            self.assertEqual(prepared["ownership_context"]["owner_kind"], "personal")
            self.assertEqual(
                prepared["ownership_context"]["payer_kind"],
                "organization" if funding_mode == "organization" else "personal",
            )
            self.assertIsNone(prepared["ownership_context"]["organization_id"])
            if funding_mode == "organization":
                self.assertEqual(
                    prepared["ownership_context"]["payer_organization_id"], "org_aaaaaaaa11111111"
                )
            self.assertNotIn("organization-runs/", prepared["message"])
            result = link.store.outbox()[0]["body"]
            self.assertEqual(result["state"], "completed")
            self.assertEqual(result["safe_summary"], {"output": "Synthetic owner output"})
            self.assertEqual(result["artifacts"], [])
            self.assertIsNone(organization_funding.funding_for(prepared["conversation_id"]))
        await platform.conversation_runs.shutdown()
