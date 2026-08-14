"""Persistent conversation-run records and immutable event journals."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Any, Mapping

from .base import StoreError


class ConversationRunRepositoryMixin:
    """Atomic profile-owned persistence for reconnectable chat runs."""

    def _conversation_run_dir(self, agent_id: Any, conversation_id: Any) -> Path:
        agent = self._id(agent_id, "agent id")
        conversation = self._id(conversation_id, "conversation id")
        return self.profiles_root / agent / "conversation-runs" / conversation

    def put_conversation_run(self, record: Mapping[str, Any]) -> dict[str, Any]:
        run = dict(record)
        directory = self._conversation_run_dir(run.get("agent_id"), run.get("conversation_id"))
        run_id = self._id(run.get("id"), "run id")
        self.atomic_json(directory / f"{run_id}.json", run)
        return run

    def get_conversation_run(self, agent_id: Any, conversation_id: Any, run_id: Any) -> dict[str, Any]:
        path = self._conversation_run_dir(agent_id, conversation_id) / f"{self._id(run_id, 'run id')}.json"
        if not path.is_file():
            raise StoreError("conversation run not found", status=404, code="run_not_found")
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise StoreError("conversation run is invalid", status=500, code="invalid_conversation_run") from error
        if not isinstance(item, dict):
            raise StoreError("conversation run is invalid", status=500, code="invalid_conversation_run")
        return item

    def list_conversation_runs(self, agent_id: Any, conversation_id: Any, limit: int = 20) -> list[dict[str, Any]]:
        directory = self._conversation_run_dir(agent_id, conversation_id)
        if not directory.is_dir():
            return []
        result: list[dict[str, Any]] = []
        for path in directory.glob("run_*.json"):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(item, dict):
                result.append(item)
        result.sort(key=lambda item: float(item.get("created_at") or 0), reverse=True)
        return result[:max(1, min(100, int(limit or 20)))]

    def append_conversation_run_event(
        self,
        agent_id: Any,
        conversation_id: Any,
        run_id: Any,
        event: Mapping[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            record = self.get_conversation_run(agent_id, conversation_id, run_id)
            sequence = int(record.get("revision") or 0) + 1
            item = {"sequence": sequence, **dict(event)}
            directory = self._conversation_run_dir(agent_id, conversation_id) / f"{self._id(run_id, 'run id')}.events"
            self.atomic_json(directory / f"{sequence:08d}.json", item)
            return item

    def list_conversation_run_events(
        self,
        agent_id: Any,
        conversation_id: Any,
        run_id: Any,
        after: int = 0,
    ) -> list[dict[str, Any]]:
        directory = self._conversation_run_dir(agent_id, conversation_id) / f"{self._id(run_id, 'run id')}.events"
        if not directory.is_dir():
            return []
        result: list[dict[str, Any]] = []
        for path in sorted(directory.glob("*.json")):
            try:
                sequence = int(path.stem)
            except ValueError:
                continue
            if sequence <= max(0, int(after or 0)):
                continue
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(item, dict):
                result.append(item)
        return result

    def delete_conversation_runs(self, agent_id: Any, conversation_id: Any) -> bool:
        directory = self._conversation_run_dir(agent_id, conversation_id)
        with self._lock:
            if not directory.is_dir():
                return False
            shutil.rmtree(directory)
            self._sync_dir(directory.parent)
            return True
