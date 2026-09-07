"""Team definition and team-run persistence."""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

import yaml

from .base import TEAM_RUN_RETENTION, StoreError


class TeamRepositoryMixin:
    """Team definition and execution record operations."""

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

            files.sort(
                key=lambda path: (
                    _created_at(path) or "",
                    path.stat().st_mtime if path.exists() else 0.0,
                ),
                reverse=True,
            )
            for path in files[max(0, int(keep)) :]:
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
