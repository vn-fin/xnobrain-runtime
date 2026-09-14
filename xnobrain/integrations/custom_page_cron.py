"""Opt-in native-cron executor bridge; native claim/history/delivery stay intact."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC
from functools import wraps
from threading import RLock

from hermes_constants import reset_hermes_home_override, set_hermes_home_override

from .cron_timezone import cron_creation_timezone, install_timezone_computation

_EXECUTOR = ContextVar("custom_page_cron_executor", default=None)
_INSTALL_LOCK = RLock()
PREFIX = "xcp_"
MARKER = "xnobrain_custom_page"
EXECUTION_WAIT_SECONDS = 330
FIRE_CLAIM_TTL_SECONDS = 600


@contextmanager
def dispatch_scope(cron, profile, identifier):
    """Fence the whole native fire, including its post-executor bookkeeping."""
    if not str(identifier).startswith(PREFIX):
        yield True
        return
    from ..repositories.base import StoreError
    from ..repositories.custom_page_locks import ScheduleDispatchLease

    try:
        lease = ScheduleDispatchLease(cron.repository.data_dir, profile, identifier)
    except StoreError as error:
        if error.code != "custom_page_jobs_active":
            raise
        yield False
        return
    try:
        yield True
    finally:
        lease.close()


@contextmanager
def store(cron, profile):
    from cron import jobs

    home = cron._profile_home(profile)
    from ..repositories.base import StoreError

    for path in (
        home / "cron",
        home / "cron/jobs.json",
        home / "cron/.jobs.lock",
        home / "cron/executions.db",
        home / "cron/output",
    ):
        if path.is_symlink():
            raise StoreError(
                "custom page cron storage is unsafe", status=409, code="custom_page_unsafe_storage"
            )
    token = set_hermes_home_override(str(home))
    install_timezone_computation(jobs)
    try:
        with jobs.use_cron_store(home):
            yield jobs
    finally:
        reset_hermes_home_override(token)


def install_job(cron, profile, binding, owner):
    """Reserved, inert native record; no briefly enabled unrestricted job."""
    from ..models.custom_page import digest

    approval = binding["approval"]
    with store(cron, profile) as jobs, jobs._jobs_lock():
        rows = jobs.load_jobs()
        if any(row["id"] == binding["id"] for row in rows):
            return
        with cron_creation_timezone(approval["timezone"]):
            schedule = jobs.parse_schedule(approval["schedule"])
        rows.append(
            {
                "id": binding["id"],
                "name": "Custom page: " + approval["action_id"],
                "prompt": "",
                "no_agent": True,
                "script": None,
                "deliver": "local",
                "skills": [],
                "schedule": schedule,
                "schedule_display": approval["schedule"],
                "enabled": False,
                "state": "paused",
                "created_at": jobs._hermes_now().isoformat(),
                "next_run_at": jobs.compute_next_run(schedule),
                "repeat": {"times": None, "completed": 0},
                MARKER: {"owner": owner, "digest": binding["digest"]},
                # Time-control may discover this immutable binding but cannot retime it.
                "xnobrain_custom_page_schedule_digest": digest(schedule),
            }
        )
        jobs.save_jobs(rows)


def arm_job(cron, profile, identifier):
    with store(cron, profile) as jobs, jobs._jobs_lock():
        current = jobs.get_job(identifier)
        if current and not current.get("enabled"):
            jobs.resume_job(identifier)


def pause_job(cron, profile, identifier):
    with store(cron, profile) as jobs:
        jobs.pause_job(identifier, reason="Custom page approval stopped or unavailable")


@contextmanager
def execution_scope(service, profile):
    token = _EXECUTOR.set((service, profile))
    try:
        yield
    finally:
        _EXECUTOR.reset(token)


def install_executor():
    from cron import jobs, scheduler

    with _INSTALL_LOCK:
        original_claim = jobs.claim_job_for_fire
        if not getattr(original_claim, "_xnobrain_custom_page", False):

            @wraps(original_claim)
            def claim(identifier, **kwargs):
                if not str(identifier).startswith(PREFIX):
                    return original_claim(identifier, **kwargs)
                with jobs._jobs_lock():
                    job = jobs.get_job(identifier)
                    from datetime import datetime

                    try:
                        due = datetime.fromisoformat(job["next_run_at"].replace("Z", "+00:00"))
                        if due > datetime.now(UTC):
                            return False
                    except (KeyError, TypeError, ValueError):
                        return False
                    # Outlive the 330s bridge wait, even if a caller requests a
                    # shorter TTL. The dispatch lease, not this timer, fences
                    # slow history/output writes and clock skew.
                    kwargs["claim_ttl_seconds"] = max(
                        FIRE_CLAIM_TTL_SECONDS, kwargs.get("claim_ttl_seconds", 300)
                    )
                    return original_claim(identifier, **kwargs)

            claim._xnobrain_custom_page = True
            jobs.claim_job_for_fire = claim
        original = scheduler.run_job
        if getattr(original, "_xnobrain_custom_page", False):
            return

        @wraps(original)
        def run_job(job, *args, **kwargs):
            if not str(job.get("id", "")).startswith(PREFIX):
                return original(job, *args, **kwargs)
            scope = _EXECUTOR.get()
            if scope is None or scope[0] is None or scope[0].loop is None:
                return False, "", "", "custom_page_schedule_executor_unavailable"
            service, profile = scope
            future = asyncio.run_coroutine_threadsafe(service.execute(profile, job), service.loop)
            try:
                # The async worker applies the tighter approved action deadline.
                return future.result(timeout=EXECUTION_WAIT_SECONDS)
            except Exception:
                future.cancel()
                return False, "", "", "custom_page_schedule_execution_failed"

        run_job._xnobrain_custom_page = True
        scheduler.run_job = run_job
