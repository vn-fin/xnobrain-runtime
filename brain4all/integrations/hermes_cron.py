"""Profile-scoped adapter for one-off execution through Hermes native cron."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
from typing import Any, Mapping


_RUN_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="brain4all-cron")
_log = logging.getLogger("brain4all.hermes_cron")


class HermesCronRunner:
    """Create and dispatch a transient native Hermes cron execution."""

    def __init__(self, profiles_root: str | Path, executor: Any | None = None):
        self.profiles_root = Path(profiles_root).resolve()
        self.executor = executor or _RUN_POOL

    def trigger_once(self, job: Mapping[str, Any], *, on_started=None, on_complete=None) -> dict[str, Any]:
        agent_id = str(job.get("agent_id") or "").strip()
        profile = (self.profiles_root / agent_id).resolve()
        if profile.parent != self.profiles_root or not profile.is_dir():
            raise ValueError("cron agent profile is unavailable")

        from cron import jobs as cron_jobs
        from hermes_constants import reset_hermes_home_override, set_hermes_home_override

        token = set_hermes_home_override(str(profile))
        try:
            with cron_jobs.use_cron_store(profile):
                # The transient one-shot keeps manual runs independent from the
                # persisted Brain4All schedule while using Hermes' normal agent path.
                native = cron_jobs.create_job(
                    prompt=str(job.get("prompt") or ""),
                    schedule=(datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
                    name=str(job.get("name") or "Manual cron run"),
                    repeat=1,
                    deliver="local",
                )
                triggered = cron_jobs.trigger_job(str(native["id"]))
                if triggered is None:
                    raise RuntimeError("Hermes cron job could not be triggered")
        finally:
            reset_hermes_home_override(token)

        run = {
            "id": str(native["id"]),
            "state": "running",
            "triggered_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        if on_started is not None:
            on_started(run)
        future = self.executor.submit(self._fire, profile, str(native["id"]))
        if on_complete is not None:
            def complete(finished):
                try:
                    on_complete(finished.result())
                except Exception as error:
                    on_complete({
                        "id": str(native["id"]),
                        "state": "failed",
                        "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        "error": str(error),
                    })
            future.add_done_callback(complete)
        return run

    @staticmethod
    def _fire(profile: Path, job_id: str) -> dict[str, Any]:
        from cron import jobs as cron_jobs
        from cron.scheduler_provider import resolve_cron_scheduler
        from hermes_constants import reset_hermes_home_override, set_hermes_home_override

        token = set_hermes_home_override(str(profile))
        try:
            with cron_jobs.use_cron_store(profile):
                if not resolve_cron_scheduler().fire_due(job_id, adapters=None, loop=None):
                    raise RuntimeError("Hermes cron job was not claimed")
                output_dir = profile / "cron" / "output" / job_id
                outputs = sorted(output_dir.glob("*.md"), key=lambda path: path.name, reverse=True)
                raw_output = outputs[0].read_text(encoding="utf-8") if outputs else ""
                output, state, error = HermesCronRunner._display_result(raw_output)
                return {
                    "id": job_id,
                    "state": state,
                    "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "output": output,
                    "error": error,
                }
        except Exception:
            _log.error("Hermes cron execution failed (job_id=%s)", job_id)
            raise
        finally:
            reset_hermes_home_override(token)

    @staticmethod
    def _display_result(raw: str) -> tuple[str, str, str]:
        """Expose only the final response section, never the stored prompt."""
        text = str(raw or "")
        if "\n## Error\n" in text:
            error = text.split("\n## Error\n", 1)[1].strip()
            if error.startswith("```") and error.endswith("```"):
                error = error[3:-3].strip()
            return "", "failed", error[:4_000]
        if "\n## Response\n" in text:
            return text.split("\n## Response\n", 1)[1].strip(), "success", ""
        return text[:200_000], "success", ""


__all__ = ["HermesCronRunner"]
