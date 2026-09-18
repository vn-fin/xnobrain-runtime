"""Explicit Personal app schedules dispatched by native cron into durable runs."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from ..integrations.custom_page_cron import MARKER, arm_job, install_job, pause_job
from ..integrations.schedule_preview import preview_calendar_runs
from ..models.custom_page import ApprovePageSchedule, SchedulePage, StopPageSchedule, digest
from ..repositories.custom_page import fail
from ..repositories.custom_page_schedules import CustomPageScheduleRepository
from ..trusted_context import TrustedRequestContext
from .custom_page import validated

_EXECUTING = set()


class CustomPageScheduleService:
    def __init__(self, pages):
        self.pages = pages
        self.repository = CustomPageScheduleRepository(pages.repository)
        self.loop = None
        self._executing = _EXECUTING

    @property
    def platform(self):
        return self.pages.platform

    def preview(self, agent, body, trusted):
        owner = self.pages.authority(agent, trusted)
        selected = validated(SchedulePage, body)
        if not selected.get("timezone"):
            selected["timezone"] = self.platform.cron.default_timezone()
        page, action = self.pages.action_scope(agent, selected["action_id"], selected, trusted)
        occurrences = preview_calendar_runs(
            selected["schedule"], selected["timezone"], datetime.now(UTC), count=5
        )
        approval = {
            **selected,
            "action_digest": digest(action),
            "page_digest": page["page"]["digest"],
        }
        return {
            "approval": approval,
            "digest": digest({"owner": owner, **approval}),
            "occurrences": occurrences,
            "dst_policy": "skip_gap_earlier_fold",
            "budget_policy": "existing_personal_admission_each_run",
            "max_model_turns": 20,
        }

    def create(self, agent, body, trusted):
        from ..repositories.custom_page_locks import ExecutionLease

        # Native install/arm writes profile files after SQLite approval. Keep
        # profile removal/archive fenced until both stores have reconciled.
        lease = ExecutionLease(self.pages.repository.files.data_dir, agent)
        try:
            return self._create_bound(agent, body, trusted)
        finally:
            lease.close()

    def _create_bound(self, agent, body, trusted):
        self.pages.require_mutation()
        selected = validated(ApprovePageSchedule, body)
        proposal = self.preview(
            agent, {key: selected[key] for key in SchedulePage.model_fields}, trusted
        )
        if (
            selected["digest"] != proposal["digest"]
            or selected["confirmation"] != "SCHEDULE " + proposal["digest"]
        ):
            fail("custom_page_schedule_approval_required", 403)
        owner = self.pages.authority(agent, trusted)
        binding = self.repository.create(
            agent, owner, proposal["approval"], selected["idempotency_key"], selected["digest"]
        )
        if binding["state"] == "preparing":
            install_job(self.platform.cron, agent, binding, owner)
            self.repository.installed(agent, owner, binding["id"])
        current = self.repository.get(agent, owner, binding["id"])
        if current["state"] == "scheduled" and current["attempts"] == 0:
            arm_job(self.platform.cron, agent, binding["id"])
        elif current["state"] != "scheduled":
            pause_job(self.platform.cron, agent, binding["id"])
        return self.public(current)

    @staticmethod
    def public(binding):
        return {
            key: binding[key]
            for key in (
                "id",
                "approval",
                "digest",
                "state",
                "attempts",
                "occurrence",
                "run_id",
                "last_status",
                "updated_at",
            )
        }

    def list(self, agent, trusted):
        owner = self.pages.authority(agent, trusted)
        return [self.public(binding) for binding in self.repository.list(agent, owner)]

    async def stop(self, agent, identifier, body, trusted):
        self.pages.require_mutation()
        owner = self.pages.authority(agent, trusted)
        selected = validated(StopPageSchedule, body)
        if selected["confirmation"] != "STOP " + identifier:
            fail("custom_page_confirmation_required", 403)
        binding = self.repository.stop(agent, owner, identifier)
        await asyncio.to_thread(pause_job, self.platform.cron, agent, identifier)
        if binding["state"] in {"running", "stopping"} and self.repository.worker_live(binding):
            fail("custom_page_jobs_active")
        last_status = binding.get("last_status") or "stopped"
        if binding["run_id"]:
            runs = self.platform.conversation_runs
            run = runs.get_run(agent, binding["approval"]["conversation_id"], binding["run_id"])
            if run["status"] not in {"completed", "failed", "timed_out", "cancelled"}:
                await runs.cancel_run(agent, run["conversation_id"], run["id"])
            last_status = runs.get_run(agent, run["conversation_id"], run["id"])["status"]
            if run["id"] in runs._active and not runs._active[run["id"]].task.done():
                fail("custom_page_jobs_active")
        if identifier in self._executing:
            fail("custom_page_jobs_active")
        from ..repositories.custom_page_locks import execution_active

        if execution_active(self.pages.repository.files.data_dir, agent):
            fail("custom_page_jobs_active")
        # Stop has proved its exact executor gone; uncertain work is retained until then.
        self.repository.finish(
            agent,
            owner,
            identifier,
            last_status
            if last_status in {"completed", "failed", "timed_out", "cancelled"}
            else "stopped",
        )
        return self.public(self.repository.get(agent, owner, identifier))

    async def execute(self, agent, job):
        """Called only after native claim, with the originating FastAPI event loop."""
        identifier = job["id"]
        if identifier in self._executing:
            return False, "", "", "custom_page_schedule_busy"
        self._executing.add(identifier)
        owner = None
        claimed = False
        binding = None
        run = None
        status = "failed"
        try:
            marker = job.get(MARKER) or {}
            owner = marker.get("owner", "")
            tenant, subject = owner.split("\0", 1)
            trusted = TrustedRequestContext(subject, tenant)
            self.pages.require_mutation()
            self.pages.authority(agent, trusted)
            binding = self.repository.get(agent, owner, identifier)
            approved = binding["approval"]
            if (
                marker.get("digest") != binding["digest"]
                or digest({"owner": owner, **approved}) != binding["digest"]
            ):
                fail("custom_page_schedule_binding_invalid")
            from ..integrations.cron_timezone import validated_schedule_timezone

            if (
                validated_schedule_timezone(job["schedule"]) != approved["timezone"]
                or job["schedule"]["expr"] != approved["schedule"]
                or job.get("script")
                or not job.get("no_agent")
                or job.get("deliver") != "local"
            ):
                fail("custom_page_schedule_binding_invalid")
            page, action = self.pages.action_scope(agent, approved["action_id"], approved, trusted)
            if (
                digest(action) != approved["action_digest"]
                or page["page"]["digest"] != approved["page_digest"]
            ):
                fail("custom_page_schedule_binding_invalid")
            # Native fire_claim timestamp is stable throughout this occurrence.
            occurrence = job["fire_claim"]["at"]
            dispatch = self.repository.claim(agent, owner, identifier, occurrence)
            claimed = True

            def guard():
                latest = self.repository.get(agent, owner, identifier)
                if latest["state"] != "running" or latest["occurrence"] != occurrence:
                    fail("custom_page_schedule_inactive")

            run = await self.pages.run_action(
                agent,
                approved["action_id"],
                {
                    "expected_revision": approved["expected_revision"],
                    "conversation_id": approved["conversation_id"],
                    "idempotency_key": dispatch["idempotency_key"],
                    "confirmation": "RUN " + approved["action_id"],
                    "timeout_seconds": approved["timeout_seconds"],
                },
                trusted,
                dispatch_guard=guard,
                schedule_id=identifier,
            )
            self.repository.attach(agent, owner, identifier, run["id"])
            runs = self.platform.conversation_runs
            entry = runs._active.get(run["id"])
            if entry is not None and entry.task is not None:
                try:
                    # The parent owns the approved deadline and terminal status.
                    # This observer only adds bounded cancellation cleanup grace.
                    await asyncio.wait_for(
                        asyncio.shield(entry.task), timeout=approved["timeout_seconds"] + 5
                    )
                except asyncio.CancelledError:
                    # Stop cancels the parent run, which propagates through
                    # shield without cancelling this observer. Read its durable
                    # terminal outcome instead of relabelling it as failure.
                    if asyncio.current_task().cancelling():
                        raise
                except TimeoutError:
                    current = runs.get_run(agent, run["conversation_id"], run["id"])
                    if current["status"] not in {"completed", "failed", "cancelled", "timed_out"}:
                        await runs.cancel_run(agent, run["conversation_id"], run["id"])
            current = runs.get_run(agent, run["conversation_id"], run["id"])
            status = current["status"]
            if status not in {"completed", "failed", "cancelled", "timed_out"}:
                status = "unknown"
            success = status == "completed"
            return (
                success,
                "Custom page run " + run["id"] + ": " + status,
                "[SILENT]",
                None if success else "custom_page_schedule_run_failed",
            )
        except asyncio.CancelledError:
            if run is not None:
                runs = self.platform.conversation_runs
                current = runs.get_run(agent, run["conversation_id"], run["id"])
                if current["status"] not in {"completed", "failed", "cancelled", "timed_out"}:
                    await runs.cancel_run(agent, run["conversation_id"], run["id"])
                status = runs.get_run(agent, run["conversation_id"], run["id"])["status"]
            raise
        except Exception:
            if run is not None:
                entry = self.platform.conversation_runs._active.get(run["id"])
                if entry and entry.task and not entry.task.done():
                    await self.platform.conversation_runs.cancel_run(
                        agent, run["conversation_id"], run["id"]
                    )
            return False, "", "", "custom_page_schedule_denied"
        finally:
            try:
                if run is not None:
                    entry = self.platform.conversation_runs._active.get(run["id"])
                    if entry and entry.task and not entry.task.done():
                        status = "unknown"
                if not claimed and binding is not None:
                    self.repository.deny(agent, owner, identifier)
                if claimed:
                    self.repository.finish(agent, owner, identifier, status)
                if (
                    not claimed
                    or status != "completed"
                    or self.repository.get(agent, owner, identifier)["state"] != "scheduled"
                ):
                    await asyncio.to_thread(pause_job, self.platform.cron, agent, identifier)
            finally:
                self._executing.discard(identifier)
