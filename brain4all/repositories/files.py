"""Atomic filesystem repository for profile, cron, team, and snapshot state."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time
from typing import Any, Mapping
import uuid

import yaml


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
TEAM_RUN_RETENTION = 100


class StoreError(ValueError):
    """An expected filesystem contract error."""

    def __init__(self, message: str, *, status: int = 400, code: str = "invalid_store_request"):
        super().__init__(message)
        self.status = status
        self.code = code


class FileRepository:
    """Small, lock-protected repositories rooted in ``DATA_DIR``.

    Hermes remains authoritative for sessions and provider credentials. Brain4All
    owns only product metadata that Hermes does not already persist.
    """

    def __init__(self, data_dir: str | Path, profiles_root: str | Path):
        self.data_dir = Path(data_dir).resolve()
        self.profiles_root = Path(profiles_root).resolve()
        self.teams_root = self.data_dir / "teams"
        self.team_runs_root = self.teams_root / "runs"
        self.notifications_root = self.data_dir / "notifications"
        self.trash_root = self.data_dir / "trash" / "profiles"
        self._lock = threading.RLock()
        for path in (self.data_dir, self.profiles_root, self.teams_root, self.team_runs_root, self.notifications_root, self.trash_root):
            path.mkdir(parents=True, exist_ok=True, mode=0o750)

    @staticmethod
    def _id(value: Any, field: str = "id") -> str:
        result = str(value or "").strip()
        if not _SAFE_ID.fullmatch(result):
            raise StoreError(f"invalid {field}")
        return result

    def profile_path(self, agent_id: Any) -> Path:
        agent_id = self._id(agent_id, "agent id")
        candidate = (self.profiles_root / agent_id).resolve()
        if candidate.parent != self.profiles_root:
            raise StoreError("agent path escapes profiles root")
        return candidate

    def soft_delete_profile(self, agent_id: Any) -> Path:
        source = self.profile_path(agent_id)
        if not source.is_dir():
            raise StoreError("agent not found", status=404, code="not_found")
        target = self.trash_root / f"{source.name}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        with self._lock:
            os.replace(source, target)
            self._sync_dir(source.parent)
            self._sync_dir(target.parent)
        return target

    def hard_delete_profile(self, agent_id: Any) -> None:
        source = self.profile_path(agent_id)
        if not source.is_dir():
            raise StoreError("agent not found", status=404, code="not_found")
        with self._lock:
            shutil.rmtree(source)
            self._sync_dir(source.parent)

    def snapshot(self, agent_id: Any, kind: str, target: str, content: bytes) -> dict[str, Any]:
        agent_id = self._id(agent_id, "agent id")
        if kind not in {"memory", "skills", "config"}:
            raise StoreError("invalid snapshot kind")
        target = self._id(target, "snapshot target")
        digest = hashlib.sha256(content).hexdigest()
        snapshot_id = f"{time.time_ns()}-{digest[:12]}"
        suffix = ".yaml" if kind == "config" else ".md"
        relative = Path("snapshots") / kind / target / f"{snapshot_id}{suffix}"
        path = self.profile_path(agent_id) / relative
        with self._lock:
            self.atomic_write(path, content, mode=0o440, replace=False)
        return {
            "id": snapshot_id,
            "agent_id": agent_id,
            "kind": kind,
            "target": target,
            "hash": digest,
            "path": relative.as_posix(),
            "created_at": time.time(),
        }

    def list_snapshots(self, agent_id: Any, kind: str | None = None) -> list[dict[str, Any]]:
        agent_id = self._id(agent_id, "agent id")
        root = self.profile_path(agent_id) / "snapshots"
        if not root.is_dir():
            return []
        result: list[dict[str, Any]] = []
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(self.profile_path(agent_id))
            parts = relative.parts
            if len(parts) != 4 or parts[0] != "snapshots":
                continue
            current_kind, target = parts[1], parts[2]
            if kind and current_kind != kind:
                continue
            payload = path.read_bytes()
            result.append({
                "id": path.stem,
                "agent_id": agent_id,
                "kind": current_kind,
                "target": target,
                "hash": hashlib.sha256(payload).hexdigest(),
                "path": relative.as_posix(),
                "created_at": path.stat().st_mtime,
            })
        return sorted(result, key=lambda item: item["created_at"], reverse=True)

    def restore_snapshot(self, agent_id: Any, snapshot_id: Any) -> dict[str, Any]:
        snapshot_id = self._id(snapshot_id, "snapshot id")
        matches = [item for item in self.list_snapshots(agent_id) if item["id"] == snapshot_id]
        if not matches:
            raise StoreError("snapshot not found", status=404, code="not_found")
        item = matches[0]
        profile = self.profile_path(agent_id)
        source = profile / item["path"]
        if item["kind"] == "skills":
            destination = profile / "skills" / "custom" / item["target"] / "SKILL.md"
            skills_root = profile / "skills"
            if skills_root.is_dir():
                for skill_file in skills_root.rglob("SKILL.md"):
                    if skill_file.parent.name == item["target"]:
                        destination = skill_file
                        break
        elif item["kind"] == "memory":
            filename = "USER.md" if item["target"] == "user" else "MEMORY.md"
            destination = profile / "memories" / filename
        else:
            destination = profile / "config.yaml"
        self.atomic_write(destination, source.read_bytes(), mode=0o640)
        return item

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

    def list_teams(self) -> list[dict[str, Any]]:
        result = []
        for path in sorted(self.teams_root.glob("*.yaml")):
            try:
                item = self._read_yaml(path)
                if isinstance(item, dict):
                    result.append(item)
            except (OSError, yaml.YAMLError):
                continue
        return result

    def get_team(self, team_id: Any) -> dict[str, Any]:
        path = self.teams_root / f"{self._id(team_id, 'team id')}.yaml"
        if not path.is_file():
            raise StoreError("team not found", status=404, code="not_found")
        item = self._read_yaml(path)
        if not isinstance(item, dict):
            raise StoreError("team is invalid", status=500, code="invalid_team")
        return item

    def put_team(self, item: Mapping[str, Any]) -> dict[str, Any]:
        team = dict(item)
        path = self.teams_root / f"{self._id(team.get('id'), 'team id')}.yaml"
        self.atomic_yaml(path, team)
        return team

    def delete_team(self, team_id: Any) -> bool:
        path = self.teams_root / f"{self._id(team_id, 'team id')}.yaml"
        with self._lock:
            if not path.is_file():
                return False
            path.unlink()
            self._sync_dir(path.parent)
            return True

    def _team_run_dir(self, team_id: Any) -> Path:
        return self.team_runs_root / self._id(team_id, "team id")

    def list_team_runs(self, team_id: Any, limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(100, int(limit or 20)))
        directory = self._team_run_dir(team_id)
        if not directory.is_dir():
            return []
        result: list[dict[str, Any]] = []
        for path in directory.glob("*.json"):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(item, dict):
                result.append(item)
        result.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return result[:limit]

    def get_team_run(self, team_id: Any, run_id: Any) -> dict[str, Any]:
        path = self._team_run_dir(team_id) / f"{self._id(run_id, 'run id')}.json"
        if not path.is_file():
            raise StoreError("team run not found", status=404, code="run_not_found")
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise StoreError("team run is invalid", status=500, code="invalid_team_run") from error
        if not isinstance(item, dict):
            raise StoreError("team run is invalid", status=500, code="invalid_team_run")
        return item

    def put_team_run(self, record: Mapping[str, Any]) -> dict[str, Any]:
        run = dict(record)
        team_id = self._id(run.get("team_id"), "team id")
        run_id = self._id(run.get("id"), "run id")
        path = self._team_run_dir(team_id) / f"{run_id}.json"
        self.atomic_json(path, run)
        self.prune_team_runs(team_id)
        return run

    def delete_team_run(self, team_id: Any, run_id: Any) -> bool:
        path = self._team_run_dir(team_id) / f"{self._id(run_id, 'run id')}.json"
        with self._lock:
            if not path.is_file():
                return False
            path.unlink()
            self._sync_dir(path.parent)
            return True

    def prune_team_runs(self, team_id: Any, keep: int = TEAM_RUN_RETENTION) -> int:
        directory = self._team_run_dir(team_id)
        if not directory.is_dir():
            return 0
        removed = 0
        with self._lock:
            files = list(directory.glob("*.json"))

            def _created_at(path: Path) -> str:
                try:
                    item = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(item, dict) and item.get("created_at"):
                        return str(item["created_at"])
                except (OSError, json.JSONDecodeError):
                    pass
                return ""

            files.sort(key=lambda path: (_created_at(path) or "", path.stat().st_mtime if path.exists() else 0.0), reverse=True)
            for path in files[max(0, int(keep)):]:
                try:
                    path.unlink()
                    removed += 1
                except FileNotFoundError:
                    pass
            if removed:
                self._sync_dir(directory)
        return removed

    def delete_team_runs(self, team_id: Any) -> bool:
        directory = self._team_run_dir(team_id)
        with self._lock:
            existed = directory.is_dir()
            if existed:
                shutil.rmtree(directory, ignore_errors=False)
                self._sync_dir(self.team_runs_root)
            return existed

    def list_notifications(self) -> list[dict[str, Any]]:
        result = []
        for path in sorted(self.notifications_root.glob("*.json")):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(item, dict):
                    result.append(item)
            except (OSError, json.JSONDecodeError):
                continue
        return result

    def put_notification(self, item: Mapping[str, Any]) -> dict[str, Any]:
        notification = dict(item)
        path = self.notifications_root / f"{self._id(notification.get('id'), 'notification id')}.json"
        self.atomic_json(path, notification)
        return notification

    def resolve_notification(self, notification_id: Any) -> dict[str, Any]:
        path = self.notifications_root / f"{self._id(notification_id, 'notification id')}.json"
        if not path.is_file():
            raise StoreError("notification not found", status=404, code="not_found")
        try:
            notification = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise StoreError("notification is invalid", status=500, code="invalid_notification") from error
        notification["resolved"] = True
        notification["resolved_at"] = time.time()
        self.atomic_json(path, notification)
        return notification

    def atomic_json(self, path: Path, value: Any) -> None:
        self.atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())

    def atomic_yaml(self, path: Path, value: Any) -> None:
        self.atomic_write(path, yaml.safe_dump(value, sort_keys=False, allow_unicode=True).encode())

    def atomic_write(self, path: Path, payload: bytes, *, mode: int = 0o640, replace: bool = True) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        with self._lock:
            if not replace and path.exists():
                raise StoreError("immutable snapshot already exists", status=409, code="snapshot_exists")
            fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
            try:
                os.fchmod(fd, mode)
                with os.fdopen(fd, "wb") as file:
                    file.write(payload)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temporary, path)
                self._sync_dir(path.parent)
            except Exception:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
                raise

    def _profile_dirs(self) -> list[Path]:
        return [path for path in self.profiles_root.iterdir() if path.is_dir() and not path.is_symlink()]

    @staticmethod
    def _read_yaml(path: Path) -> Any:
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    @staticmethod
    def _sync_dir(path: Path) -> None:
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            pass
