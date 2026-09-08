"""Opt-in per-job timezone computation without replacing native claims/storage."""

import re

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from threading import RLock

from xnobrain.models.automation import CronCreate

from .schedule_preview import preview_calendar_runs


POLICY = "skip_gap_earlier_fold"
_install_lock = RLock()
_creation_zone: ContextVar[str | None] = ContextVar("cron_creation_zone", default=None)


@contextmanager
def cron_creation_timezone(zone: str):
    token = _creation_zone.set(zone)
    try:
        yield
    finally:
        _creation_zone.reset(token)


def validated_schedule_timezone(schedule: dict) -> str:
    """Validate a persisted binding before execution or authoritative readback."""
    binding = schedule["xnobrain_time"]
    if (
        not isinstance(binding, dict)
        or type(binding.get("schema_version")) is not int
        or binding.get("schema_version") != 1
        or set(binding) != {"schema_version", "timezone", "dst_policy"}
        or not isinstance(binding.get("timezone"), str)
        or not isinstance(schedule.get("expr"), str)
        or binding.get("dst_policy") != POLICY
        or schedule.get("kind") != "cron"
    ):
        raise ValueError("unsupported per-job timezone binding")
    return CronCreate.validate_timezone(binding["timezone"])


def install_timezone_computation(jobs_module) -> None:
    """Install once; unbound schedules retain the original implementation."""
    with _install_lock:
        _install_timezone_computation(jobs_module)


def _install_timezone_computation(jobs_module) -> None:
    original = jobs_module.compute_next_run
    if getattr(original, "_xnobrain_timezone_adapter", False) is True:
        return

    @wraps(original)
    def compute(schedule, last_run_at=None):
        if not isinstance(schedule, dict) or "xnobrain_time" not in schedule:
            return original(schedule, last_run_at)
        zone = validated_schedule_timezone(schedule)
        cutoff = (
            datetime.fromisoformat(last_run_at.replace("Z", "+00:00"))
            if last_run_at
            else datetime.now(timezone.utc)
        )
        return preview_calendar_runs(
            schedule["expr"], zone, cutoff, count=1
        )[0]["utc"]

    compute._xnobrain_timezone_adapter = True
    jobs_module.compute_next_run = compute
    original_parse = getattr(jobs_module, "parse_schedule", None)
    if original_parse is not None:
        @wraps(original_parse)
        def parse(schedule):
            zone = _creation_zone.get()
            if zone is not None and isinstance(schedule, str):
                text = schedule.strip()
                if re.match(r"^\d{4}-\d{2}-\d{2}", text):
                    try:
                        instant = datetime.fromisoformat(text.replace("Z", "+00:00"))
                    except ValueError as error:
                        raise ValueError("invalid absolute schedule timestamp") from error
                    if instant.tzinfo is None or instant.utcoffset() is None:
                        raise ValueError("absolute schedule requires an explicit UTC offset")
            try:
                parsed = original_parse(schedule)
            except ValueError:
                if zone is None or not isinstance(schedule, str):
                    raise
                # Native parsing recognizes numeric cron only. Validate named
                # fields with the same bounded evaluator used for execution.
                preview_calendar_runs(
                    schedule, zone, datetime.now(timezone.utc), count=1
                )
                parsed = {
                    "kind": "cron", "expr": schedule.strip(),
                    "display": schedule.strip(),
                }
            if zone is not None and parsed.get("kind") == "cron":
                parsed = {**parsed, "xnobrain_time": {
                    "schema_version": 1, "timezone": zone, "dst_policy": POLICY,
                }}
            return parsed

        jobs_module.parse_schedule = parse

