"""Immutable conversation ownership API and persistence tests."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import yaml
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from xnobrain.app import XNOBrainApplication
from xnobrain.integrations import AgentManager, GlobalConfigManager
from xnobrain.repositories import StoreError
from xnobrain.trusted_context import (
    TRUSTED_CONVERSATION_CONTEXT_HEADER,
    TRUSTED_CONVERSATION_CONTEXT_SIGNATURE_HEADER,
    TRUSTED_ORGANIZATION_HEADER,
    TRUSTED_SIGNATURE_HEADER,
    TRUSTED_SUBJECT_HEADER,
    conversation_context_signature,
    encode_conversation_context,
    principal_signature,
)


class FakeRouter:
    async def list_connections(self):
        return {"connections": []}

    async def list_models(self):
        return {"data": []}

    async def status(self):
        return {"available": True, "provider_count": 0}


class ConversationOwnershipTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        temporary = Path(self.temporary.name)
        self.root = temporary / "root"
        self.profiles = temporary / "profiles"
        self.root.mkdir()
        self.profiles.mkdir()
        (self.root / "config.yaml").write_text(
            yaml.safe_dump({"model": {"default": "auto"}}),
            encoding="utf-8",
        )
        self.token = "test-internal-token"
        self.environment = patch.dict(
            os.environ,
            {
                "HERMES_HOME": str(self.root),
                "HERMES_ROOT_PROFILE": str(self.root),
                "HERMES_PROFILES_ROOT": str(self.profiles),
                "DATA_DIR": str(temporary / "data"),
                "RUNTIME_INCLUDE_PACKAGED_SKILLS": "false",
                "RUNTIME_INTERNAL_SERVICE_TOKEN": self.token,
            },
        )
        self.environment.start()
        app = FastAPI()
        composition = XNOBrainApplication(
            AgentManager(
                root_profile=self.root,
                profiles_root=self.profiles,
                legacy_agents_root=temporary / "legacy",
            ),
            GlobalConfigManager(root_profile=self.root),
            FakeRouter(),
        )
        composition.register(app)
        self.app = app
        self.service = composition.service

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def client(self):
        return AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test")

    def trusted_headers(
        self,
        subject: str = "user-1",
        organization: str = "org-1",
        context: dict | None = None,
    ):
        context = context or self.organization_context()
        encoded_context = encode_conversation_context(context)
        return {
            TRUSTED_SUBJECT_HEADER: subject,
            TRUSTED_ORGANIZATION_HEADER: organization,
            TRUSTED_SIGNATURE_HEADER: principal_signature(
                self.token,
                subject,
                organization_id=organization,
            ),
            TRUSTED_CONVERSATION_CONTEXT_HEADER: encoded_context,
            TRUSTED_CONVERSATION_CONTEXT_SIGNATURE_HEADER: (
                conversation_context_signature(
                    self.token,
                    subject,
                    "",
                    organization,
                    encoded_context,
                )
            ),
        }

    @staticmethod
    def organization_context() -> dict:
        return {
            "schema_version": 1,
            "id": "cctx-org-1",
            "owner_kind": "organization",
            "organization_id": "org-1",
            "payer_kind": "organization_sponsor",
            "sponsor_grant_id": "sgr-1",
            "membership_revision_at_create": 8,
            "policy_revision_at_create": 3,
            "state": "active",
        }

    async def test_create_list_detail_and_run_keep_durable_immutable_context(self):
        async with self.client() as client:
            created = await client.post(
                "/xnobrain/api/runtime/v1/sessions?agent=big-brother",
                json={
                    "title": "Organization research",
                    "ownership_context": self.organization_context(),
                },
                headers=self.trusted_headers(),
            )
            self.assertEqual(created.status_code, 201, created.text)
            conversation = created.json()["data"]
            conversation_id = conversation["id"]
            self.assertEqual(conversation["ownership_context"], self.organization_context())

            listed = await client.get("/xnobrain/api/runtime/v1/sessions?agent=big-brother")
            detailed = await client.get(
                f"/xnobrain/api/runtime/v1/sessions/{conversation_id}/detail?agent=big-brother"
            )
            with patch.object(
                self.service.conversation_runs,
                "start_run",
                new=AsyncMock(
                    return_value={
                        "id": "run_" + "a" * 32,
                        "ownership_context": self.organization_context(),
                    }
                ),
            ) as start_run:
                run = await client.post(
                    f"/xnobrain/api/runtime/v1/sessions/{conversation_id}/runs?agent=big-brother",
                    json={"input": "Prepare the report"},
                )

        self.assertEqual(
            listed.json()["data"]["conversations"][0]["ownership_context"],
            self.organization_context(),
        )
        self.assertEqual(detailed.json()["data"]["ownership_context"], self.organization_context())
        self.assertEqual(run.json()["data"]["ownership_context"], self.organization_context())
        self.assertEqual(
            start_run.await_args.kwargs["ownership_context"], self.organization_context()
        )
        persisted = self.service.repository.get_conversation_context(self.root, conversation_id)
        self.assertEqual(persisted["actor_user_id"], "user-1")
        self.assertFalse(persisted["legacy_backfill"])

    async def test_legacy_conversation_is_backfilled_personal_on_read(self):
        raw = self.service.agents.create_conversation("big-brother", {"title": "Legacy personal"})[
            "conversation"
        ]
        async with self.client() as client:
            first = await client.get(
                f"/xnobrain/api/runtime/v1/sessions/{raw['id']}/detail?agent=big-brother"
            )
            second = await client.get(
                f"/xnobrain/api/runtime/v1/sessions/{raw['id']}/detail?agent=big-brother"
            )

        expected = {
            "schema_version": 1,
            "id": "personal",
            "owner_kind": "personal",
            "organization_id": None,
            "payer_kind": "personal",
            "sponsor_grant_id": None,
            "membership_revision_at_create": None,
            "policy_revision_at_create": None,
            "state": "active",
        }
        self.assertEqual(first.json()["data"]["ownership_context"], expected)
        self.assertEqual(second.json()["data"]["ownership_context"], expected)
        stored = self.service.repository.get_conversation_context(self.root, raw["id"])
        self.assertTrue(stored["legacy_backfill"])

    async def test_context_create_requires_signed_facade_context(self):
        async with self.client() as client:
            absent = await client.post(
                "/xnobrain/api/runtime/v1/sessions?agent=big-brother",
                json={"ownership_context": self.organization_context()},
            )
            forged = await client.post(
                "/xnobrain/api/runtime/v1/sessions?agent=big-brother",
                json={"ownership_context": self.organization_context()},
                headers={TRUSTED_SUBJECT_HEADER: "attacker"},
            )
            unverified_context = await client.post(
                "/xnobrain/api/runtime/v1/sessions?agent=big-brother",
                json={"ownership_context": self.organization_context()},
                headers={
                    TRUSTED_SUBJECT_HEADER: "user-1",
                    TRUSTED_ORGANIZATION_HEADER: "org-1",
                    TRUSTED_SIGNATURE_HEADER: principal_signature(
                        self.token, "user-1", organization_id="org-1"
                    ),
                },
            )

        self.assertEqual(absent.status_code, 401, absent.text)
        self.assertEqual(forged.status_code, 401, forged.text)
        self.assertEqual(unverified_context.status_code, 403, unverified_context.text)
        self.assertEqual(
            unverified_context.json()["error"]["code"],
            "conversation_context_not_verified",
        )
        self.assertEqual(self.service.list_conversations("big-brother")["conversations"], [])

    async def test_run_rejects_caller_owner_or_payer_fields_before_dispatch(self):
        conversation = self.service.create_conversation("big-brother", {"title": "Personal"})
        conversation_id = conversation["id"]
        with patch.object(
            self.service.conversation_runs.agents,
            "chat_stream",
        ) as chat_stream:
            with self.assertRaisesRegex(Exception, "conflicts") as caught:
                await self.service.start_conversation_run(
                    "big-brother",
                    conversation_id,
                    {
                        "input": "forged sponsored request",
                        "payer_kind": "organization_sponsor",
                    },
                )
        self.assertEqual(caught.exception.code, "conversation_context_conflict")
        chat_stream.assert_not_called()

    def test_repository_rejects_symlinked_context_store(self):
        metadata = self.root / ".xnobrain"
        metadata.mkdir(exist_ok=True)
        outside = Path(self.temporary.name) / "outside-context"
        outside.mkdir()
        (metadata / "conversation-contexts").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(StoreError) as caught:
            self.service.repository.get_conversation_context(self.root, "session-one")
        self.assertEqual(caught.exception.code, "unsafe_conversation_context_store")


if __name__ == "__main__":
    unittest.main()
