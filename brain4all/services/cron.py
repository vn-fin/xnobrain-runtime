"""Standalone cron control-plane backed by Hermes native execution."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import time
from typing import Any, Mapping
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..repositories import FileRepository
from ..integrations import HermesCronRunner


class CronServiceError(ValueError):
    """Expected cron validation or lifecycle error."""

    def __init__(self, message: str, *, status: int = 400, code: str = "invalid_cron"):
        super().__init__(message)
        self.status = status
        self.code = code


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise CronServiceError("schedule must be an ISO timestamp") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class CronService:
    """Own standalone cron definitions and expose their latest native run."""

    def __init__(
        self,
        repository: FileRepository,
        agents: Any,
        jobs: Any | None = None,
        runner: Any | None = None,
    ):
        self.repository = repository
        self.agents = agents
        self.jobs = jobs or repository
        self.runner = runner or HermesCronRunner(repository.profiles_root)

    def list_jobs(self) -> list[dict[str, Any]]:
        return self.jobs.list_crons()

    def get_job_detail(self, job_id: str) -> dict[str, Any]:
        job = self._job(job_id)
        run = None
        if job.get("last_run_id"):
            run = {
                "id": str(job.get("last_run_id")),
                "state": str(job.get("last_run_status") or "running"),
                "triggered_at": job.get("last_run_at"),
                "completed_at": job.get("last_run_completed_at"),
                "output": job.get("last_output") or "",
                "error": job.get("last_error") or "",
            }
        return {"job": job, "run": run}

    def create_job(self, body: Mapping[str, Any]) -> dict[str, Any]:
        agent_id = str(body.get("agent_id") or "").strip()
        if not agent_id:
            raise CronServiceError("agent_id is required")
        self.agents.describe_agent(agent_id, include_memory=False)

        name = str(body.get("name") or "").strip()
        prompt = str(body.get("prompt") or "").strip()
        if not name:
            raise CronServiceError("name is required")
        if not prompt:
            raise CronServiceError("prompt is required")

        timezone_name = str(body.get("timezone") or "Etc/UTC").strip()
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as error:
            raise CronServiceError("timezone is invalid") from error

        interval = int(body.get("interval_minutes") or 0)
        raw_schedule = str(body.get("schedule") or "").strip()
        now = datetime.now(timezone.utc)
        if interval > 0:
            schedule = f"@every {interval}m"
            next_run_at = now + timedelta(minutes=interval)
        elif raw_schedule:
            schedule = raw_schedule
            next_run_at = _parse_iso(raw_schedule)
            if next_run_at <= now:
                raise CronServiceError("scheduled time must be in the future")
        else:
            raise CronServiceError("interval_minutes or schedule is required")

        timestamp = _iso(now)
        job = {
            "id": uuid.uuid4().hex,
            "agent_id": agent_id,
            "name": name,
            "prompt": prompt,
            "schedule": schedule,
            "timezone": timezone_name,
            "enabled": True,
            "next_run_at": _iso(next_run_at),
            "created_at": timestamp,
            "updated_at": timestamp,
            "version": 1,
        }
        self._snapshot(job, "created")
        return self.jobs.put_cron(job)

    def set_enabled(self, job_id: str, enabled: bool) -> dict[str, Any]:
        current = self._job(job_id)
        self._snapshot(current, "before-resume" if enabled else "before-pause")
        updated = {
            **current,
            "enabled": bool(enabled),
            "updated_at": _iso(datetime.now(timezone.utc)),
            "version": int(current.get("version") or 1) + 1,
        }
        return self.jobs.put_cron(updated)

    def delete_job(self, job_id: str) -> dict[str, Any]:
        current = self._job(job_id)
        self._snapshot(current, "before-delete")
        if not self.jobs.delete_cron(job_id):
            raise CronServiceError("cron job not found", status=404, code="cron_not_found")
        return {"deleted": True}

    def due_jobs(self, now: datetime | None = None) -> list[dict[str, Any]]:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        result = []
        for job in self.list_jobs():
            if not bool(job.get("enabled")):
                continue
            raw = str(job.get("next_run_at") or "").strip()
            if raw and _parse_iso(raw) <= current:
                result.append(job)
        return result

    def request_run(self, job_id: str) -> dict[str, Any]:
        job = self._job(job_id)
        def started(run: Mapping[str, Any]) -> None:
            current = self._job(job_id)
            self.jobs.put_cron({
                **current,
                "last_run_id": str(run.get("id") or ""),
                "last_run_status": "running",
                "last_run_at": run.get("triggered_at") or _iso(datetime.now(timezone.utc)),
                "last_run_completed_at": None,
                "last_output": "",
                "last_error": "",
                "updated_at": _iso(datetime.now(timezone.utc)),
                "version": int(current.get("version") or 1) + 1,
            })
        def completed(run: Mapping[str, Any]) -> None:
            current = self._job(job_id)
            if str(current.get("last_run_id") or "") != str(run.get("id") or ""):
                return
            self.jobs.put_cron({
                **current,
                "last_run_status": str(run.get("state") or "failed"),
                "last_run_completed_at": run.get("completed_at") or _iso(datetime.now(timezone.utc)),
                "last_output": str(run.get("output") or "")[:200_000],
                "last_error": str(run.get("error") or "")[:4_000],
                "updated_at": _iso(datetime.now(timezone.utc)),
                "version": int(current.get("version") or 1) + 1,
            })
        try:
            run = self.runner.trigger_once(job, on_started=started, on_complete=completed)
        except Exception as error:
            raise CronServiceError(
                "Hermes could not start the cron job",
                status=503,
                code="cron_runtime_unavailable",
            ) from error
        return {"job": self._job(job_id), "run": run}

    def _job(self, job_id: str) -> dict[str, Any]:
        normalized = str(job_id or "").strip()
        for job in self.list_jobs():
            if str(job.get("id") or "") == normalized:
                return job
        raise CronServiceError("cron job not found", status=404, code="cron_not_found")

    def _snapshot(self, job: Mapping[str, Any], reason: str) -> None:
        agent_id = str(job.get("agent_id") or "")
        job_id = str(job.get("id") or "")
        snapshot = {
            "reason": reason,
            "captured_at": _iso(datetime.now(timezone.utc)),
            "job": dict(job),
        }
        path: Path = (
            self.repository.profile_path(agent_id)
            / "cron"
            / "snapshots"
            / job_id
            / f"{time.time_ns()}.yaml"
        )
        self.repository.atomic_write(
            path,
            self._yaml_bytes(snapshot),
            mode=0o440,
            replace=False,
        )

    @staticmethod
    def _yaml_bytes(value: Mapping[str, Any]) -> bytes:
        import yaml

        return yaml.safe_dump(dict(value), sort_keys=False, allow_unicode=True).encode()


__all__ = ["CronService", "CronServiceError"]
