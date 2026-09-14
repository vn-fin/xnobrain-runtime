"""Real native cron store/claim/history driving approved app runs, fake inference."""

from __future__ import annotations

import asyncio
import copy
import multiprocessing
import unittest
from pathlib import Path
from unittest.mock import patch

from xnobrain.integrations.custom_page_cron import dispatch_scope, install_executor
from xnobrain.repositories import StoreError
from xnobrain.services.cron import CronService, CronServiceError
from xnobrain.services.custom_page_schedules import CustomPageScheduleService
from xnobrain.tests import test_custom_page_actions as action_fixtures
from xnobrain.trusted_context import TrustedRequestContext


def hold_schedule_dispatch(root, identifier, channel):
    """Independent interpreter, no inherited executing set or coroutine state."""
    from types import SimpleNamespace

    cron = SimpleNamespace(repository=SimpleNamespace(data_dir=Path(root)))
    with dispatch_scope(cron, "research", identifier) as admitted:
        channel.send(admitted)
        channel.recv()
    channel.send("released")


class CustomPageScheduleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await action_fixtures.CustomPageActionTests.asyncSetUp(self)
        self.agents.profile_path = lambda _agent: self.profile
        self.agents.list_agents = lambda **_kwargs: [{"id": "research"}]
        self.platform.cron = CronService(self.files, self.agents)
        self.schedules = CustomPageScheduleService(self.service)
        self.service.schedules = self.schedules
        self.schedules.loop = asyncio.get_running_loop()
        self.platform.cron.custom_page_schedules = self.schedules
        self.selection = {
            "expected_revision": 1,
            "action_id": "analyze",
            "conversation_id": "session",
            "schedule": "0 9 * * *",
            "timezone": "Asia/Ho_Chi_Minh",
            "payer_kind": "personal",
            "timeout_seconds": 20,
            "max_runs": 2,
        }
        self.env = patch.dict(
            "os.environ",
            {
                "HERMES_HOME": str(self.profile),
                "HERMES_ROOT_PROFILE": str(self.profile),
                "RUNTIME_ACCOUNTING_MODE": "legacy",
            },
        )
        self.env.start()

    async def asyncTearDown(self):
        self.env.stop()
        await action_fixtures.CustomPageActionTests.asyncTearDown(self)

    def approve(self, key="schedule-once"):
        proposal = self.schedules.preview("research", self.selection, self.trusted)
        body = {
            **self.selection,
            "idempotency_key": key,
            "digest": proposal["digest"],
            "confirmation": "SCHEDULE " + proposal["digest"],
        }
        return self.schedules.create("research", body, self.trusted), body

    async def fire(self, identifier):
        self.platform.cron._native("research", "trigger_job", identifier)
        return await asyncio.to_thread(self.platform.cron.fire_due, "research", identifier)

    async def test_preview_no_writes_exact_approval_native_execution_and_limit(self):
        preview = self.schedules.preview("research", self.selection, self.trusted)
        self.assertEqual(len(preview["occurrences"]), 5)
        self.assertTrue(
            all(row["timezone"] == "Asia/Ho_Chi_Minh" for row in preview["occurrences"])
        )
        self.assertEqual(self.schedules.list("research", self.trusted), [])
        self.assertFalse((self.profile / "cron" / "jobs.json").exists())
        binding, body = self.approve()
        replay = self.schedules.create("research", body, self.trusted)
        self.assertEqual(replay["id"], binding["id"])
        self.assertEqual(len(self.platform.cron._native("research", "list_jobs", True)), 1)
        self.assertEqual(self.analytics.calls, [])
        with self.assertRaises(StoreError):
            self.schedules.create("research", {**body, "timezone": "Etc/UTC"}, self.trusted)
        self.agents.release.set()
        for number in (1, 2):
            self.assertTrue(await self.fire(binding["id"]))
            current = self.schedules.repository.get("research", "tenant\0owner", binding["id"])
            self.assertEqual(current["attempts"], number)
            self.assertEqual(current["last_status"], "completed")
            run = self.files.get_conversation_run("research", "session", current["run_id"])
            self.assertEqual(run["ownership_context"]["payer_kind"], "personal")
            self.assertEqual(run["custom_page_schedule"], binding["id"])
        self.assertEqual(current["state"], "stopped")
        native = self.platform.cron._native("research", "get_job", binding["id"])
        self.assertFalse(native["enabled"])
        self.assertTrue(native["no_agent"])
        self.assertIsNone(native["script"])
        self.assertEqual(self.analytics.calls, ["research", "research"])
        # Even a native/manual re-enable cannot expand the durable occurrence cap.
        await self.fire(binding["id"])
        self.assertEqual(self.analytics.calls, ["research", "research"])
        from cron import executions
        from hermes_constants import reset_hermes_home_override, set_hermes_home_override

        token = set_hermes_home_override(str(self.profile))
        try:
            attempts = executions.list_executions(job_id=binding["id"])
        finally:
            reset_hermes_home_override(token)
        self.assertGreaterEqual(len(attempts), 2)
        self.assertEqual(sum(row["status"] == "completed" for row in attempts), 2)

    async def test_stop_during_budget_admission_prevents_dispatch_and_replay(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def budget(_agent):
            entered.set()
            await release.wait()

        self.analytics.require_execution_budget = budget
        binding, _ = self.approve()
        firing = asyncio.create_task(self.fire(binding["id"]))
        await asyncio.wait_for(entered.wait(), 3)
        with self.assertRaisesRegex(StoreError, "jobs active"):
            await self.schedules.stop(
                "research", binding["id"], {"confirmation": "STOP " + binding["id"]}, self.trusted
            )
        release.set()
        await asyncio.wait_for(firing, 3)
        self.assertEqual(self.files.list_conversation_runs("research", "session"), [])
        stopped = await self.schedules.stop(
            "research", binding["id"], {"confirmation": "STOP " + binding["id"]}, self.trusted
        )
        self.assertEqual(stopped["state"], "stopped")

    async def test_stop_cancels_only_owned_run_archive_then_delete(self):
        binding, _ = self.approve()
        firing = asyncio.create_task(self.fire(binding["id"]))
        for _ in range(300):
            current = self.schedules.repository.get("research", "tenant\0owner", binding["id"])
            if current["run_id"]:
                break
            await asyncio.sleep(0.01)
        self.assertTrue(current["run_id"])
        with self.assertRaisesRegex(StoreError, "jobs active"):
            self.service.archive(
                "research",
                {"expected_revision": 1, "confirmation": "ARCHIVE research"},
                self.trusted,
            )
        try:
            await self.schedules.stop(
                "research", binding["id"], {"confirmation": "STOP " + binding["id"]}, self.trusted
            )
        except StoreError as error:
            self.assertEqual(error.code, "custom_page_jobs_active")
        await asyncio.wait_for(firing, 3)
        finished = self.schedules.repository.get("research", "tenant\0owner", binding["id"])
        self.assertEqual(finished["last_status"], "cancelled")
        await self.schedules.stop(
            "research", binding["id"], {"confirmation": "STOP " + binding["id"]}, self.trusted
        )
        self.assertEqual(
            self.runs.get_run("research", "session", current["run_id"])["status"], "cancelled"
        )
        stopped = self.schedules.repository.get("research", "tenant\0owner", binding["id"])
        self.assertEqual(stopped["last_status"], "cancelled")
        self.assertEqual(stopped["state"], "stopped")
        self.service.archive(
            "research", {"expected_revision": 1, "confirmation": "ARCHIVE research"}, self.trusted
        )
        self.service.delete(
            "research", {"expected_revision": 1, "confirmation": "DELETE research"}, self.trusted
        )
        await self.fire(binding["id"])
        self.assertFalse((self.files.data_dir / "agent-apps" / "research").exists())
        self.assertEqual(self.analytics.calls, ["research"])

    async def test_changed_revision_timezone_or_unknown_claim_never_executes(self):
        binding, _ = self.approve()
        original = self.platform.cron._native("research", "get_job", binding["id"])
        for changed in ("zone", "digest"):
            job = copy.deepcopy(original)
            job["fire_claim"] = {"at": "2026-09-13T00:00:00Z"}
            if changed == "zone":
                job["schedule"]["xnobrain_time"]["timezone"] = "Etc/UTC"
            else:
                job["xnobrain_custom_page"]["digest"] = "sha256:" + "0" * 64
            result = await self.schedules.execute("research", job)
            self.assertFalse(result[0])
        binding, _ = self.approve(key="another-approved-schedule")
        self.schedules.repository.claim("research", "tenant\0owner", binding["id"], "abandoned")
        self.schedules = CustomPageScheduleService(self.service)
        self.schedules.loop = asyncio.get_running_loop()
        self.platform.cron.custom_page_schedules = self.schedules
        await self.fire(binding["id"])
        self.assertEqual(self.analytics.calls, [])
        self.assertEqual(
            self.schedules.repository.get("research", "tenant\0owner", binding["id"])["state"],
            "running",
        )
        with self.assertRaisesRegex(StoreError, "jobs active"):
            self.service.archive(
                "research",
                {"expected_revision": 1, "confirmation": "ARCHIVE research"},
                self.trusted,
            )
        stopped = await self.schedules.stop(
            "research", binding["id"], {"confirmation": "STOP " + binding["id"]}, self.trusted
        )
        self.assertEqual(stopped["state"], "stopped")

    async def test_reserved_jobs_cannot_fall_back_to_unrestricted_execution(self):
        from cron import scheduler

        install_executor()
        result = scheduler.run_job({"id": "xcp_orphan", "prompt": "ignored"})
        self.assertFalse(result[0])
        binding, _ = self.approve()
        for method, args in (("request_run", ()), ("set_enabled", (True,)), ("delete_job", ())):
            with self.assertRaises(CronServiceError):
                getattr(self.platform.cron, method)(binding["id"], *args, agent_id="research")
        with self.assertRaises(StoreError):
            self.schedules.preview(
                "research", self.selection, TrustedRequestContext("other", "tenant")
            )
        for change in (
            {"timezone": "UTC+7"},
            {"payer_kind": "organization"},
            {"max_runs": 100000},
            {"schedule": "every 1m"},
        ):
            with self.assertRaises(ValueError):
                self.schedules.preview("research", {**self.selection, **change}, self.trusted)

    async def test_stale_native_fire_is_rejected_and_archive_disarms_future_work(self):
        binding, _ = self.approve()
        self.agents.release.set()
        self.assertTrue(await self.fire(binding["id"]))
        self.assertFalse(
            await asyncio.to_thread(self.platform.cron.fire_due, "research", binding["id"])
        )
        self.assertEqual(self.analytics.calls, ["research"])
        self.service.archive(
            "research", {"expected_revision": 1, "confirmation": "ARCHIVE research"}, self.trusted
        )
        self.assertFalse(
            self.platform.cron._native("research", "get_job", binding["id"])["enabled"]
        )
        self.assertEqual(self.schedules.list("research", self.trusted)[0]["state"], "stopped")
        self.assertIn("schedules", self.service.export("research", self.trusted))

    async def test_schema_change_or_context_revocation_blocks_next_occurrence(self):
        binding, _ = self.approve()
        updated = self.service.prepare(
            "research",
            {"expected_revision": 1, "manifest": self.manifest, "idempotency_key": "next-revision"},
            self.trusted,
        )
        self.service.activate(
            "research",
            {
                "expected_revision": 1,
                "revision": updated["revision"],
                "digest": updated["digest"],
                "confirmation": "ACTIVATE " + updated["digest"],
            },
            self.trusted,
        )
        await self.fire(binding["id"])
        self.assertEqual(self.analytics.calls, [])
        self.assertFalse(
            self.platform.cron._native("research", "get_job", binding["id"])["enabled"]
        )

    async def test_live_foreign_executor_is_not_mistaken_for_quiescence(self):
        binding, _ = self.approve()
        self.schedules.repository.claim(
            "research", "tenant\0owner", binding["id"], "owned-elsewhere"
        )
        with patch.object(self.schedules.repository, "worker_live", return_value=True):
            with self.assertRaisesRegex(StoreError, "jobs active"):
                await self.schedules.stop(
                    "research",
                    binding["id"],
                    {"confirmation": "STOP " + binding["id"]},
                    self.trusted,
                )
        self.assertEqual(
            self.schedules.repository.get("research", "tenant\0owner", binding["id"])["state"],
            "stopping",
        )
        with self.assertRaisesRegex(StoreError, "jobs active"):
            self.service.archive(
                "research",
                {"expected_revision": 1, "confirmation": "ARCHIVE research"},
                self.trusted,
            )

    async def test_signed_api_preview_create_stop_and_unknown_fields(self):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from xnobrain.handlers import APIHandlers
        from xnobrain.routes import setup_routes
        from xnobrain.trusted_context import principal_signature

        app = FastAPI()
        self.platform.custom_page = self.service
        setup_routes(app, APIHandlers(self.platform))
        base = "/xnobrain/api/runtime/v1/agents/research/custom-page/schedules"
        headers = {
            "x-xnobrain-verified-subject": "owner",
            "x-xnobrain-verified-tenant": "tenant",
            "x-xnobrain-principal-signature": principal_signature("test-key", "owner", "tenant"),
        }
        with patch.dict("os.environ", {"RUNTIME_INTERNAL_SERVICE_TOKEN": "test-key"}):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                self.assertEqual((await client.get(base)).status_code, 401)
                preview = await client.post(base + "/preview", headers=headers, json=self.selection)
                self.assertEqual(preview.status_code, 200, preview.text)
                self.assertFalse((self.profile / "cron" / "jobs.json").exists())
                approved = await client.post(
                    base,
                    headers=headers,
                    json={
                        **self.selection,
                        "idempotency_key": "schedule-api-1",
                        "digest": preview.json()["data"]["digest"],
                        "confirmation": "SCHEDULE " + preview.json()["data"]["digest"],
                    },
                )
                self.assertEqual(approved.status_code, 201, approved.text)
                binding = approved.json()["data"]
                self.assertNotIn("worker_pid", binding)
                invalid = await client.post(
                    base + "/preview", headers=headers, json={**self.selection, "script": "unsafe"}
                )
                self.assertEqual(invalid.status_code, 422)
                stopped = await client.post(
                    base + "/" + binding["id"] + "/stop",
                    headers=headers,
                    json={"confirmation": "STOP " + binding["id"]},
                )
                self.assertEqual(stopped.status_code, 200, stopped.text)
                self.assertEqual(stopped.json()["data"]["state"], "stopped")
        self.assertEqual(self.analytics.calls, [])

    async def test_partial_install_retry_is_idempotent_and_revoked_context_denied(self):
        with patch(
            "xnobrain.services.custom_page_schedules.install_job",
            side_effect=OSError("interrupted"),
        ):
            with self.assertRaises(OSError):
                self.approve()
        preparing = self.schedules.list("research", self.trusted)[0]
        self.assertEqual(preparing["state"], "preparing")
        binding, _ = self.approve()
        self.assertEqual(binding["id"], preparing["id"])
        self.assertEqual(len(self.platform.cron._native("research", "list_jobs", True)), 1)
        context_path = self.files._conversation_context_path(self.profile, "session")
        context = self.files.get_conversation_context(self.profile, "session")
        self.files.atomic_json(context_path, {**context, "state": "revoked"})
        await self.fire(binding["id"])
        self.assertEqual(self.analytics.calls, [])
        self.assertEqual(self.schedules.list("research", self.trusted)[0]["state"], "stopped")

    async def test_scoped_schedule_tools_recheck_stop_after_run_is_bound(self):
        import json
        from types import SimpleNamespace

        from tools.registry import registry

        from xnobrain.integrations.custom_page_tools import bind_run, install_tools

        binding, _ = self.approve()
        self.schedules.repository.claim(
            "research", "tenant\0owner", binding["id"], "2026-09-13T00:00:00Z"
        )
        run = {
            "id": "run_" + "e" * 32,
            "agent_id": "research",
            "conversation_id": "session",
            "status": "running",
            "ownership_context": self.platform._personal_context(),
            "actor_user_id": "owner",
            "actor_tenant_id": "tenant",
            "custom_page_schedule": binding["id"],
            "custom_page_datasets": ["articles"],
            "custom_page_revision": 1,
        }
        self.files.put_conversation_run(run)
        self.schedules.repository.attach("research", "tenant\0owner", binding["id"], run["id"])
        with bind_run(self.service, "research", "session", run["id"], self.trusted):
            agent = SimpleNamespace(tools=[], valid_tool_names=set())
            install_tools(agent, "session")
            self.assertNotIn("custom_page_prepare", agent.valid_tool_names)
            self.assertEqual(agent.max_iterations, 20)
            accepted = json.loads(
                registry.dispatch("custom_page_query", {"query_id": "latest"}, session_id="session")
            )
            self.assertTrue(accepted["success"])
            self.schedules.repository.stop("research", "tenant\0owner", binding["id"])
            denied = json.loads(
                registry.dispatch("custom_page_query", {"query_id": "latest"}, session_id="session")
            )
            self.assertFalse(denied["success"])

    async def test_crash_after_native_install_resumes_exact_preparing_binding(self):
        with patch.object(
            self.schedules.repository, "installed", side_effect=OSError("interrupted")
        ):
            with self.assertRaises(OSError):
                self.approve()
        binding = self.schedules.list("research", self.trusted)[0]
        self.assertEqual(binding["state"], "preparing")
        self.assertFalse(
            self.platform.cron._native("research", "get_job", binding["id"])["enabled"]
        )
        replay, _ = self.approve()
        self.assertEqual(replay["id"], binding["id"])
        self.assertTrue(self.platform.cron._native("research", "get_job", binding["id"])["enabled"])
        self.assertEqual(len(self.platform.cron._native("research", "list_jobs", True)), 1)

    async def test_native_schedule_install_holds_lifecycle_gate_until_armed(self):
        from xnobrain.repositories.custom_page_locks import lifecycle_gate
        from xnobrain.services.custom_page_schedules import install_job

        observed = []

        def install(*args, **kwargs):
            with self.assertRaises(StoreError) as caught:
                with lifecycle_gate(self.files.data_dir, "research"):
                    self.fail("native installation had no lifecycle fence")
            observed.append(caught.exception.code)
            return install_job(*args, **kwargs)

        with patch("xnobrain.services.custom_page_schedules.install_job", side_effect=install):
            binding, _body = self.approve()
        self.assertEqual(observed, ["custom_page_jobs_active"])
        self.assertEqual(binding["state"], "scheduled")
        with lifecycle_gate(self.files.data_dir, "research"):
            pass

    async def test_foreign_dispatch_fences_expired_claim_without_mutating_schedule(self):
        from xnobrain.integrations.custom_page_cron import store
        from xnobrain.repositories.custom_page_locks import lifecycle_gate
        from xnobrain.repositories.runtime_update_gate import checkpoint_gate

        binding, _ = self.approve()
        identifier = binding["id"]
        self.platform.cron._native("research", "trigger_job", identifier)
        with store(self.platform.cron, "research") as jobs, jobs._jobs_lock():
            rows = jobs.load_jobs()
            rows[0]["fire_claim"] = {"at": "2020-01-01T00:00:00+00:00", "by": "prior-worker"}
            jobs.save_jobs(rows)
        before = self.platform.cron._native("research", "get_job", identifier)
        context = multiprocessing.get_context("spawn")
        channel, child = context.Pipe()
        process = context.Process(
            target=hold_schedule_dispatch,
            args=(str(self.files.data_dir), identifier, child),
        )
        process.start()
        try:
            self.assertTrue(await asyncio.to_thread(channel.poll, 10))
            self.assertTrue(channel.recv())
            for gate in (
                lambda: lifecycle_gate(self.files.data_dir, "research"),
                lambda: checkpoint_gate(self.files.data_dir),
            ):
                with self.assertRaises(StoreError) as caught, gate():
                    self.fail("native finalization has no lifecycle fence")
                self.assertEqual(caught.exception.code, "custom_page_jobs_active")
            self.assertFalse(
                await asyncio.to_thread(self.platform.cron.fire_due, "research", identifier)
            )
            self.assertEqual(self.platform.cron._native("research", "get_job", identifier), before)
            self.assertEqual(self.analytics.calls, [])
            self.assertEqual(
                self.schedules.repository.get("research", "tenant\0owner", identifier)["attempts"],
                0,
            )
            channel.send("release")
            self.assertTrue(await asyncio.to_thread(channel.poll, 10))
            self.assertEqual(channel.recv(), "released")
            await asyncio.to_thread(process.join, 10)
            self.assertEqual(process.exitcode, 0)
        finally:
            if process.is_alive():
                process.terminate()
            await asyncio.to_thread(process.join, 10)
            channel.close()
            child.close()
        self.agents.release.set()
        self.assertTrue(
            await asyncio.to_thread(self.platform.cron.fire_due, "research", identifier)
        )
        self.assertEqual(self.analytics.calls, ["research"])
        current = self.schedules.repository.get("research", "tenant\0owner", identifier)
        self.assertEqual(current["attempts"], 1)
        self.assertEqual(current["state"], "scheduled")

    async def test_finalization_is_fenced_after_async_executor_returns(self):
        from cron import scheduler

        from xnobrain.integrations.custom_page_cron import store

        binding, _ = self.approve()
        identifier = binding["id"]
        self.agents.release.set()
        original = scheduler.mark_job_run
        observed = []

        def mark(job_id, *args, **kwargs):
            self.assertNotIn(identifier, self.schedules._executing)
            self.assertEqual(
                self.schedules.repository.get("research", "tenant\0owner", identifier)["state"],
                "scheduled",
            )
            # Simulate clock skew / a slow native finalizer exceeding even the
            # claim TTL. A second delivery must not create/finish another claim.
            with store(self.platform.cron, "research") as jobs, jobs._jobs_lock():
                rows = jobs.load_jobs()
                rows[0]["fire_claim"]["at"] = "2020-01-01T00:00:00+00:00"
                rows[0]["next_run_at"] = "2020-01-01T00:00:00+00:00"
                jobs.save_jobs(rows)
            before = self.platform.cron._native("research", "get_job", identifier)
            observed.append(self.platform.cron.fire_due("research", identifier))
            self.assertEqual(self.platform.cron._native("research", "get_job", identifier), before)
            return original(job_id, *args, **kwargs)

        with patch.object(scheduler, "mark_job_run", side_effect=mark):
            self.assertTrue(await self.fire(identifier))
        self.assertEqual(observed, [False])
        self.assertEqual(self.analytics.calls, ["research"])
        self.assertEqual(
            self.schedules.repository.get("research", "tenant\0owner", identifier)["attempts"], 1
        )
        self.assertTrue(self.platform.cron._native("research", "get_job", identifier)["enabled"])

    async def test_native_claim_floor_exceeds_bridge_deadline_and_preserves_ordinary_jobs(self):
        from datetime import UTC, datetime, timedelta

        from xnobrain.integrations.custom_page_cron import (
            EXECUTION_WAIT_SECONDS,
            FIRE_CLAIM_TTL_SECONDS,
            store,
        )

        self.assertGreater(FIRE_CLAIM_TTL_SECONDS, EXECUTION_WAIT_SECONDS)
        self.assertGreater(EXECUTION_WAIT_SECONDS, 300)
        binding, _ = self.approve()
        self.platform.cron._native("research", "trigger_job", binding["id"])
        with store(self.platform.cron, "research") as jobs, jobs._jobs_lock():
            rows = jobs.load_jobs()
            rows[0]["fire_claim"] = {
                "at": (datetime.now(UTC) - timedelta(seconds=310)).isoformat(),
                "by": "worker",
            }
            jobs.save_jobs(rows)
            install_executor()
            self.assertFalse(jobs.claim_job_for_fire(binding["id"], claim_ttl_seconds=1))
            ordinary = copy.deepcopy(rows[0])
            ordinary["id"] = "ordinary-job"
            ordinary.pop("xnobrain_custom_page")
            jobs.save_jobs([*rows, ordinary])
            self.assertTrue(jobs.claim_job_for_fire("ordinary-job", claim_ttl_seconds=1))
        # The reservation and TTL floor apply only to app-owned native IDs.
        with dispatch_scope(self.platform.cron, "research", "ordinary-job") as admitted:
            self.assertTrue(admitted)
        self.assertEqual(self.analytics.calls, [])

    async def test_dispatch_lock_releases_on_worker_death_and_is_scoped_per_schedule(self):
        from xnobrain.repositories.custom_page_locks import lifecycle_gate

        binding, _ = self.approve()
        context = multiprocessing.get_context("spawn")
        channel, child = context.Pipe()
        process = context.Process(
            target=hold_schedule_dispatch,
            args=(str(self.files.data_dir), binding["id"], child),
        )
        process.start()
        try:
            self.assertTrue(await asyncio.to_thread(channel.poll, 10))
            self.assertTrue(channel.recv())
            with dispatch_scope(self.platform.cron, "research", binding["id"]) as admitted:
                self.assertFalse(admitted)
            with dispatch_scope(self.platform.cron, "research", "xcp_other") as admitted:
                self.assertTrue(admitted)
            with dispatch_scope(self.platform.cron, "another-agent", binding["id"]) as admitted:
                self.assertTrue(admitted)
            process.terminate()
            await asyncio.to_thread(process.join, 10)
            self.assertIsNotNone(process.exitcode)
            # Actual process death, never a timer/mtime or PID-based guess.
            with dispatch_scope(self.platform.cron, "research", binding["id"]) as admitted:
                self.assertTrue(admitted)
            with lifecycle_gate(self.files.data_dir, "research"):
                pass
        finally:
            if process.is_alive():
                process.terminate()
            await asyncio.to_thread(process.join, 10)
            channel.close()
            child.close()

    async def test_dispatch_error_releases_scope_and_maintenance_prevents_claim(self):
        from xnobrain.integrations.custom_page_cron import store
        from xnobrain.repositories.custom_page_locks import lifecycle_gate
        from xnobrain.repositories.runtime_update_gate import require_admission

        binding, _ = self.approve()
        with patch(
            "cron.scheduler_provider.resolve_cron_scheduler", side_effect=RuntimeError("fixture")
        ):
            with self.assertRaisesRegex(RuntimeError, "fixture"):
                await self.fire(binding["id"])
        with dispatch_scope(self.platform.cron, "research", binding["id"]) as admitted:
            self.assertTrue(admitted)
        with lifecycle_gate(self.files.data_dir, "research"):
            pass
        self.assertEqual(self.platform.cron.active_execution_count, 0)
        with store(self.platform.cron, "research") as jobs:
            self.assertIsNone(jobs.get_job(binding["id"]).get("fire_claim"))
        with patch(
            "xnobrain.repositories.runtime_update_gate.maintenance",
            return_value={"dispatch_paused": True},
        ):
            with self.assertRaises(StoreError) as caught:
                require_admission(self.files.data_dir)
            self.assertEqual(caught.exception.code, "runtime_update_maintenance")
            with self.assertRaises(StoreError) as caught:
                await asyncio.to_thread(self.platform.cron.fire_due, "research", binding["id"])
            self.assertEqual(caught.exception.code, "runtime_update_maintenance")
        self.assertEqual(self.analytics.calls, [])

    async def test_stop_after_completion_preserves_outcome_and_never_rearms_on_retry(self):
        binding, _ = self.approve()
        self.agents.release.set()
        await self.fire(binding["id"])
        self.assertEqual(
            self.schedules.repository.get("research", "tenant\0owner", binding["id"])["state"],
            "scheduled",
        )
        activity_before = self.service.activity("research", self.trusted)
        for _ in range(2):
            stopped = await self.schedules.stop(
                "research", binding["id"], {"confirmation": "STOP " + binding["id"]}, self.trusted
            )
            self.assertEqual(stopped["state"], "stopped")
            self.assertEqual(stopped["last_status"], "completed")
        self.assertEqual(self.analytics.calls, ["research"])
        self.assertEqual(self.service.activity("research", self.trusted), activity_before)

    async def test_late_completion_cannot_resurrect_stopped_binding(self):
        binding, _ = self.approve()
        self.schedules.repository.stop("research", "tenant\0owner", binding["id"])
        self.schedules.repository.finish("research", "tenant\0owner", binding["id"], "completed")
        self.assertEqual(
            self.schedules.repository.get("research", "tenant\0owner", binding["id"])["state"],
            "stopped",
        )

    async def test_schedule_deadline_records_timeout_not_user_cancellation(self):
        self.selection["timeout_seconds"] = 10
        binding, _ = self.approve()
        await asyncio.wait_for(self.fire(binding["id"]), timeout=13)
        result = self.schedules.repository.get("research", "tenant\0owner", binding["id"])
        self.assertEqual(result["state"], "stopped")
        self.assertEqual(result["last_status"], "timed_out")
        self.assertEqual(
            self.runs.get_run("research", "session", result["run_id"])["status"], "timed_out"
        )
        self.assertTrue(self.agents.cancelled.is_set())
        self.assertEqual(self.analytics.calls, ["research"])
        self.assertFalse(
            self.platform.cron._native("research", "get_job", binding["id"])["enabled"]
        )
        again = await self.schedules.stop(
            "research", binding["id"], {"confirmation": "STOP " + binding["id"]}, self.trusted
        )
        self.assertEqual(again["last_status"], "timed_out")

    async def test_cancelled_schedule_observer_cancels_parent_and_keeps_outcome(self):
        binding, _ = self.approve()
        self.platform.cron._native("research", "trigger_job", binding["id"])
        self.platform.cron._native("research", "claim_job_for_fire", binding["id"])
        job = self.platform.cron._native("research", "get_job", binding["id"])
        observer = asyncio.create_task(self.schedules.execute("research", job))
        try:
            for _ in range(200):
                record = self.schedules.repository.get("research", "tenant\0owner", binding["id"])
                if record["run_id"]:
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(record["run_id"])
            observer.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await observer
            finished = self.schedules.repository.get("research", "tenant\0owner", binding["id"])
            self.assertEqual(finished["last_status"], "cancelled")
            self.assertEqual(finished["state"], "stopped")
            self.assertEqual(
                self.runs.get_run("research", "session", record["run_id"])["status"], "cancelled"
            )
            self.assertNotIn(binding["id"], self.schedules._executing)
            self.assertEqual(self.analytics.calls, ["research"])
        finally:
            if not observer.done():
                observer.cancel()
            await asyncio.gather(observer, return_exceptions=True)
