"""Profile-scoped facade over Hermes' native Cron store and scheduler."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..repositories import FileRepository


class CronServiceError(ValueError):
    def __init__(self, message: str, *, status: int = 400, code: str = "invalid_cron"):
        super().__init__(message)
        self.status = status
        self.code = code


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value or "")


class CronService:
    """Translate the stable Brain4All contract to Hermes native Cron calls."""

    def __init__(self, repository: FileRepository, agents: Any):
        self.repository = repository
        self.agents = agents

    def list_jobs(self) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        for profile in self._profiles():
            jobs.extend(self._dto(profile, job) for job in self._native(profile, "list_jobs", True))
        return jobs

    def get_job_detail(self, job_id: str) -> dict[str, Any]:
        profile, job = self._find(job_id)
        run = self._latest_run(profile, job)
        return {"job": self._dto(profile, job), "run": run}

    def create_job(self, body: Mapping[str, Any]) -> dict[str, Any]:
        agent_id = str(body.get("agent_id") or "").strip()
        if not agent_id:
            raise CronServiceError("agent_id is required")
        self.agents.describe_agent(agent_id, include_memory=False)
        name = str(body.get("name") or "").strip()
        prompt = str(body.get("prompt") or "").strip()
        if not name or not prompt:
            raise CronServiceError("name and prompt are required")
        interval = int(body.get("interval_minutes") or 0)
        schedule = str(body.get("schedule") or "").strip()
        if interval > 0:
            schedule = f"every {interval}m"
        if not schedule:
            raise CronServiceError("interval_minutes or schedule is required")
        created = self._native(
            agent_id,
            "create_job",
            prompt=prompt,
            schedule=schedule,
            name=name,
            deliver="local",
        )
        return self._dto(agent_id, created)

    def set_enabled(self, job_id: str, enabled: bool) -> dict[str, Any]:
        profile, job = self._find(job_id)
        action = "resume_job" if enabled else "pause_job"
        return self._dto(profile, self._native(profile, action, job["id"]))

    def delete_job(self, job_id: str) -> dict[str, Any]:
        profile, job = self._find(job_id)
        if not self._native(profile, "remove_job", job["id"]):
            raise CronServiceError("cron job not found", status=404, code="cron_not_found")
        return {"deleted": True}

    def request_run(self, job_id: str) -> dict[str, Any]:
        profile, job = self._find(job_id)
        triggered = self._native(profile, "trigger_job", job["id"])
        if not triggered:
            raise CronServiceError("cron job not found", status=404, code="cron_not_found")
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return {
            "job": self._dto(profile, triggered),
            "run": {"id": f"pending-{job['id']}", "state": "running", "triggered_at": now},
        }

    def _profiles(self) -> list[str]:
        return [
            str(item["name"])
            for item in self.agents.list_agents()["agents"]
        ]

    def _find(self, job_id: str) -> tuple[str, dict[str, Any]]:
        wanted = str(job_id or "").strip()
        for profile in self._profiles():
            for job in self._native(profile, "list_jobs", True):
                if str(job.get("id") or "") == wanted or str(job.get("name") or "") == wanted:
                    return profile, job
        raise CronServiceError("cron job not found", status=404, code="cron_not_found")

    def _native(self, profile: str, function: str, *args, **kwargs):
        from cron import jobs as native
        from hermes_constants import reset_hermes_home_override, set_hermes_home_override

        home = self._profile_home(profile)
        token = set_hermes_home_override(str(home))
        try:
            with native.use_cron_store(home):
                return getattr(native, function)(*args, **kwargs)
        finally:
            reset_hermes_home_override(token)

    def _profile_home(self, agent_id: str) -> Path:
        detail = self.agents.describe_agent(agent_id, include_memory=False)
        home = Path(detail["profile_path"]).resolve()
        if not home.is_dir():
            raise CronServiceError(
                "cron agent profile is unavailable",
                status=404,
                code="agent_not_found",
            )
        return home

    @staticmethod
    def _dto(profile: str, job: Mapping[str, Any]) -> dict[str, Any]:
        schedule = job.get("schedule")
        display = job.get("schedule_display")
        if not display and isinstance(schedule, Mapping):
            display = schedule.get("display") or schedule.get("expr")
        result = dict(job)
        result["agent_id"] = profile
        result["schedule"] = str(display or schedule or "")
        result["next_run_at"] = _iso(job.get("next_run_at"))
        result["enabled"] = bool(job.get("enabled", True))
        return result

    def _latest_run(self, profile: str, job: Mapping[str, Any]) -> dict[str, Any] | None:
        last_at = str(job.get("last_run_at") or "")
        if not last_at:
            return None
        output_dir = self._profile_home(profile) / "cron" / "output" / str(job["id"])
        files = sorted(output_dir.glob("*.md"), key=lambda path: path.name, reverse=True)
        output = files[0].read_text(encoding="utf-8") if files else ""
        if "\n## Error\n" in output:
            error = output.split("\n## Error\n", 1)[1].strip()
            state, output = "failed", ""
        elif "\n## Response\n" in output:
            output, error, state = output.split("\n## Response\n", 1)[1].strip(), "", "success"
        else:
            error, state = "", "success"
        return {
            "id": f"{job['id']}-{last_at}",
            "state": state if str(job.get("last_status") or "ok") == "ok" else "failed",
            "triggered_at": last_at,
            "completed_at": last_at,
            "output": output[:200_000],
            "error": error[:4_000],
        }


__all__ = ["CronService", "CronServiceError"]
