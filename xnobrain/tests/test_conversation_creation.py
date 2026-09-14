"""Signed creation receipts recover one native session without list heuristics."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from xnobrain.repositories import StoreError
from xnobrain.repositories.conversation_creation import ConversationCreationRepository
from xnobrain.tests import test_conversation_ownership
from xnobrain.trusted_context import TrustedRequestContext


def spawned_replay(root, profiles, data, channel):
    from pathlib import Path

    from xnobrain.app import XNOBrainApplication
    from xnobrain.integrations import AgentManager, GlobalConfigManager

    composition = XNOBrainApplication(
        AgentManager(
            root_profile=Path(root),
            profiles_root=Path(profiles),
            legacy_agents_root=Path(data) / "legacy",
        ),
        GlobalConfigManager(root_profile=Path(root)),
        test_conversation_ownership.FakeRouter(),
    )
    owner = TrustedRequestContext(
        subject="user-1",
        tenant_id="tenant-1",
        ownership_context=ConversationCreationTests.binding(),
    )
    try:
        result = composition.service.create_conversation(
            "big-brother", {"title": "News", "creation_intent": "cint_test"}, owner
        )
        channel.send(result["id"])
    finally:
        channel.close()


class ConversationCreationTests(test_conversation_ownership.ConversationOwnershipTests):
    # Reuse the disposable actual native/session/HTTP fixture; inherited ownership
    # tests also exercise compatibility with existing explicit-context callers.
    @staticmethod
    def binding():
        return {
            "schema_version": 1,
            "id": "cctx_resolved",
            "owner_kind": "personal",
            "payer_kind": "personal",
            "policy_revision_at_create": 1,
            "revocation_version": 1,
            "state": "active",
            "creation_intent": "cint_test",
            "conversation_id": "ses_bound",
        }

    def principal(self, subject="user-1", tenant="tenant-1"):
        return TrustedRequestContext(
            subject=subject, tenant_id=tenant, ownership_context=self.binding()
        )

    def create(self, title="News"):
        return self.service.create_conversation(
            "big-brother", {"title": title, "creation_intent": "cint_test"}, self.principal()
        )

    async def test_signed_control_shape_is_accepted_and_replayed_without_duplicate(self):
        async with self.client() as client:
            headers = self.trusted_headers(context=self.binding())
            body = {"title": "News", "creation_intent": "cint_test"}
            first = await client.post(
                "/xnobrain/api/runtime/v1/sessions?agent=big-brother", json=body, headers=headers
            )
            replay = await client.post(
                "/xnobrain/api/runtime/v1/sessions?agent=big-brother", json=body, headers=headers
            )
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(replay.status_code, 201, replay.text)
        self.assertEqual(first.json()["data"]["id"], "ses_bound")
        self.assertEqual(replay.json()["data"]["id"], "ses_bound")
        self.assertEqual(first.json()["data"]["ownership_context"]["id"], "cctx_resolved")
        self.assertNotIn("creation_intent", first.json()["data"]["ownership_context"])
        self.assertEqual(len(self.service.list_conversations("big-brother")["conversations"]), 1)

    async def test_recovers_native_commit_followed_by_lost_reply(self):
        original = self.service.agents.create_conversation

        def lost(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("lost response")

        with patch.object(self.service.agents, "create_conversation", side_effect=lost):
            with self.assertRaises(OSError):
                self.create()
        with patch.object(self.service.agents, "create_conversation", wraps=original) as create:
            self.assertEqual(self.create()["id"], "ses_bound")
            create.assert_not_called()

    async def test_retry_pending_before_native_insert_and_concurrent_creates(self):
        with patch.object(
            self.service.agents, "create_conversation", side_effect=OSError("interrupted")
        ):
            with self.assertRaises(OSError):
                self.create()
        with ThreadPoolExecutor(max_workers=4) as executor:
            rows = list(executor.map(lambda _: self.create(), range(4)))
        self.assertEqual({row["id"] for row in rows}, {"ses_bound"})
        self.assertEqual(len(self.service.list_conversations("big-brother")["conversations"]), 1)

    async def test_rejects_changed_title_foreign_owner_and_deleted_session(self):
        self.create()
        with self.assertRaisesRegex(Exception, "conflicts"):
            self.create("Changed title")
        for principal in (self.principal(subject="other"), self.principal(tenant="other")):
            with self.assertRaisesRegex(Exception, "conflicts"):
                self.service.create_conversation(
                    "big-brother", {"title": "News", "creation_intent": "cint_test"}, principal
                )
        self.service.delete_conversation("big-brother", "ses_bound")
        with self.assertRaisesRegex(Exception, "deleted"):
            self.create()
        self.assertEqual(self.service.list_conversations("big-brother")["conversations"], [])

    async def test_rejects_missing_binding_and_unsafe_receipt(self):
        with self.assertRaisesRegex(Exception, "verified"):
            self.service.create_conversation(
                "big-brother", {"creation_intent": "forged"}, self.principal()
            )
        repo = ConversationCreationRepository(
            self.service.repository, self.root, "big-brother", "ses_bound"
        )
        target = self.root / "not-a-receipt"
        target.write_text("private data")
        with repo.locked():
            repo.path.symlink_to(target)
        with self.assertRaisesRegex(Exception, "unsafe"):
            self.create()
        self.assertEqual(target.read_text(), "private data")

    async def test_pending_receipt_survives_a_new_repository_instance_and_stops_on_maintenance(
        self,
    ):
        from xnobrain.repositories import FileRepository

        with patch.object(
            self.service.agents, "create_conversation", side_effect=OSError("interrupted")
        ):
            with self.assertRaises(OSError):
                self.create()
        self.service.repository = FileRepository(self.service.repository.data_dir, self.profiles)
        self.assertEqual(self.create()["id"], "ses_bound")
        root = self.service.repository.data_dir / "runtime-updates"
        root.mkdir(exist_ok=True)
        (root / "maintenance.json").write_text(
            '{"dispatch_paused": true, "operation_id": "test", "generation": 1, "target": {}}'
        )
        with self.assertRaisesRegex(Exception, "draining"):
            self.create()

    async def test_actual_postgres_control_signed_wire_when_supplied(self):
        import json
        import os
        from pathlib import Path

        from xnobrain.trusted_context import (
            TRUSTED_CONVERSATION_CONTEXT_HEADER,
            TRUSTED_CONVERSATION_CONTEXT_SIGNATURE_HEADER,
            TRUSTED_SIGNATURE_HEADER,
            TRUSTED_SUBJECT_HEADER,
            TRUSTED_TENANT_HEADER,
            principal_signature,
        )

        source = os.getenv("XNOBRAIN_CREATION_FIXTURE_INPUT")
        if not source:
            self.skipTest("Control-generated signed wire fixture not supplied")
        fixture = json.loads(Path(source).read_text())
        headers = {
            TRUSTED_SUBJECT_HEADER: fixture["subject"],
            TRUSTED_TENANT_HEADER: fixture["tenant"],
            TRUSTED_SIGNATURE_HEADER: principal_signature(
                fixture["token"], fixture["subject"], fixture["tenant"]
            ),
            TRUSTED_CONVERSATION_CONTEXT_HEADER: fixture["encoded"],
            TRUSTED_CONVERSATION_CONTEXT_SIGNATURE_HEADER: fixture["signature"],
        }
        with patch.dict(os.environ, {"RUNTIME_INTERNAL_SERVICE_TOKEN": fixture["token"]}):
            async with self.client() as client:
                for _attempt in range(2):
                    response = await client.post(
                        "/xnobrain/api/runtime/v1/sessions?agent=big-brother",
                        json=fixture["body"],
                        headers=headers,
                    )
                    self.assertEqual(response.status_code, 201, response.text)
                    data = response.json()["data"]
                    self.assertEqual(data["id"], fixture["context"]["conversation_id"])
                    self.assertEqual(data["ownership_context"]["id"], fixture["context"]["id"])
        self.assertEqual(len(self.service.list_conversations("big-brother")["conversations"]), 1)

    async def test_independent_process_recovers_pending_native_commit(self):
        import multiprocessing

        original = self.service.agents.create_conversation

        def interrupted(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("lost reply")

        with patch.object(self.service.agents, "create_conversation", side_effect=interrupted):
            with self.assertRaises(OSError):
                self.create()
        process_context = multiprocessing.get_context("spawn")
        parent, child = process_context.Pipe()
        process = process_context.Process(
            target=spawned_replay,
            args=(str(self.root), str(self.profiles), str(self.service.repository.data_dir), child),
        )
        process.start()
        child.close()
        try:
            self.assertTrue(parent.poll(15), "recovery process timed out")
            self.assertEqual(parent.recv(), "ses_bound")
            process.join(15)
            self.assertEqual(process.exitcode, 0)
            self.assertEqual(
                len(self.service.list_conversations("big-brother")["conversations"]), 1
            )
        finally:
            if process.is_alive():
                process.terminate()
                process.join(5)
            parent.close()

    async def test_removed_replaced_profile_cannot_replay_old_creation(self):
        import shutil

        self.create()
        old = self.root.with_name("old-profile")
        self.root.rename(old)
        shutil.copytree(old, self.root)
        with self.assertRaisesRegex(Exception, "conflicts"):
            self.create()

    async def test_nonempty_lock_file_is_not_excluded_from_update_integrity(self):
        from xnobrain.repositories.runtime_update_gate import (
            is_coordination_file,
            validate_coordination_file,
        )

        repo = ConversationCreationRepository(
            self.service.repository, self.root, "big-brother", "ses_bound"
        )
        path = self.service.repository.data_dir / f".conversation-creation-{repo.lock_key}.lock"
        with repo.locked():
            self.assertTrue(is_coordination_file(path, self.service.repository.data_dir))
        path.write_text("not coordination")
        with self.assertRaises(StoreError):
            validate_coordination_file(path)

    async def test_root_profile_runs_and_custom_pages_use_real_writer_presence(self):
        from unittest.mock import AsyncMock

        from xnobrain.services.base import ServiceError
        from xnobrain.tests.test_custom_page import news_manifest

        self.create()
        with patch.object(self.service.conversation_runs, "start_run", new=AsyncMock()) as start:
            with self.assertRaises(ServiceError) as denied:
                await self.service.start_conversation_run(
                    "big-brother",
                    "ses_bound",
                    {"input": "No tenant crossing"},
                    self.principal(tenant="different"),
                )
            self.assertEqual(denied.exception.code, "conversation_owner_forbidden")
            self.assertEqual(denied.exception.status, 403)
            start.assert_not_called()
        self.assertEqual(self.service.repository.live_profile_path("big-brother"), self.root)
        caps = self.service.custom_page.capabilities("big-brother", self.principal())
        self.assertIn("prepare", caps["operations"])
        self.assertNotIn("remove_agent", caps["operations"])
        draft = self.service.custom_page.prepare(
            "big-brother",
            {
                "manifest": news_manifest(),
                "expected_revision": 0,
                "idempotency_key": "root-page",
            },
            self.principal(),
        )
        self.assertEqual(draft["revision"], 1)
        with self.assertRaisesRegex(Exception, "protected"):
            self.service.custom_page.repository.remove_agent(
                "big-brother", "tenant-1\0user-1", 0, lambda: self.fail("protected profile deleted")
            )
        self.assertFalse((self.profiles / "big-brother").exists())
