"""Profile-scoped facade over Hermes' native Cron store and scheduler."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import logging
from pathlib import Path
import sqlite3
import time
from typing import Any, Mapping
import uuid

from ..integrations import (
    CronBlueprintInvalid,
    CronBlueprintNotFound,
    CronDeliveryAdapter,
    CronDeliveryAdapterError,
)
from ..repositories import FileRepository


LOGGER = logging.getLogger(__name__)


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
    """Translate the stable XNOBrain contract to Hermes native Cron calls."""

    def __init__(self, repository: FileRepository, agents: Any):
        self.repository = repository
        self.agents = agents
        self.delivery = CronDeliveryAdapter()
        self.kanban: Any | None = None

    def list_jobs(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        profile_jobs = self._jobs_by_profile([agent_id] if agent_id else None)
        self.reconcile_deliveries(profile_jobs)
        return [
            self._dto(profile, job)
            for profile, jobs in profile_jobs
            for job in jobs
        ]

    def get_job_detail(self, job_id: str, agent_id: str | None = None) -> dict[str, Any]:
        profile_jobs = self._jobs_by_profile([agent_id] if agent_id else None)
        self.reconcile_deliveries(profile_jobs)
        profile, job = self._find(job_id, profile_jobs)
        runs = self._run_dtos(profile, job, limit=20)
        return {
            "job": self._dto(profile, job),
            "run": self._latest_run(profile, job, runs),
            "targets": self._target_dtos(profile, job),
            "runs": runs,
        }

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
        try:
            created = self._native(
                agent_id,
                "create_job",
                prompt=prompt,
                schedule=schedule,
                name=name,
                deliver="local",
            )
        except ValueError as exc:
            raise CronServiceError(str(exc), code="invalid_schedule") from exc
        if self.kanban is not None:
            next_run_at = _iso(created.get("next_run_at"))
            task = self.kanban.create_task("default", {
                "title": name,
                "description": prompt,
                "status": "scheduled",
                "assignee": agent_id,
                "schedule": {
                    "recurrence": "interval" if interval > 0 else "once",
                    "scheduled_at": next_run_at,
                    "interval_minutes": interval if interval > 0 else None,
                    "timezone": "Etc/UTC",
                },
            }, created_by="cron")
            created = self._native(agent_id, "update_job", str(created["id"]), {
                "xnobrain_kanban_board": "default",
                "xnobrain_kanban_task_id": str(task["id"]),
            }) or created
        return self._dto(agent_id, created)

    def list_blueprints(self) -> dict[str, Any]:
        try:
            return {"blueprints": self.delivery.list_blueprints()}
        except CronDeliveryAdapterError as exc:
            raise CronServiceError(str(exc), status=503, code="cron_blueprints_unavailable") from exc

    def instantiate_blueprint(self, body: Mapping[str, Any]) -> dict[str, Any]:
        agent_id = str(body.get("agent_id") or "").strip()
        self.agents.describe_agent(agent_id, include_memory=False)
        try:
            spec = self.delivery.fill(str(body.get("blueprint") or ""), body.get("values") or {})
        except CronBlueprintNotFound as exc:
            raise CronServiceError(str(exc), status=404, code="blueprint_not_found") from exc
        except CronBlueprintInvalid as exc:
            raise CronServiceError(str(exc), status=422, code="invalid_blueprint_values") from exc
        except CronDeliveryAdapterError as exc:
            raise CronServiceError(str(exc), status=503, code="cron_blueprints_unavailable") from exc
        deliver = str(spec.get("deliver") or "local")
        native_deliver = deliver if deliver not in {"origin", ""} else "local"
        try:
            created = self._native(
                agent_id,
                "create_job",
                prompt=str(spec.get("prompt") or ""),
                schedule=str(spec.get("schedule") or ""),
                name=str(spec.get("name") or body.get("blueprint") or "Automation"),
                deliver=native_deliver,
                skills=spec.get("skills") or None,
            )
        except ValueError as exc:
            raise CronServiceError(str(exc), status=422, code="invalid_schedule") from exc
        targets = list(body.get("deliver_targets") or [])
        if not targets and native_deliver not in {"local", "origin"}:
            targets = [{
                "target_type": "email" if native_deliver.split(":", 1)[0] == "email" else "channel",
                "destination": native_deliver,
            }]
        for target in targets:
            self.add_delivery_target(str(created["id"]), target, agent_id)
        profile, updated = self._find_job(str(created["id"]), agent_id)
        return self._dto(profile, updated)

    def list_delivery_target_options(self, agent_id: str | None = None) -> dict[str, Any]:
        profiles = [agent_id] if agent_id else self._profiles()
        discovered: dict[tuple[str, str], dict[str, Any]] = {}
        for profile in profiles:
            try:
                rows = self._native_module(profile, "cron.scheduler", "cron_delivery_targets")
            except Exception:
                rows = []
            for row in rows or []:
                target_id = str(row.get("id") or "").strip()
                if not target_id:
                    continue
                target_type = "email" if target_id == "email" else "channel"
                discovered[(target_type, target_id)] = {
                    "target_type": target_type,
                    "id": target_id,
                    "name": str(row.get("name") or target_id.title()),
                    "available": bool(row.get("home_target_set")),
                    "degraded_reason": None if row.get("home_target_set") else "No home delivery target configured",
                }
        discovered.setdefault(("email", "email"), {
            "target_type": "email", "id": "email", "name": "Email",
            "available": False, "degraded_reason": "No email delivery target configured",
        })
        return {
            "options": [
                *discovered.values(),
                {"target_type": "kanban", "id": "kanban", "name": "Kanban board", "available": True, "degraded_reason": None},
                {"target_type": "file", "id": "file", "name": "Workspace file", "available": True, "degraded_reason": None},
            ]
        }

    def list_job_targets(self, job_id: str, agent_id: str | None = None) -> dict[str, Any]:
        profile, job = self._find_job(job_id, agent_id)
        return {"targets": self._target_dtos(profile, job)}

    def add_delivery_target(
        self,
        job_id: str,
        body: Mapping[str, Any],
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        profile, job = self._find_job(job_id, agent_id)
        target_type = str(body.get("target_type") or "").strip()
        destination = str(body.get("destination") or "").strip()
        if target_type not in {"channel", "email", "kanban", "file"}:
            raise CronServiceError("invalid delivery target type", status=422, code="invalid_delivery_target")
        if target_type == "kanban":
            if not destination:
                raise CronServiceError("Kanban board is required", status=422, code="invalid_delivery_target")
            if self.kanban is None:
                raise CronServiceError("Kanban is unavailable", status=503, code="kanban_not_ready")
            self.kanban.get_board(destination)
        elif target_type == "file":
            if not destination:
                raise CronServiceError("workspace file path is required", status=422, code="invalid_delivery_target")
            try:
                path = self.agents._workspace_path(profile, destination, require_file=False)
                destination = path.relative_to(self.agents._workspace_dir(profile).resolve()).as_posix()
            except (ValueError, OSError) as exc:
                raise CronServiceError("workspace delivery path is invalid", code="invalid_workspace_path") from exc
        else:
            if not destination:
                raise CronServiceError(
                    f"{target_type} destination is required", status=422, code="invalid_delivery_target"
                )
            option = next((
                item for item in self.list_delivery_target_options(profile)["options"]
                if item["target_type"] == target_type and item["id"] == destination
            ), None)
            if not option or not option.get("available"):
                raise CronServiceError(
                    f"{target_type} delivery target is not configured",
                    status=422,
                    code="delivery_target_unavailable",
                )

        target = {
            "id": uuid.uuid4().hex[:12],
            "target_type": target_type,
            "destination": destination,
            "created_at": _iso(datetime.now(timezone.utc)),
        }
        targets = [dict(item) for item in job.get("xnobrain_delivery_targets") or []]
        targets.append(target)
        self._snapshot_store(profile)
        updated = self._native(profile, "update_job", str(job["id"]), {
            "xnobrain_delivery_targets": targets,
            "deliver": self._native_deliver(targets),
        })
        return self._target_dto(profile, updated or job, target)

    def remove_delivery_target(
        self,
        job_id: str,
        target_id: str,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        profile, job = self._find_job(job_id, agent_id)
        targets = [dict(item) for item in job.get("xnobrain_delivery_targets") or []]
        kept = [item for item in targets if str(item.get("id")) != str(target_id)]
        if len(kept) == len(targets):
            raise CronServiceError("delivery target not found", status=404, code="delivery_target_not_found")
        self._snapshot_store(profile)
        self._native(profile, "update_job", str(job["id"]), {
            "xnobrain_delivery_targets": kept,
            "deliver": self._native_deliver(kept),
        })
        return {"deleted": True}

    def set_enabled(
        self,
        job_id: str,
        enabled: bool,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        profile, job = self._find_job(job_id, agent_id)
        action = "resume_job" if enabled else "pause_job"
        return self._dto(profile, self._native(profile, action, job["id"]))

    def delete_job(self, job_id: str, agent_id: str | None = None) -> dict[str, Any]:
        profile, job = self._find_job(job_id, agent_id)
        self._snapshot_store(profile)
        self._snapshot_job_output(profile, str(job["id"]))
        if not self._native(profile, "remove_job", job["id"]):
            raise CronServiceError("cron job not found", status=404, code="cron_not_found")
        return {"deleted": True}

    def request_run(self, job_id: str, agent_id: str | None = None) -> dict[str, Any]:
        profile, job = self._find_job(job_id, agent_id)
        triggered = self._native(profile, "trigger_job", job["id"])
        if not triggered:
            raise CronServiceError("cron job not found", status=404, code="cron_not_found")
        now = _iso(datetime.now(timezone.utc))
        return {
            "job": self._dto(profile, triggered),
            "run": {"id": f"pending-{job['id']}", "state": "running", "triggered_at": now, "deliveries": []},
        }

    def due_jobs(self) -> list[tuple[str, str]]:
        """Return profile jobs that should be handed to the native scheduler."""
        now = datetime.now(timezone.utc)
        due: list[tuple[str, str]] = []
        try:
            profiles = self._profiles()
        except Exception as exc:
            LOGGER.debug("Could not enumerate cron profiles: %s", exc)
            return due
        for profile in profiles:
            try:
                jobs = self._native(profile, "list_jobs", True)
            except Exception as exc:
                LOGGER.debug("Could not inspect cron jobs for profile %s: %s", profile, exc)
                continue
            for job in jobs:
                if not job.get("enabled", True) or str(job.get("state") or "") == "paused":
                    continue
                raw_next = str(job.get("next_run_at") or "").strip()
                if not raw_next:
                    continue
                try:
                    next_run = datetime.fromisoformat(raw_next.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if next_run.tzinfo is None:
                    next_run = next_run.replace(tzinfo=timezone.utc)
                if next_run <= now:
                    due.append((profile, str(job["id"])))
        return due

    def fire_due(self, profile: str, job_id: str) -> bool:
        """Claim and execute one due job using its profile-scoped Hermes home."""
        from hermes_constants import reset_hermes_home_override, set_hermes_home_override
        from cron import jobs as cron_jobs
        from cron.scheduler_provider import resolve_cron_scheduler

        home = self._profile_home(profile)
        token = set_hermes_home_override(str(home))
        try:
            with cron_jobs.use_cron_store(home):
                provider = resolve_cron_scheduler()
                fired = bool(provider.fire_due(job_id, adapters=None, loop=None))
            if fired:
                try:
                    self.reconcile_deliveries([(profile, self._native(profile, "list_jobs", True))])
                except Exception:
                    LOGGER.warning("Could not reconcile cron deliveries for job %s", job_id)
            return fired
        finally:
            reset_hermes_home_override(token)

    def list_job_runs(
        self,
        job_id: str,
        limit: int = 20,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        profile_jobs = self._jobs_by_profile([agent_id] if agent_id else None)
        self.reconcile_deliveries(profile_jobs)
        profile, job = self._find(job_id, profile_jobs)
        bounded = max(1, min(int(limit), 100))
        return {"runs": self._run_dtos(profile, job, bounded), "limit": bounded}

    def reconcile_deliveries(
        self,
        profile_jobs: list[tuple[str, list[dict[str, Any]]]] | None = None,
    ) -> None:
        for profile, jobs in profile_jobs if profile_jobs is not None else self._jobs_by_profile():
            for job in jobs:
                targets = [dict(item) for item in job.get("xnobrain_delivery_targets") or []]
                if not targets:
                    continue
                records = [dict(item) for item in job.get("xnobrain_delivery_records") or []]
                indexed = {(str(item.get("execution_id")), str(item.get("target_id"))): item for item in records}
                changed = False
                for execution in reversed(self._executions(profile, job, 50)):
                    status = str(execution.get("status") or "")
                    if status not in {"completed", "failed", "unknown"}:
                        continue
                    output = self._execution_output(profile, job, execution)
                    for target in targets:
                        key = (str(execution["id"]), str(target["id"]))
                        previous = indexed.get(key)
                        if previous and previous.get("status") in {"delivered", "failed"}:
                            continue
                        result = self._deliver_execution(profile, job, execution, target, output)
                        if result is None:
                            continue
                        record = {
                            "execution_id": str(execution["id"]),
                            "target_id": str(target["id"]),
                            "target_type": str(target["target_type"]),
                            "status": result["status"],
                            "at": _iso(datetime.now(timezone.utc)),
                            "reason": result.get("reason"),
                        }
                        if previous:
                            records[records.index(previous)] = record
                        else:
                            records.append(record)
                        indexed[key] = record
                        changed = True
                if changed:
                    self._snapshot_store(profile)
                    self._native(profile, "update_job", str(job["id"]), {
                        "xnobrain_delivery_records": records[-500:],
                    })
                    # Keep the request's loaded job in sync with the durable
                    # update so detail/run responses publish delivery state
                    # immediately, without requiring a second refresh.
                    job["xnobrain_delivery_records"] = records[-500:]

    def _deliver_execution(
        self,
        profile: str,
        job: Mapping[str, Any],
        execution: Mapping[str, Any],
        target: Mapping[str, Any],
        output: str | None,
    ) -> dict[str, str | None] | None:
        target_type = str(target.get("target_type") or "")
        execution_status = str(execution.get("status") or "")
        if target_type in {"channel", "email"}:
            if execution_status != "completed":
                return {"status": "failed", "reason": str(execution.get("error") or "Cron run failed")[:500]}
            delivery_error = str(job.get("last_delivery_error") or "").strip()
            return {"status": "failed" if delivery_error else "delivered", "reason": delivery_error[:500] or None}
        if execution_status != "completed":
            return {"status": "failed", "reason": str(execution.get("error") or "Cron run failed")[:500]}
        if output is None:
            return None
        delivery_key = hashlib.sha256(f"{execution['id']}:{target['id']}".encode()).hexdigest()
        try:
            if target_type == "file":
                destination = self.agents._workspace_path(
                    profile, str(target.get("destination") or ""), require_file=True
                )
                if destination.is_file():
                    payload = destination.read_bytes()
                    digest = hashlib.sha256(payload).hexdigest()
                    relative = destination.relative_to(self.agents.workspace_dir(profile))
                    snapshot = (
                        self._profile_home(profile) / "snapshots" / "workspace"
                        / relative.parent / f"{relative.name}.{time.time_ns()}-{digest[:12]}"
                    )
                    self.repository.atomic_write(snapshot, payload, mode=0o440, replace=False)
                self.repository.atomic_write(destination, output.encode("utf-8"))
            elif target_type == "kanban":
                if self.kanban is None:
                    return {"status": "degraded", "reason": "Kanban is unavailable"}
                self.kanban.create_task(str(target.get("destination") or "default"), {
                    "title": f"{job.get('name') or 'Automation'} result",
                    "description": output[:50_000] or "Cron run completed without text output.",
                    "status": "todo",
                    "idempotency_key": f"cron-delivery:{delivery_key}",
                }, created_by="xnobrain-cron")
            else:
                return {"status": "failed", "reason": "Unsupported delivery target"}
        except Exception:
            reason = "Workspace file delivery failed" if target_type == "file" else "Kanban delivery failed"
            return {"status": "degraded", "reason": reason}
        return {"status": "delivered", "reason": None}

    def _profiles(self) -> list[str]:
        return self.agents.list_agent_names()

    def _jobs_by_profile(
        self,
        profiles: list[str] | None = None,
    ) -> list[tuple[str, list[dict[str, Any]]]]:
        rows: list[tuple[str, list[dict[str, Any]]]] = []
        for profile in profiles if profiles is not None else self._profiles():
            try:
                rows.append((profile, self._native(profile, "list_jobs", True)))
            except Exception:
                continue
        return rows

    def _find(
        self,
        job_id: str,
        profile_jobs: list[tuple[str, list[dict[str, Any]]]] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        wanted = str(job_id or "").strip()
        rows = profile_jobs if profile_jobs is not None else self._jobs_by_profile()
        for profile, jobs in rows:
            for job in jobs:
                if str(job.get("id") or "") == wanted or str(job.get("name") or "") == wanted:
                    return profile, job
        raise CronServiceError("cron job not found", status=404, code="cron_not_found")

    def _find_job(
        self,
        job_id: str,
        agent_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        rows = self._jobs_by_profile([agent_id]) if agent_id else None
        return self._find(job_id, rows)

    def _native(self, profile: str, function: str, *args, **kwargs):
        return self._native_module(profile, "cron.jobs", function, *args, **kwargs)

    def _native_module(self, profile: str, module: str, function: str, *args, **kwargs):
        from importlib import import_module
        from hermes_constants import reset_hermes_home_override, set_hermes_home_override

        home = self._profile_home(profile)
        token = set_hermes_home_override(str(home))
        try:
            loaded = import_module(module)
            if module == "cron.jobs":
                with loaded.use_cron_store(home):
                    return getattr(loaded, function)(*args, **kwargs)
            return getattr(loaded, function)(*args, **kwargs)
        finally:
            reset_hermes_home_override(token)

    def _profile_home(self, agent_id: str) -> Path:
        home = self.agents.profile_path(agent_id)
        if not home.is_dir():
            raise CronServiceError("cron agent profile is unavailable", status=404, code="agent_not_found")
        return home

    def _snapshot_store(self, profile: str) -> None:
        path = self._profile_home(profile) / "cron" / "jobs.json"
        if not path.is_file():
            return
        payload = path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        snapshot = self._profile_home(profile) / "snapshots" / "cron" / "jobs" / f"{time.time_ns()}-{digest[:12]}.json"
        self.repository.atomic_write(snapshot, payload, mode=0o440, replace=False)

    def _snapshot_job_output(self, profile: str, job_id: str) -> None:
        output_dir = self._profile_home(profile) / "cron" / "output" / job_id
        if not output_dir.is_dir():
            return
        snapshot_dir = (
            self._profile_home(profile) / "snapshots" / "cron" / "output"
            / job_id / str(time.time_ns())
        )
        for source in output_dir.glob("*.md"):
            if source.is_file():
                self.repository.atomic_write(
                    snapshot_dir / source.name, source.read_bytes(), mode=0o440, replace=False
                )

    @staticmethod
    def _native_deliver(targets: list[Mapping[str, Any]]) -> str:
        destinations: list[str] = []
        for item in targets:
            target_type = str(item.get("target_type") or "")
            if target_type not in {"channel", "email"}:
                continue
            destination = str(item.get("destination") or "").strip()
            if target_type == "email":
                if not destination or destination == "email":
                    destination = "email"
                elif not destination.startswith("email:"):
                    destination = f"email:{destination}"
            destinations.append(destination)
        return ",".join(item for item in destinations if item) or "local"

    def _target_dtos(self, profile: str, job: Mapping[str, Any]) -> list[dict[str, Any]]:
        targets = list(job.get("xnobrain_delivery_targets") or [])
        needs_options = any(str(item.get("target_type") or "") in {"channel", "email"} for item in targets)
        options = self.list_delivery_target_options(profile)["options"] if needs_options else []
        return [self._target_dto(profile, job, item, options) for item in targets]

    def _target_dto(
        self,
        profile: str,
        job: Mapping[str, Any],
        target: Mapping[str, Any],
        options: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        target_type = str(target.get("target_type") or "")
        destination = str(target.get("destination") or "")
        available, reason = True, None
        if target_type in {"channel", "email"}:
            option_id = destination.split(":", 1)[0] or "email"
            available_options = options if options is not None else self.list_delivery_target_options(profile)["options"]
            option = next((item for item in available_options if item["target_type"] == target_type and item["id"] == option_id), None)
            available = bool(option and option.get("available"))
            reason = None if available else "Delivery target is not configured"
        return {
            "id": str(target.get("id") or ""),
            "target_type": target_type,
            "destination": destination,
            "available": available,
            "degraded_reason": reason,
        }

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
        result["delivery_targets"] = [dict(item) for item in job.get("xnobrain_delivery_targets") or []]
        result["kanban_board"] = str(job.get("xnobrain_kanban_board") or "") or None
        result["kanban_task_id"] = str(job.get("xnobrain_kanban_task_id") or "") or None
        result.pop("xnobrain_delivery_records", None)
        result.pop("origin", None)
        return result

    def _executions(self, profile: str, job: Mapping[str, Any], limit: int) -> list[dict[str, Any]]:
        job_id = str(job["id"])
        path = self._profile_home(profile) / "cron" / "executions.db"
        rows: list[Any] = []
        if path.is_file():
            try:
                conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
                conn.row_factory = sqlite3.Row
                try:
                    rows = conn.execute(
                        "SELECT id, job_id, status, claimed_at, started_at, finished_at, error "
                        "FROM executions WHERE job_id=? ORDER BY claimed_at DESC, id DESC LIMIT ?",
                        (job_id, max(1, min(limit, 100))),
                    ).fetchall()
                finally:
                    conn.close()
            except sqlite3.Error:
                rows = []
        if rows:
            return [dict(row) for row in rows]

        # Older/native scheduler paths persist one atomic Markdown artifact per
        # run but may not create executions.db. Treat those artifacts as the
        # durable run store so successful results are still reconciled.
        output_dir = self._profile_home(profile) / "cron" / "output" / job_id
        artifacts = sorted(
            (item for item in output_dir.glob("*.md") if item.is_file()),
            key=lambda item: (item.stat().st_mtime_ns, item.name),
            reverse=True,
        )[:max(1, min(limit, 100))]
        synthesized: list[dict[str, Any]] = []
        for index, artifact in enumerate(artifacts):
            finished = datetime.fromtimestamp(artifact.stat().st_mtime, timezone.utc)
            text = artifact.read_text(encoding="utf-8", errors="replace")
            failed = "\n## Error\n" in text or (
                index == 0 and str(job.get("last_status") or "ok") != "ok"
            )
            synthesized.append({
                "id": f"output-{artifact.name}",
                "job_id": job_id,
                "status": "failed" if failed else "completed",
                "claimed_at": _iso(finished),
                "started_at": _iso(finished),
                "finished_at": _iso(finished),
                "error": str(job.get("last_error") or "") if failed and index == 0 else "",
                "output_path": str(artifact),
            })
        return synthesized

    def _run_dtos(self, profile: str, job: Mapping[str, Any], limit: int) -> list[dict[str, Any]]:
        records = [dict(item) for item in job.get("xnobrain_delivery_records") or []]
        by_execution: dict[str, list[dict[str, Any]]] = {}
        for item in records:
            by_execution.setdefault(str(item.get("execution_id") or ""), []).append({
                "target_id": str(item.get("target_id") or ""),
                "target_type": str(item.get("target_type") or ""),
                "status": str(item.get("status") or ""),
                "at": str(item.get("at") or ""),
                "reason": item.get("reason"),
            })
        result = []
        for execution in self._executions(profile, job, limit):
            state = {"claimed": "running", "running": "running", "completed": "success"}.get(str(execution["status"]), "failed")
            result.append({
                "id": str(execution["id"]),
                "occurrence_id": str(execution["id"]),
                "state": state,
                "triggered_at": str(execution.get("claimed_at") or ""),
                "completed_at": str(execution.get("finished_at") or ""),
                "error": str(execution.get("error") or "")[:4_000],
                "deliveries": by_execution.get(str(execution["id"]), []),
            })
        return result

    def _execution_output(self, profile: str, job: Mapping[str, Any], execution: Mapping[str, Any]) -> str | None:
        output_path = str(execution.get("output_path") or "")
        if output_path:
            candidate = Path(output_path)
            output_root = self._profile_home(profile) / "cron" / "output" / str(job["id"])
            try:
                if candidate.resolve().parent != output_root.resolve() or not candidate.is_file():
                    return None
            except OSError:
                return None
            text = candidate.read_text(encoding="utf-8", errors="replace")
            if "\n## Response\n" in text:
                return text.split("\n## Response\n", 1)[1].strip()[:50_000]
            if "\n## Error\n" in text:
                return ""
            return text.strip()[:50_000]
        finished = str(execution.get("finished_at") or "")
        cutoff = None
        try:
            cutoff = datetime.fromisoformat(finished.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
        output_dir = self._profile_home(profile) / "cron" / "output" / str(job["id"])
        candidates = sorted(output_dir.glob("*.md"), key=lambda path: path.stat().st_mtime, reverse=True)
        if cutoff is not None:
            candidates = [path for path in candidates if path.stat().st_mtime <= cutoff + 120]
        if not candidates:
            return None
        text = candidates[0].read_text(encoding="utf-8", errors="replace")
        if "\n## Response\n" in text:
            return text.split("\n## Response\n", 1)[1].strip()[:50_000]
        if "\n## Error\n" in text:
            return ""
        return text.strip()[:50_000]

    def _latest_run(
        self,
        profile: str,
        job: Mapping[str, Any],
        known_runs: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        runs = known_runs if known_runs is not None else self._run_dtos(profile, job, 1)
        if runs:
            run = dict(runs[0])
            run["output"] = self._execution_output(profile, job, {"finished_at": run["completed_at"]}) or ""
            return run
        last_at = str(job.get("last_run_at") or "")
        if not last_at:
            return None
        output = self._execution_output(profile, job, {"finished_at": last_at}) or ""
        return {
            "id": f"{job['id']}-{last_at}",
            "state": "success" if str(job.get("last_status") or "ok") == "ok" else "failed",
            "triggered_at": last_at,
            "completed_at": last_at,
            "output": output,
            "error": str(job.get("last_error") or "")[:4_000],
            "deliveries": [],
        }


__all__ = ["CronService", "CronServiceError"]
