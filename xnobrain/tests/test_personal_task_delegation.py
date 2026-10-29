"""Real Hermes persistence and dispatcher seam with a controlled transport."""

import copy
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock, patch

import httpx

from xnobrain.integrations import kanban as kb
from xnobrain.integrations import kanban_delegation as ledger
from xnobrain.integrations.kanban_admission import dispatch_scope
from xnobrain.integrations.kanban_schedules import release_due_schedules
from xnobrain.integrations.personal_task_executions import PersonalTaskExecutionClient
from xnobrain.services.base import ServiceError
from xnobrain.services.kanban_delegated_tasks import KanbanDelegatedTasks
from xnobrain.tests.test_kanban import HERMES_AVAILABLE, HermesKanbanAPITests


class PersonalTaskTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_identity_and_idempotency_use_internal_transport_only(self):
        seen = []

        def respond(request):
            seen.append(request)
            return httpx.Response(200, json={"data": {"id": "pex_one", "status": "running"}})

        with patch.dict(
            os.environ,
            {
                "RUNTIME_CONTROL_URL": "https://control.test",
                "RUNTIME_WORKSPACE_ID": "owner_workspace",
                "RUNTIME_INTERNAL_SERVICE_TOKEN": "test-only-token",
            },
        ):
            client = PersonalTaskExecutionClient(
                httpx.AsyncClient(
                    transport=httpx.MockTransport(respond),
                    follow_redirects=False,
                )
            )
            await client.call("POST", body={"source_kind": "task"}, key="stable_key")
            await client.call("DELETE", "pex_one")
            await client.close()
        self.assertEqual(
            seen[0].url.path, "/xnobrain/api/control/internal/v1/personal/delegated-executions"
        )
        self.assertEqual(seen[0].headers["X-XNOBrain-Workspace-ID"], "owner_workspace")
        self.assertEqual(seen[0].headers["Authorization"], "Bearer test-only-token")
        self.assertEqual(seen[0].headers["Idempotency-Key"], "stable_key")
        self.assertNotIn("Idempotency-Key", seen[1].headers)

    async def test_denial_is_bounded_and_missing_credentials_never_make_request(self):
        def respond(_request):
            return httpx.Response(403, json={"error": "private provider output"})

        client = PersonalTaskExecutionClient(
            httpx.AsyncClient(
                transport=httpx.MockTransport(respond),
            )
        )
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ServiceError) as missing:
                await client.call("POST")
            self.assertEqual(missing.exception.status, 503)
        with patch.dict(
            os.environ,
            {
                "RUNTIME_CONTROL_URL": "https://control.test",
                "RUNTIME_WORKSPACE_ID": "owner_workspace",
                "RUNTIME_INTERNAL_SERVICE_TOKEN": "test-only-token",
            },
        ):
            with self.assertRaises(ServiceError) as denied:
                await client.call("POST")
            self.assertEqual(denied.exception.status, 403)
            self.assertNotIn("private provider output", str(denied.exception))
        await client.close()


@unittest.skipUnless(HERMES_AVAILABLE, "Hermes runtime is not installed")
class PersonalTaskDelegationTests(unittest.IsolatedAsyncioTestCase):
    setUp = HermesKanbanAPITests.setUp
    tearDown = HermesKanbanAPITests.tearDown

    def prepare_team(self):
        service = self.composition.service
        service.kanban.list_boards()
        service.repository.put_team(
            {
                "id": "mixed",
                "name": "Mixed team",
                "orchestrator_id": "own",
                "members": [
                    {
                        "agent_id": "remote_alias",
                        "role": "researcher",
                        "external_agent": {
                            "organization_id": "org_a",
                            "registration_id": "reg_a",
                            "grant_id": "grant_a",
                        },
                    }
                ],
            }
        )
        return service

    def create_task(self, service, **extra):
        return service.kanban.create_task(
            "default",
            {
                "title": "Delegated work",
                "description": "Use the saved team",
                "team_id": "mixed",
                **extra,
            },
        )

    async def test_unknown_admission_reuses_identity_and_never_spawns_local_profile(self):
        service = self.prepare_team()
        task = self.create_task(service)
        self.assertEqual(task["team_id"], "mixed")
        service.kanban.move_task("default", task["id"], "running")
        spawn = Mock()
        with kb.connection("default") as conn:
            with dispatch_scope(kb._module(), service.repository.data_dir, "default"):
                kb._module().dispatch_once(
                    conn, board="default", spawn_fn=spawn, default_assignee="own"
                )
        spawn.assert_not_called()
        client = Mock(
            call=AsyncMock(
                side_effect=[
                    ServiceError("lost response", status=503),
                    {"id": "pex_one", "status": "running", "revision": 1},
                    {
                        "id": "pex_one",
                        "status": "completed",
                        "revision": 2,
                        "orchestrator_summary": "Owner-bound result",
                    },
                ]
            )
        )
        worker = KanbanDelegatedTasks(service, client)
        await worker.reconcile()
        first = copy.deepcopy(client.call.call_args.kwargs)
        # Construct another worker to prove the identity comes from SQLite.
        await KanbanDelegatedTasks(service, client).reconcile()
        self.assertEqual(client.call.call_args.kwargs, first)
        await worker.reconcile()
        self.assertEqual(client.call.call_args.args, ("GET", "pex_one"))
        done = service.kanban.get_task("default", task["id"])
        self.assertEqual(done["kanban_status"], "done")
        self.assertEqual(done["state_detail"]["kind"], "completed")
        self.assertEqual(done["result"], "Owner-bound result")

    async def test_cancel_unknown_admission_waits_for_receipt_then_cancels_same_execution(self):
        service = self.prepare_team()
        task = self.create_task(service)
        service.kanban.move_task("default", task["id"], "running")
        client = Mock(
            call=AsyncMock(
                side_effect=[
                    ServiceError("lost", status=503),
                    {"id": "pex_one", "status": "running"},
                    {"id": "pex_one", "status": "cancelled"},
                ]
            )
        )
        worker = KanbanDelegatedTasks(service, client)
        await worker.reconcile()
        service.kanban.cancel_task("default", task["id"])
        with self.assertRaises(ServiceError):
            service.kanban.archive_task("default", task["id"])
        await worker.reconcile()
        await worker.reconcile()
        self.assertEqual(client.call.call_args.args, ("DELETE", "pex_one"))
        self.assertEqual(
            service.kanban.get_task("default", task["id"])["state_detail"]["kind"], "cancelled"
        )

    async def test_changed_team_or_revoked_grant_does_not_fall_back_to_local_execution(self):
        service = self.prepare_team()
        task = self.create_task(service)
        team = service.repository.get_team("mixed")
        team["members"][0]["external_agent"]["grant_id"] = "new_grant"
        service.repository.put_team(team)
        service.kanban.move_task("default", task["id"], "running")
        client = Mock(call=AsyncMock())
        await KanbanDelegatedTasks(service, client).reconcile()
        client.call.assert_not_called()
        detail = service.kanban.get_task("default", task["id"])
        self.assertEqual(detail["state_detail"]["kind"], "execution_unavailable")
        with self.assertRaises(ServiceError):
            service.kanban.assign_task("default", task["id"], {"assignee": "own"})

    async def test_interval_schedule_copies_binding_once_per_occurrence(self):
        service = self.prepare_team()
        future = datetime.now(timezone.utc) + timedelta(minutes=5)
        task = self.create_task(
            service,
            status="scheduled",
            schedule={
                "recurrence": "interval",
                "scheduled_at": future.isoformat(),
                "interval_minutes": 5,
                "timezone": "Etc/UTC",
            },
        )
        with kb.connection("default") as conn:
            due = release_due_schedules(conn, board="default", now=int(future.timestamp()) + 1)
            self.assertEqual(len(due), 1)
            child = ledger.binding(conn, due[0])
            self.assertEqual(child["team_id"], "mixed")
            self.assertIsNone(child["admission_key"])
            self.assertEqual(
                release_due_schedules(conn, board="default", now=int(future.timestamp()) + 1), []
            )
            self.assertEqual(kb._module().get_task(conn, task["id"]).status, "scheduled")

    async def test_cancel_before_admission(self):
        service = self.prepare_team()
        task = self.create_task(service)
        service.kanban.move_task("default", task["id"], "running")
        service.kanban.cancel_task("default", task["id"])
        client = Mock(call=AsyncMock())
        await KanbanDelegatedTasks(service, client).reconcile()
        client.call.assert_not_called()
        self.assertEqual(
            service.kanban.get_task("default", task["id"])["delegated_execution"]["state"],
            "cancelled",
        )

    async def test_stale_receipt_cannot_reopen_or_replace_execution(self):
        service = self.prepare_team()
        task = self.create_task(service)
        with kb.connection("default") as conn:
            ledger.record(
                conn,
                task["id"],
                {
                    "id": "pex_one",
                    "status": "completed",
                    "revision": 8,
                },
            )
            ledger.record(
                conn,
                task["id"],
                {
                    "id": "pex_one",
                    "status": "running",
                    "revision": 4,
                },
            )
            self.assertEqual(ledger.binding(conn, task["id"])["status"], "completed")
            with self.assertRaises(ValueError):
                ledger.record(
                    conn,
                    task["id"],
                    {
                        "id": "pex_other",
                        "status": "running",
                        "revision": 9,
                    },
                )

    async def test_delegated_create_cannot_rebind_existing_local_task_key(self):
        service = self.prepare_team()
        local = service.kanban.create_task(
            "default",
            {
                "title": "Local",
                "description": "Existing local task",
                "idempotency_key": "same",
            },
        )
        external = self.create_task(service, idempotency_key="same")
        replay = self.create_task(service, idempotency_key="same")
        self.assertNotEqual(local["id"], external["id"])
        self.assertEqual(external["id"], replay["id"])
        with kb.connection("default") as conn:
            self.assertIsNone(ledger.binding(conn, local["id"]))

    async def test_late_receipt_projects_the_newer_persisted_result(self):
        service = self.prepare_team()
        task = self.create_task(service)
        service.kanban.move_task("default", task["id"], "running")

        async def delayed_response(*args, **kwargs):
            with kb.connection("default") as conn:
                ledger.record(
                    conn,
                    task["id"],
                    {
                        "id": "pex_one",
                        "status": "completed",
                        "revision": 3,
                        "orchestrator_summary": "Current result",
                    },
                )
            return {
                "id": "pex_one",
                "status": "completed",
                "revision": 2,
                "orchestrator_summary": "Stale result",
            }

        client = Mock(call=AsyncMock(side_effect=delayed_response))
        await KanbanDelegatedTasks(service, client).reconcile()
        self.assertEqual(service.kanban.get_task("default", task["id"])["result"], "Current result")

    async def test_disabled_feature_blocks_new_admission_but_keeps_active_cancellation(self):
        service = self.prepare_team()
        task = self.create_task(service)
        service.kanban.move_task("default", task["id"], "running")
        client = Mock(call=AsyncMock(return_value={"id": "pex_one", "status": "running"}))
        worker = KanbanDelegatedTasks(service, client)
        with patch.dict(os.environ, {"FT_ENABLE_PERSONAL_ORG_WORKSPACE_EXPERIENCE": "false"}):
            await worker.reconcile()
            client.call.assert_not_called()
            with self.assertRaises(ServiceError):
                self.create_task(service)
        await worker.reconcile()
        service.kanban.cancel_task("default", task["id"])
        client.call.return_value = {"id": "pex_one", "status": "cancelled"}
        with patch.dict(os.environ, {"FT_ENABLE_PERSONAL_ORG_WORKSPACE_EXPERIENCE": "false"}):
            await worker.reconcile()
        self.assertEqual(client.call.call_args.args, ("DELETE", "pex_one"))

    async def test_creation_recovers_after_binding_without_premature_dispatch(self):
        service = self.prepare_team()
        with patch.object(kb, "park_task_in_todo", side_effect=RuntimeError("interrupted")):
            with self.assertRaises(RuntimeError):
                self.create_task(service, idempotency_key="recover-todo")
        with kb.connection("default") as conn:
            pending = ledger.actionable(conn)
            self.assertEqual(len(pending), 1)
            task_id = pending[0]["task_id"]
            self.assertFalse(pending[0]["creation_complete"])
            spawn = Mock()
            with dispatch_scope(kb._module(), service.repository.data_dir, "default"):
                kb._module().dispatch_once(
                    conn, board="default", spawn_fn=spawn, default_assignee="own"
                )
            spawn.assert_not_called()
        client = Mock(call=AsyncMock())
        await KanbanDelegatedTasks(service, client).reconcile()
        client.call.assert_not_called()
        self.assertEqual(service.kanban.get_task("default", task_id)["kanban_status"], "todo")
        service.kanban.move_task("default", task_id, "running")
        replay = self.create_task(service, idempotency_key="recover-todo")
        self.assertEqual(replay["id"], task_id)
        self.assertEqual(replay["kanban_status"], "running")

    async def test_scheduled_creation_recovers_elapsed_intent_once(self):
        service = self.prepare_team()
        future = datetime.now(timezone.utc) + timedelta(minutes=5)
        with patch.object(kb, "put_task_schedule", side_effect=RuntimeError("interrupted")):
            with self.assertRaises(RuntimeError):
                self.create_task(
                    service,
                    status="scheduled",
                    idempotency_key="recover-schedule",
                    schedule={
                        "recurrence": "interval",
                        "scheduled_at": future.isoformat(),
                        "interval_minutes": 5,
                        "timezone": "Etc/UTC",
                    },
                )
        with kb.connection("default") as conn:
            task_id = ledger.actionable(conn)[0]["task_id"]
            self.assertEqual(kb._module().get_task(conn, task_id).status, "scheduled")
        for mutation in (
            lambda: service.kanban.archive_task("default", task_id),
            lambda: service.kanban.move_task("default", task_id, "archived"),
            lambda: service.kanban.patch_task("default", task_id, {"title": "Changed"}),
        ):
            with self.assertRaises(ServiceError) as pending:
                mutation()
            self.assertEqual(pending.exception.status, 409)
        self.assertEqual(service.kanban.get_task("default", task_id)["allowed_kanban_statuses"], [])
        client = Mock(call=AsyncMock())
        with patch(
            "xnobrain.integrations.kanban_schedules.time.time", return_value=future.timestamp() + 60
        ):
            await KanbanDelegatedTasks(service, client).reconcile()
        client.call.assert_not_called()
        with kb.connection("default") as conn:
            self.assertTrue(ledger.binding(conn, task_id)["creation_complete"])
            self.assertEqual(
                kb.task_schedule(conn, task_id)["next_run_at"], int(future.timestamp())
            )
            released = release_due_schedules(
                conn, board="default", now=int(future.timestamp()) + 61
            )
            self.assertEqual(len(released), 1)
            self.assertEqual(
                release_due_schedules(conn, board="default", now=int(future.timestamp()) + 61), []
            )

    async def test_board_archive_waits_for_unknown_admission_and_confirmed_cancellation(self):
        service = self.prepare_team()
        service.kanban.create_board({"slug": "active-board", "name": "Active board"})
        task = service.kanban.create_task(
            "active-board", {"title": "Work", "description": "Saved work", "team_id": "mixed"}
        )
        service.kanban.move_task("active-board", task["id"], "running")
        with self.assertRaises(ServiceError) as active:
            service.kanban.delete_board("active-board")
        self.assertEqual(active.exception.code, "board_execution_active")
        client = Mock(
            call=AsyncMock(
                side_effect=[
                    ServiceError("lost receipt", status=503),
                    {"id": "pex_archive", "status": "running"},
                    {"id": "pex_archive", "status": "cancelled"},
                ]
            )
        )
        worker = KanbanDelegatedTasks(service, client)
        await worker.reconcile()
        service.kanban.cancel_task("active-board", task["id"])
        with self.assertRaises(ServiceError):
            service.kanban.delete_board("active-board")
        await worker.reconcile()
        await worker.reconcile()
        self.assertEqual(service.kanban.delete_board("active-board")["action"], "archived")
        with self.assertRaises(ValueError):
            with kb.connection("active-board"):
                self.fail("A stale connection must not recreate an archived board")
