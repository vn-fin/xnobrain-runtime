"""Hermes cron inventory and exact per-job FT0013 migration adapter."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any, Mapping

from hermes_constants import reset_hermes_home_override, set_hermes_home_override

from .cron_timezone import POLICY, install_timezone_computation, validated_schedule_timezone
from .schedule_preview import preview_calendar_runs


def schedule_revision(job: Mapping[str, Any]) -> int:
    value = job.get("xnobrain_time_revision")
    schedule = job.get("schedule")
    if type(value) is not int or value < 1 or not isinstance(schedule, Mapping):
        return 0
    expected = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(schedule, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    return value if job.get("xnobrain_time_revision_digest") == expected else 0


def inventory(cron_service) -> list[dict[str, Any]]:
    """Return local schedules with persisted revision authority."""
    rows = []
    for profile, jobs in cron_service._jobs_by_profile():
        for job in jobs:
            projected = cron_service._dto(profile, job)
            kind = projected.get("schedule_kind")
            revision = schedule_revision(job)
            editable = bool(kind == "cron" and projected.get("timezone") and revision > 0)
            restriction = ""
            if kind != "cron":
                restriction = "Only calendar schedules can be retimed."
            elif not projected.get("timezone"):
                restriction = "The persisted calendar timezone is unknown or unsupported."
            elif revision < 1:
                restriction = "This schedule has no revision-safe migration authority."
            rows.append(
                {
                    "id": str(job.get("id") or ""),
                    "label": str(job.get("name") or job.get("id") or "Schedule")[:280],
                    "kind": "calendar" if kind == "cron" else kind or "unknown",
                    "timezone": projected.get("timezone"),
                    "revision": revision,
                    "editable": editable,
                    "restriction": restriction,
                    "next_run_at": str(projected.get("next_run_at") or ""),
                    "schedule": str(projected.get("schedule") or "")[:280],
                }
            )
    return rows


def preview_migration(
    cron_service,
    items: list[Mapping[str, Any]],
    target_timezone: str,
    cutoff: datetime,
) -> list[dict[str, Any]]:
    """Preview each exact selected schedule without mutating cron state."""
    available = {
        str(job.get("id") or ""): (profile, job)
        for profile, jobs in cron_service._jobs_by_profile()
        for job in jobs
    }
    result = []
    for requested in items:
        identifier = str(requested["id"])
        expected = int(requested["revision"])
        found = available.get(identifier)
        if found is None:
            result.append(
                _preview_outcome(
                    identifier, expected, target_timezone, "stale", "The schedule no longer exists."
                )
            )
            continue
        _, job = found
        revision = schedule_revision(job)
        schedule = job.get("schedule")
        if revision != expected:
            result.append(
                _preview_outcome(
                    identifier,
                    revision,
                    target_timezone,
                    "stale",
                    "The schedule revision changed before preview.",
                )
            )
            continue
        try:
            old_timezone = validated_schedule_timezone(dict(schedule))
            expression = str(schedule["expr"])
            occurrences = preview_calendar_runs(
                expression,
                target_timezone,
                cutoff,
                count=5,
            )
        except (KeyError, TypeError, ValueError):
            result.append(
                _preview_outcome(
                    identifier,
                    revision,
                    target_timezone,
                    "unsupported",
                    "The calendar schedule or its timezone policy is unsupported.",
                )
            )
            continue
        result.append(
            {
                "id": identifier,
                "label": str(job.get("name") or identifier)[:280],
                "old_timezone": old_timezone,
                "new_timezone": target_timezone,
                "revision": revision,
                "status": "ready",
                "reason": "",
                "next_occurrences": [
                    {"utc": row["utc"], "local": row["local"]} for row in occurrences
                ],
            }
        )
    return result


def migrate_schedule(
    cron_service,
    item: Mapping[str, Any],
    target_timezone: str,
    cutoff: datetime,
    operation_id: str,
) -> dict[str, Any]:
    """Atomically migrate one exact job while retaining all run/history fields."""
    identifier = str(item["id"])
    expected = int(item["revision"])
    matches = []
    for profile, jobs in cron_service._jobs_by_profile():
        for job in jobs:
            if str(job.get("id") or "") == identifier:
                matches.append((profile, job))
    if len(matches) != 1:
        return _item_result(
            identifier, "stale", expected, "The schedule no longer has unique local ownership."
        )
    profile, _ = matches[0]
    home = cron_service._profile_home(profile)
    token = set_hermes_home_override(str(home))
    try:
        from cron import jobs as native_jobs

        install_timezone_computation(native_jobs)
        with native_jobs.use_cron_store(home), native_jobs._jobs_lock():
            jobs = native_jobs.load_jobs()
            indexes = [
                index for index, job in enumerate(jobs) if str(job.get("id") or "") == identifier
            ]
            if len(indexes) != 1:
                return _item_result(
                    identifier, "stale", expected, "The schedule changed before migration."
                )
            index = indexes[0]
            job = jobs[index]
            revision = schedule_revision(job)
            schedule = job.get("schedule")
            if job.get("xnobrain_time_last_operation") == operation_id and revision == expected + 1:
                try:
                    if validated_schedule_timezone(dict(schedule)) == target_timezone:
                        return _item_result(
                            identifier,
                            "succeeded",
                            revision,
                            next_run_at=str(job.get("next_run_at") or ""),
                        )
                except (TypeError, ValueError):
                    pass
            if revision != expected:
                return _item_result(
                    identifier, "stale", revision, "The schedule revision changed before migration."
                )
            try:
                validated_schedule_timezone(dict(schedule))
                expression = str(schedule["expr"])
                next_run = preview_calendar_runs(
                    expression,
                    target_timezone,
                    cutoff,
                    count=1,
                )[0]["utc"]
            except (KeyError, TypeError, ValueError):
                return _item_result(
                    identifier,
                    "unsupported",
                    revision,
                    "The calendar schedule or its timezone policy is unsupported.",
                )

            jobs_path = home / "cron" / "jobs.json"
            if jobs_path.is_file() and not jobs_path.is_symlink():
                payload = jobs_path.read_bytes()
                digest = hashlib.sha256(payload).hexdigest()
                snapshot = (
                    home
                    / "snapshots"
                    / "cron"
                    / "time-control"
                    / f"{time.time_ns()}-{digest[:12]}.json"
                )
                cron_service.repository.atomic_write(
                    snapshot,
                    payload,
                    mode=0o440,
                    replace=False,
                )
            migrated = dict(job)
            migrated_schedule = dict(schedule)
            migrated_schedule["xnobrain_time"] = {
                "schema_version": 1,
                "timezone": target_timezone,
                "dst_policy": POLICY,
            }
            migrated["schedule"] = migrated_schedule
            migrated["next_run_at"] = next_run
            migrated["xnobrain_time_revision"] = expected + 1
            migrated["xnobrain_time_revision_digest"] = (
                "sha256:"
                + hashlib.sha256(
                    json.dumps(
                        migrated_schedule,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest()
            )
            migrated["xnobrain_time_last_operation"] = operation_id
            jobs[index] = migrated
            native_jobs.save_jobs(jobs)
            return _item_result(
                identifier,
                "succeeded",
                expected + 1,
                next_run_at=next_run,
            )
    except Exception:
        return _item_result(
            identifier, "failed", expected, "The Runtime could not persist this schedule migration."
        )
    finally:
        reset_hermes_home_override(token)


def _preview_outcome(
    identifier: str,
    revision: int,
    target_timezone: str,
    status: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "id": identifier,
        "label": identifier,
        "old_timezone": None,
        "new_timezone": target_timezone,
        "revision": max(1, revision),
        "status": status,
        "reason": reason,
        "next_occurrences": [],
    }


def _item_result(
    identifier: str,
    status: str,
    revision: int,
    reason: str = "",
    *,
    next_run_at: str = "",
) -> dict[str, Any]:
    return {
        "id": identifier,
        "status": status,
        "reason": reason,
        "revision": max(0, revision),
        "next_run_at": next_run_at,
    }
