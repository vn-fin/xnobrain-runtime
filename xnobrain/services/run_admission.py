"""One durable workspace queue coordinator; accepted runs outlive UI connections."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from contextlib import suppress
from dataclasses import asdict
from datetime import datetime, timezone

from opentelemetry import trace

from ..integrations.run_admission import BOOT_ID, AdmissionClient, admitted_root
from ..repositories.base import StoreError
from ..repositories.custom_page_locks import ConversationLease, ExecutionLease, acquire, release
from ..repositories.run_admission import AdmissionRepository
from ..trusted_context import TrustedRequestContext
from .base import ServiceError


class RunAdmissionService:
    def __init__(self, platform, client=None):
        self.platform = platform
        self.runs = platform.conversation_runs
        self.files = platform.repository
        self.store = AdmissionRepository(self.files.data_dir)
        self.client = client or AdmissionClient()
        self.task = None
        self.guards = {}

    async def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self._loop(), name="agent-capacity-queue")

    async def close(self):
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        await self.client.close()

    def enqueue(self, record, payload, trusted, guard=None):
        from ..integrations.run_admission import requires_immediate

        private = dict(payload)
        for name in ("_agent_maker_principal", "_custom_page_principal"):
            private.pop(name, None)
        private["principal"] = (
            asdict(trusted) if isinstance(trusted, TrustedRequestContext) else None
        )
        private["_record"] = record
        if requires_immediate():
            private["_immediate_boot"] = BOOT_ID
        self.store.enqueue(record, private)
        self.files.put_conversation_run(record)
        self.store.update(record["id"], "waiting", {})
        if guard is not None:
            self.guards[record["id"]] = guard
        return self.waiting(record, "capacity_recovering")

    def waiting(self, record, reason):
        old = record.get("capacity") or {}
        capacity = {
            "state": "waiting",
            "reason_code": reason or "capacity_recovering",
            "queued_at": old.get("queued_at") or datetime.now(timezone.utc).isoformat(),
            "retry_after_ms": 5000,
        }
        if old != capacity:
            record = self.runs._append(
                record,
                {
                    "event": "message",
                    "data": {
                        "event": "run.capacity_waiting",
                        "run_id": record["id"],
                        "capacity": capacity,
                    },
                },
            )
        return record

    async def cancel(self, record):
        # Persist cancellation before attempting the remote acknowledgement.
        record = self.runs._mark_terminal(record, "cancelled", "Queued task cancelled")
        self.guards.pop(record["id"], None)
        return record

    async def _cancel_receipt(self, row):
        admission = row["admission"]
        if not admission:
            try:
                admission = await self.client.observe(row["id"])
            except ServiceError as error:
                if error.status in {400, 404}:
                    return  # Registration never committed.
                raise
        await self.client.receipt(admission, "cancelled")

    async def admit_immediate(self, record):
        try:
            admission = await self.client.register(record["id"], immediate=True)
            self.store.update(record["id"], "waiting", admission)
            if admission["state"] == "offered":
                row = next(row for row in self.store.pending() if row["id"] == record["id"])
                await self._dispatch(row, record, admission)
                if record["id"] in self.runs._active:
                    return self.files.get_conversation_run(
                        record["agent_id"], record["conversation_id"], record["id"]
                    )
        except (ServiceError, StoreError):
            pass
        # This API has no durable wait contract. Persist cancellation before the
        # HTTP rejection; the coordinator only has a cleanup obligation left.
        await self.cancel(record)
        raise ServiceError(
            "New tasks are temporarily unavailable; retry shortly",
            status=503,
            code="capacity_service_unavailable",
        )

    async def _loop(self):
        while True:
            descriptor = None
            try:
                descriptor = acquire(
                    self.files.data_dir, ".agent-capacity-coordinator.lock", shared=False
                )
                for row in self.store.pending():
                    # One unavailable/deleted task must not starve other roots.
                    with trace.get_tracer(__name__).start_as_current_span(
                        "run_admission.reconcile",
                        record_exception=False,
                        set_status_on_exception=False,
                    ):
                        try:
                            await self._tick(row)
                        except (StoreError, ServiceError, OSError, sqlite3.Error) as error:
                            logging.getLogger(__name__).warning(
                                "Run admission reconciliation failed",
                                extra={
                                    "event": "run_admission_reconcile_failed",
                                    "run_id": row["id"],
                                    "error": "reconciliation_failed",
                                    "error_type": type(error).__name__,
                                },
                            )
                from ..integrations.native_admission import reconcile_native

                await asyncio.to_thread(
                    reconcile_native, self.files.data_dir, self._native_source_exists
                )
            except (StoreError, ServiceError, OSError, sqlite3.Error) as error:
                logging.getLogger(__name__).warning(
                    "Run admission queue unavailable",
                    extra={
                        "event": "run_admission_queue_failed",
                        "error": "queue_unavailable",
                        "error_type": type(error).__name__,
                    },
                )
            finally:
                if descriptor is not None:
                    release(descriptor)
            await asyncio.sleep(5)

    def _native_source_exists(self, kind, key):
        """Release deleted/replaced native intents without changing recurrence rules."""
        if kind == "cron":
            _, profile, job_id, occurrence = key.split(":", 3)
            if profile not in self.platform.cron._profiles():
                return False
            jobs = self.platform.cron._native(profile, "list_jobs", True)
            return any(
                str(job["id"]) == job_id and str(job.get("next_run_at", "")) == occurrence
                for job in jobs
            )
        if kind == "kanban":
            from ..integrations import kanban as kb_adapter

            _, board, task_id, run_id, status = key.split(":", 4)
            kb = self.platform.kanban._ready()
            if not kb.board_exists(board):
                return False
            with kb_adapter.connection(board) as connection:
                task = kb.get_task(connection, task_id)
                return (
                    task is not None
                    and str(task.current_run_id) == run_id
                    and str(task.status) == status
                )
        return True

    async def _tick(self, row):
        if row["payload"].get("_kind") == "team":
            await self._tick_team(row)
            return
        admission = row["admission"]
        try:
            record = self.files.get_conversation_run(row["agent"], row["conversation"], row["id"])
        except StoreError:
            if row["state"] == "prepared":
                # Recover the intent committed immediately before the public record.
                record = row["payload"]["_record"]
                self.files.put_conversation_run(record)
                self.store.update(row["id"], "waiting", admission)
                return
            # Deleted workspace content cannot authorize delayed execution.
            await self._cancel_receipt(row)
            self.store.remove(row["id"])
            return
        terminal = record["status"] in {"completed", "failed", "cancelled", "timed_out"}
        if (
            row["payload"].get("_immediate_boot")
            and row["state"] in {"prepared", "waiting"}
            and not terminal
        ):
            if row["payload"]["_immediate_boot"] != BOOT_ID:
                await self.cancel(record)
            return
        try:
            if terminal:
                if admission:
                    state = "finished" if row["state"] == "running" else "cancelled"
                    await self.client.receipt(admission, state)
                else:
                    await self._cancel_receipt(row)
                self.store.remove(row["id"])
                self.guards.pop(row["id"], None)
                return
            if row["state"] == "running":
                await self.client.receipt(admission, "running")
                if row["id"] not in self.runs._active:
                    self.runs._heal_if_stale(record)
                return
            if row["state"] == "starting":
                # A pre-executor crash cannot prove an external side effect did not occur.
                # Conservatively terminate locally and acknowledge, never rerun the root.
                self.runs._mark_terminal(
                    record, "failed", "Task start was interrupted; review before retrying"
                )
                return
            if not admission:
                admission = await self.client.register(row["id"])
                self.store.update(row["id"], "waiting", admission)
            else:
                admission = await self.client.observe(admission["id"])
            if row["state"] == "claiming" and admission["state"] == "starting":
                # The claim may have committed while its response was lost. No executor
                # is started until the separate 'starting' journal write, so abort safely.
                await self.client.receipt(admission, "aborted")
                self.store.update(row["id"], "waiting", {})
                return
            if admission["state"] == "offered":
                await self._dispatch(row, record, admission)
            elif admission["state"] in {"cancelled", "rejected", "finished"}:
                self.runs._mark_terminal(record, "cancelled", "Queued task is no longer eligible")
            else:
                self.waiting(record, admission.get("reason_code"))
        except (ServiceError, StoreError) as error:
            if not terminal:
                if (
                    error.status in {400, 401, 403, 404, 409, 422, 429}
                    and error.code != "capacity_conflict"
                ):
                    self.runs._mark_terminal(record, "failed", str(error))
                else:
                    self.waiting(record, error.code)

    def _validate(self, record, payload, trusted):
        from .conversation_authority import require_binding

        self.platform.runtime_updates.require_dispatch()
        self.platform.agents.get_conversation(record["agent_id"], record["conversation_id"])
        if trusted is not None:
            require_binding(
                self.files,
                record["agent_id"],
                record["conversation_id"],
                trusted,
                active=True,
                expected=record.get("ownership_context") or {},
            )
        guard = self.guards.get(record["id"])
        if guard is not None:
            guard()
        if record.get("custom_page_datasets") is not None:
            pages = self.platform.custom_page
            pages.require_mutation()
            if record.get("custom_page_action"):
                action = record["custom_page_action"]
                pages.action_scope(record["agent_id"], action["id"], action["body"], trusted)
            owner = pages.authority(record["agent_id"], trusted)
            page = pages.repository.read(record["agent_id"], owner)
            if page["status"] != "active" or page["active"] != record["custom_page_revision"]:
                raise ServiceError(
                    "Queued action changed", status=409, code="custom_page_revision_conflict"
                )
            if record.get("custom_page_schedule"):
                schedule = pages.schedules.repository.get(
                    record["agent_id"], owner, record["custom_page_schedule"]
                )
                if schedule["state"] != "running" or schedule.get("run_id") != record["id"]:
                    raise ServiceError(
                        "Schedule is no longer active",
                        status=403,
                        code="custom_page_schedule_inactive",
                    )
        if record.get("ui_assistance"):
            scope = record["ui_assistance"]
            row = self.platform.ui_composition.repository.get(scope["owner"], scope["id"])
            self.platform.ui_composition.context(row["request"], trusted)
            if row["cancelled"]:
                raise ServiceError(
                    "Layout task cancelled", status=403, code="ui_assistance_cancelled"
                )

    async def _dispatch(self, row, record, offer):
        from .conversation_runs import _ActiveConversationRun

        payload = dict(row["payload"])
        payload.pop("_record", None)
        payload.pop("_immediate_boot", None)
        principal = payload.pop("principal", None)
        trusted = TrustedRequestContext(**principal) if principal else None
        self._validate(record, payload, trusted)
        if self.runs.analytics is not None:
            from ..integrations.accounting_context import accounting_enabled

            context = record.get("ownership_context") or {}
            kwargs = (
                {
                    "context_id": str(
                        context.get("organization_id")
                        or context.get("payer_organization_id")
                        or "personal"
                    )
                }
                if accounting_enabled()
                else {}
            )
            await self.runs.analytics.require_execution_budget(record["agent_id"], **kwargs)
        conversation = ConversationLease(
            self.files.data_dir, record["agent_id"], record["conversation_id"]
        )
        lease = None
        registered = False
        try:
            record = self.files.get_conversation_run(row["agent"], row["conversation"], row["id"])
            if record["status"] != "queued":
                return
            self._validate(record, payload, trusted)
            lease = ExecutionLease(self.files.data_dir, record["agent_id"])
            # Persist the offer before the network mutation so a lost response is recoverable.
            self.store.update(row["id"], "claiming", {**offer, "runtime_boot": BOOT_ID})
            admission = await self.client.claim(offer)
            if admission["state"] != "starting":
                self.store.update(row["id"], "waiting", admission)
                self.waiting(record, admission.get("reason_code"))
                return
            self.store.update(row["id"], "starting", admission)
            latest = self.files.get_conversation_run(row["agent"], row["conversation"], row["id"])
            if latest["status"] != "queued":
                await self.client.receipt(admission, "cancelled")
                return
            self._validate(record, payload, trusted)
            if (
                trusted is not None
                and (record.get("ownership_context") or {}).get("owner_kind") == "personal"
            ):
                payload["_custom_page_principal"] = trusted
                if not record.get("ui_assistance") and record.get("custom_page_datasets") is None:
                    payload["_agent_maker_principal"] = trusted
            capacity = {**(record.get("capacity") or {}), "state": "admitted", "reason_code": ""}
            record = self.runs._append(
                record,
                {
                    "event": "message",
                    "data": {
                        "event": "run.capacity_admitted",
                        "run_id": row["id"],
                        "capacity": capacity,
                    },
                },
            )
            entry = _ActiveConversationRun(
                row["id"],
                row["agent"],
                row["conversation"],
                lifecycle_lease=lease,
                conversation_lease=conversation,
            )
            self.runs._active[row["id"]] = entry
            self.runs._by_conversation[(row["agent"], row["conversation"])] = row["id"]
            self.store.update(row["id"], "running", admission)

            async def execute():
                with admitted_root(admission):
                    # Keep an acknowledgement obligation in the journal if Control is down.
                    try:
                        await self.client.receipt(admission, "running")
                    except ServiceError:
                        pass
                    await lease.activity.run(self.runs._drive, record, payload)

            entry.task = asyncio.create_task(execute(), name="conversation-run-" + row["id"])
            entry.task.add_done_callback(lambda _task: self.runs._deregister(row["id"]))
            registered = True
        finally:
            if not registered:
                if lease is not None:
                    lease.close()
                conversation.close()

    def enqueue_team(self, record, team, workflow):
        capacity = {
            "state": "waiting",
            "reason_code": "capacity_recovering",
            "queued_at": datetime.now(timezone.utc).isoformat(),
            "retry_after_ms": 5000,
        }
        record = {**record, "capacity": capacity}
        self.store.enqueue(
            {
                "id": record["id"],
                "agent_id": record["orchestrator_id"],
                "conversation_id": record["team_id"],
            },
            {"_kind": "team", "_record": record, "team": team, "workflow": workflow},
        )
        self.files.put_team_run(record)
        self.store.update(record["id"], "waiting", {})
        return record

    async def _tick_team(self, row):
        teams = self.platform.team_runs
        admission = row["admission"]
        try:
            record = self.files.get_team_run(row["conversation"], row["id"])
        except StoreError:
            if row["state"] == "prepared":
                self.files.put_team_run(row["payload"]["_record"])
                return
            await self._cancel_receipt(row)
            self.store.remove(row["id"])
            return
        if record["status"] not in {"pending", "running"}:
            if admission:
                await self.client.receipt(
                    admission, "finished" if row["state"] == "running" else "cancelled"
                )
            else:
                await self._cancel_receipt(row)
            self.store.remove(row["id"])
            return
        if row["state"] == "running":
            await self.client.receipt(admission, "running")
            teams._heal_if_stale(record)
            return
        if row["state"] == "starting":
            if self.store.team_active(row["id"]):
                return
            await teams._finalize(record, "failed", error="interrupted_by_restart")
            return
        executor_fence = None
        try:
            admission = (
                await self.client.observe(admission["id"])
                if admission
                else await self.client.register(row["id"], "team")
            )
            if row["state"] == "claiming" and admission["state"] == "starting":
                await self.client.receipt(admission, "aborted")
                self.store.update(row["id"], "waiting", {})
                return
            self.store.update(row["id"], "waiting", admission)
            if admission["state"] in {"cancelled", "finished", "rejected"}:
                await teams._finalize(record, "cancelled", error="capacity_no_longer_eligible")
                return
            if admission["state"] != "offered":
                reason = admission.get("reason_code") or "capacity_recovering"
                if record["capacity"]["reason_code"] != reason:
                    record["capacity"]["reason_code"] = reason
                    await teams._persist(record)
                return
            self.platform.runtime_updates.require_dispatch()
            team = self.platform.get_team(record["team_id"])
            if not team.get("enabled", True) or team != row["payload"]["team"]:
                await teams._finalize(record, "cancelled", error="team_changed_while_waiting")
                return
            workflow = row["payload"]["workflow"]
            await teams._require_execution_budgets(team, workflow)
            from ..repositories.runtime_update_gate import WorkspaceActivity

            with WorkspaceActivity(self.files.data_dir):
                executor_fence = self.store.team_fence(row["id"])
                self.store.update(row["id"], "claiming", {**admission, "runtime_boot": BOOT_ID})
                admission = await self.client.claim(admission)
                if admission["state"] != "starting":
                    self.store.update(row["id"], "waiting", admission)
                    return
                self.store.update(row["id"], "starting", admission)
                record = self.files.get_team_run(row["conversation"], row["id"])
                if record["status"] != "pending":
                    await self.client.receipt(admission, "cancelled")
                    return
                record["capacity"]["state"] = "admitted"
                entry = teams._register(record, task=None)
                entry.executor_fence = executor_fence
                executor_fence = None
                self.store.update(row["id"], "running", admission)

                async def execute():
                    with admitted_root(admission):
                        with suppress(ServiceError):
                            await self.client.receipt(admission, "running")
                        await entry.activity.run(teams._drive, record, team, workflow)

                entry.task = asyncio.create_task(execute(), name="team-run-" + row["id"])
                entry.task.add_done_callback(lambda _task: teams._deregister(row["id"]))
        except (ServiceError, StoreError) as error:
            if error.status in {401, 403, 404, 422, 429}:
                await teams._finalize(record, "failed", error=error.code)
            else:
                record["capacity"]["reason_code"] = error.code
                await teams._persist(record)
        finally:
            if executor_fence is not None:
                release(executor_fence)
