"""Reconcile saved personal task executions through the shared team engine."""

import asyncio
import json
import logging
from contextlib import suppress

from ..feature_flags import FEATURE_PERSONAL_ORG_WORKSPACE_EXPERIENCE, enabled
from ..integrations import kanban as kb_adapter
from ..integrations import kanban_delegation as ledger
from ..integrations.personal_task_executions import PersonalTaskExecutionClient
from ..repositories import StoreError
from ..repositories.runtime_update_gate import workspace_activity
from .base import ServiceError
from .personal_resource_definitions import team_definition


class KanbanDelegatedTasks:
    def __init__(self, platform, client=None):
        self.platform = platform
        self.client = client or PersonalTaskExecutionClient()
        self.lock = asyncio.Lock()
        self.wake = asyncio.Event()
        self.worker = None
        self.closed = False

    async def start(self):
        if self.worker is None:
            self.closed = False
            self.worker = asyncio.create_task(
                self._work(), name="xnobrain-personal-task-reconciler"
            )

    async def close(self):
        if self.closed:
            return
        self.closed = True
        if self.worker is not None:
            self.worker.cancel()
            with suppress(asyncio.CancelledError):
                await self.worker
            self.worker = None
        self.wake.clear()
        await self.client.close()

    async def tick(self):
        # Native dispatch must never await Control. One background worker and
        # one coalesced wake-up bound the work queued by a slow/unavailable peer.
        if self.closed:
            return
        if self.worker is None:
            await self.start()
        self.wake.set()

    async def _work(self):
        while True:
            await self.wake.wait()
            self.wake.clear()
            try:
                await asyncio.to_thread(self.platform.cron.reconcile_deliveries)
                await self.reconcile()
            except Exception as error:
                logging.getLogger(__name__).warning(
                    "Personal task reconciliation failed",
                    extra={"error_type": type(error).__name__},
                )

    async def reconcile(self):
        async with self.lock:
            kb = kb_adapter._module()
            slots = asyncio.Semaphore(4)

            async def reconcile_one(slug, task_id):
                async with slots:
                    await self._reconcile_task(slug, task_id)

            for board in kb.list_boards(include_archived=False):
                slug = str(board.get("slug") or "default")
                with kb_adapter.connection(slug) as conn:
                    pending = ledger.actionable(conn)
                await asyncio.gather(*(reconcile_one(slug, item["task_id"]) for item in pending))

    @workspace_activity(lambda self: self.platform.repository.data_dir, independent=True)
    async def _reconcile_task(self, board, task_id):
        kb = kb_adapter._module()
        with kb_adapter.connection(board) as conn:
            current = ledger.binding(conn, task_id)
        if current is None:
            return
        try:
            if not current["creation_complete"]:
                self.platform.kanban.resume_delegated_task_creation(board, task_id)
                return
            if current["status"] in ledger.TERMINAL:
                receipt = json.loads(current["receipt"] or "{}")
            elif current["execution_id"]:
                receipt = await self.client.call(
                    "DELETE" if current["cancel_requested"] else "GET",
                    current["execution_id"],
                )
            elif current["cancel_requested"] and not current["admission_key"]:
                receipt = {"id": None, "status": "cancelled"}
            else:
                if (
                    not enabled(FEATURE_PERSONAL_ORG_WORKSPACE_EXPERIENCE)
                    and not current["admission_key"]
                ):
                    return
                if not current["admission_key"]:
                    team = self.platform.repository.get_team(current["team_id"])
                    if (
                        team_definition(current["team_id"], team)["source_revision"]
                        != current["team_revision"]
                    ):
                        raise ServiceError("Saved team changed", status=409)
                    definition = self.platform.personal_resource_definitions.load(
                        "task",
                        task_id,
                        {"board_id": board},
                    )
                    with kb_adapter.connection(board) as conn:
                        current = ledger.prepare(
                            conn,
                            task_id,
                            board,
                            definition["source_revision"],
                        )
                # The same key/revision survives a lost HTTP response or restart.
                receipt = await self.client.call(
                    "POST",
                    key=current["admission_key"],
                    body={
                        "source_kind": "task",
                        "source_id": task_id,
                        "board_id": board,
                        "source_revision": current["source_revision"],
                        "input": {},
                    },
                )
            with kb_adapter.connection(board) as conn:
                ledger.record(conn, task_id, receipt)
                # Another process may have persisted a newer receipt while the
                # HTTP request was in flight. Project only the durable winner.
                receipt = json.loads(ledger.binding(conn, task_id)["receipt"])
                if receipt["status"] in ledger.TERMINAL:
                    summary = (
                        str(receipt.get("orchestrator_summary") or "Team execution completed")
                        if receipt["status"] == "completed"
                        else "Team execution " + receipt["status"]
                    )
                    kb.complete_task(
                        conn, task_id, summary=summary[:32000], fire_lifecycle_hook=False
                    )
        except (ServiceError, StoreError) as exc:
            with kb_adapter.connection(board) as conn:
                ledger.record_error(conn, task_id, denied=exc.status in {401, 403, 404, 409})
        except (ValueError, KeyError, TypeError):
            with kb_adapter.connection(board) as conn:
                ledger.record_error(conn, task_id)
