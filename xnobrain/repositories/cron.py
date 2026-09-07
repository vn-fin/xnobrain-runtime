"""Cron schedule persistence."""

from __future__ import annotations

from typing import Any, Mapping

import yaml

from .base import StoreError


class CronRepositoryMixin:
    """Cron schedule operations."""

    def list_crons(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for profile in self._profile_dirs():
            jobs = profile / "cron" / "jobs"
            if not jobs.is_dir():
                continue
            for path in sorted(jobs.glob("*.yaml")):
                try:
                    item = self._read_yaml(path)
                    if isinstance(item, dict):
                        item.setdefault("agent_id", profile.name)
                        result.append(item)
                except (OSError, yaml.YAMLError):
                    continue
        return sorted(result, key=lambda item: str(item.get("created_at") or ""))

    def put_cron(self, item: Mapping[str, Any]) -> dict[str, Any]:
        job = dict(item)
        agent_id = self._id(job.get("agent_id"), "agent id")
        cron_id = self._id(job.get("id"), "cron id")
        path = self.profile_path(agent_id) / "cron" / "jobs" / f"{cron_id}.yaml"
        self.atomic_yaml(path, job)
        return job

    def delete_cron(self, cron_id: Any) -> bool:
        cron_id = self._id(cron_id, "cron id")
        with self._lock:
            for profile in self._profile_dirs():
                path = profile / "cron" / "jobs" / f"{cron_id}.yaml"
                if path.is_file():
                    path.unlink()
                    self._sync_dir(path.parent)
                    return True
        return False
