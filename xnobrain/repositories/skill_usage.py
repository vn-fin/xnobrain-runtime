"""Durable, privacy-safe skill lifecycle event persistence."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .base import StoreError

SKILL_USAGE_SCHEMA_VERSION = 1
SKILL_USAGE_INSTRUMENTATION_VERSION = "xnobrain.skill-usage.v1"
_SKILL_EVENT_TYPES = {
    "skill.requested",
    "skill.loaded",
    "skill.reference_read",
    "skill.tool_invoked",
    "skill.tool_completed",
    "skill.tool_failed",
}
_SAFE_FIELDS = {
    "schema_version",
    "instrumentation_version",
    "event_id",
    "event_type",
    "agent_id",
    "work_context_id",
    "run_id",
    "session_id",
    "skill_id",
    "skill_digest",
    "associated_skills",
    "attribution",
    "occurred_at",
    "duration_ms",
    "outcome",
    "tool_name",
}


class SkillUsageRepositoryMixin:
    """Store immutable events below an agent-owned profile metadata root."""

    def _skill_usage_events_dir(self, agent_id: Any) -> Path:
        agent = self._id(agent_id, "agent id")
        return self.profiles_root / agent / "skill-usage" / "v1" / "events"

    def append_skill_usage_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        """Persist one allowlisted event, idempotently by stable event ID."""
        item = self._validate_skill_usage_event(event)
        directory = self._skill_usage_events_dir(item["agent_id"])
        path = directory / f"{item['event_id']}.json"
        with self._lock:
            if path.is_file():
                existing = self._read_skill_usage_event(path)
                if existing == item:
                    return existing
                raise StoreError(
                    "skill usage event id conflict",
                    status=409,
                    code="skill_usage_event_conflict",
                )
            payload = (json.dumps(item, ensure_ascii=False, indent=2) + "\n").encode()
            self.atomic_write(path, payload, replace=False)
        return item

    def has_skill_usage_events(self, agent_id: Any) -> bool:
        directory = self._skill_usage_events_dir(agent_id)
        return directory.is_dir() and any(directory.glob("*.json"))

    def list_skill_usage_events(
        self,
        agent_id: Any,
        *,
        start_epoch: float,
        end_epoch: float,
        work_context_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read valid events in the half-open ``(start, end]`` interval."""
        directory = self._skill_usage_events_dir(agent_id)
        if not directory.is_dir():
            return []
        result: list[tuple[float, str, dict[str, Any]]] = []
        for path in directory.glob("*.json"):
            try:
                item = self._read_skill_usage_event(path)
                occurred = _timestamp(item["occurred_at"])
            except (OSError, ValueError, StoreError):
                continue
            if not start_epoch < occurred <= end_epoch:
                continue
            if work_context_id and item["work_context_id"] != work_context_id:
                continue
            result.append((occurred, item["event_id"], item))
        result.sort(key=lambda row: (row[0], row[1]))
        return [row[2] for row in result]

    def _read_skill_usage_event(self, path: Path) -> dict[str, Any]:
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise StoreError(
                "skill usage event is invalid",
                status=500,
                code="invalid_skill_usage_event",
            ) from error
        if not isinstance(item, dict):
            raise StoreError(
                "skill usage event is invalid",
                status=500,
                code="invalid_skill_usage_event",
            )
        return self._validate_skill_usage_event(item)

    def _validate_skill_usage_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(event, Mapping):
            raise StoreError("skill usage event must be an object")
        unexpected = set(event) - _SAFE_FIELDS
        if unexpected:
            raise StoreError("skill usage event contains unsupported fields")
        item = dict(event)
        if item.get("schema_version") != SKILL_USAGE_SCHEMA_VERSION:
            raise StoreError("unsupported skill usage schema version")
        if item.get("instrumentation_version") != SKILL_USAGE_INSTRUMENTATION_VERSION:
            raise StoreError("unsupported skill usage instrumentation version")
        event_id = self._id(item.get("event_id"), "event id")
        if not event_id.startswith("sue_"):
            raise StoreError("invalid event id")
        event_type = str(item.get("event_type") or "")
        if event_type not in _SKILL_EVENT_TYPES:
            raise StoreError("invalid skill usage event type")
        agent_id = self._id(item.get("agent_id"), "agent id")
        for field in ("work_context_id", "run_id", "session_id"):
            value = str(item.get(field) or "").strip()
            if not value or len(value) > 256 or any(char in value for char in "\x00\r\n"):
                raise StoreError(f"invalid {field.replace('_', ' ')}")
            item[field] = value
        skill_id = item.get("skill_id")
        if skill_id is not None:
            item["skill_id"] = _safe_label(skill_id, "skill id", 256)
        digest = item.get("skill_digest")
        if digest is not None:
            digest = str(digest)
            if not digest.startswith("sha256:") or len(digest) != 71:
                raise StoreError("invalid skill digest")
            try:
                int(digest[7:], 16)
            except ValueError as error:
                raise StoreError("invalid skill digest") from error
            item["skill_digest"] = digest
        associated = item.get("associated_skills")
        if associated is not None:
            if not isinstance(associated, list) or len(associated) > 64:
                raise StoreError("invalid associated skills")
            normalized = []
            for association in associated:
                if not isinstance(association, Mapping):
                    raise StoreError("invalid associated skills")
                extra = set(association) - {"skill_id", "skill_digest"}
                if extra:
                    raise StoreError("invalid associated skills")
                association_id = _safe_label(association.get("skill_id"), "skill id", 256)
                association_digest = association.get("skill_digest")
                if association_digest is not None:
                    association_digest = str(association_digest)
                    if (
                        not association_digest.startswith("sha256:")
                        or len(association_digest) != 71
                    ):
                        raise StoreError("invalid skill digest")
                    try:
                        int(association_digest[7:], 16)
                    except ValueError as error:
                        raise StoreError("invalid skill digest") from error
                normalized.append(
                    {
                        "skill_id": association_id,
                        "skill_digest": association_digest,
                    }
                )
            item["associated_skills"] = normalized
        attribution = str(item.get("attribution") or "")
        if attribution not in {"observed", "multiple", "unattributed"}:
            raise StoreError("invalid skill usage attribution")
        item["attribution"] = attribution
        occurred_at = str(item.get("occurred_at") or "")
        try:
            _timestamp(occurred_at)
        except ValueError as error:
            raise StoreError("invalid skill usage timestamp") from error
        item["occurred_at"] = occurred_at
        duration = item.get("duration_ms")
        if duration is not None:
            if isinstance(duration, bool) or not isinstance(duration, int) or duration < 0:
                raise StoreError("invalid skill usage duration")
        outcome = item.get("outcome")
        if outcome is not None and outcome not in {"completed", "failed"}:
            raise StoreError("invalid skill usage outcome")
        tool_name = item.get("tool_name")
        if tool_name is not None:
            item["tool_name"] = _safe_label(tool_name, "tool name", 128)
        item["event_id"] = event_id
        item["event_type"] = event_type
        item["agent_id"] = agent_id
        return item


def _safe_label(value: Any, field: str, maximum: int) -> str:
    result = str(value or "").strip()
    if not result or len(result) > maximum or any(char in result for char in "\x00\r\n"):
        raise StoreError(f"invalid {field}")
    return result


def _timestamp(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
