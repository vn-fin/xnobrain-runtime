"""Durable, bounded, privacy-safe skill lifecycle event persistence."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from .base import StoreError

SKILL_USAGE_SCHEMA_VERSION = 1
SKILL_USAGE_INSTRUMENTATION_VERSION = "xnobrain.skill-usage.v1"
SKILL_USAGE_RAW_RETENTION_DAYS = 90
SKILL_USAGE_ROLLUP_RETENTION_DAYS = 730
SKILL_USAGE_MAX_EVENTS_PER_DAY = 10_000
SKILL_USAGE_MAX_QUERY_DAYS = 366
SKILL_USAGE_MAX_LEGACY_FILES = 20_000
SKILL_USAGE_MAX_ROLLUP_BYTES = 4 * 1024 * 1024
_SKILL_EVENT_TYPES = {
    "skill.requested",
    "skill.loaded",
    "skill.run_associated",
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
    """Store immutable events and bounded daily rollups under one profile."""

    def _skill_usage_root(self, agent_id: Any) -> Path:
        agent = self._id(agent_id, "agent id")
        profile = self.profiles_root / agent
        if profile.is_symlink():
            raise StoreError("skill usage profile must not be a symlink")
        root = profile / "skill-usage" / "v1"
        if root.is_symlink():
            raise StoreError("skill usage storage must not be a symlink")
        resolved = root.resolve(strict=False)
        if profile.resolve(strict=False) not in resolved.parents:
            raise StoreError("skill usage storage escapes profile")
        return resolved

    def _skill_usage_events_dir(self, agent_id: Any) -> Path:
        return self._skill_usage_root(agent_id) / "events"

    def _skill_usage_rollups_dir(self, agent_id: Any) -> Path:
        return self._skill_usage_root(agent_id) / "rollups"

    def append_skill_usage_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        """Persist one allowlisted event and idempotently update its rollup."""
        item = self._validate_skill_usage_event(event)
        day = _day(item["occurred_at"])
        directory = self._skill_usage_events_dir(item["agent_id"]) / day
        path = directory / f"{item['event_id']}.json"
        with self._lock:
            if path.is_file():
                existing = self._read_skill_usage_event(path)
                if existing != item:
                    raise StoreError(
                        "skill usage event id conflict",
                        status=409,
                        code="skill_usage_event_conflict",
                    )
                self._ensure_skill_usage_rollup(item)
                return existing
            legacy = self._skill_usage_events_dir(item["agent_id"]) / path.name
            if legacy.is_file():
                existing = self._read_skill_usage_event(legacy)
                if existing != item:
                    raise StoreError(
                        "skill usage event id conflict",
                        status=409,
                        code="skill_usage_event_conflict",
                    )
                self._ensure_skill_usage_rollup(item)
                return existing
            if directory.is_dir() and sum(1 for entry in directory.glob("*.json")) >= (
                SKILL_USAGE_MAX_EVENTS_PER_DAY
            ):
                raise StoreError(
                    "daily skill usage event limit reached",
                    status=507,
                    code="skill_usage_daily_limit",
                )
            payload = (json.dumps(item, ensure_ascii=False, indent=2) + "\n").encode()
            self.atomic_write(path, payload, replace=False)
            self._ensure_skill_usage_rollup(item)
            self._update_skill_usage_state(item["agent_id"], accepted=1, item=item)
            self._prune_skill_usage(item["agent_id"], datetime.now(timezone.utc).timestamp())
        return item

    def record_skill_usage_gap(self, agent_id: Any) -> None:
        """Best-effort durable indication that one instrumentation event was lost."""
        try:
            with self._lock:
                self._update_skill_usage_state(agent_id, dropped=1)
        except Exception:
            return

    def skill_usage_state(self, agent_id: Any) -> dict[str, Any]:
        path = self._skill_usage_root(agent_id) / "coverage.json"
        if not path.is_file() or path.is_symlink():
            return {
                "accepted_events": 0,
                "dropped_events": 0,
                "coverage_start": None,
                "coverage_end": None,
            }
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {
                "accepted_events": 0,
                "dropped_events": 1,
                "coverage_start": None,
                "coverage_end": None,
            }
        return value if isinstance(value, dict) else {}

    def _update_skill_usage_state(
        self,
        agent_id: Any,
        *,
        accepted: int = 0,
        dropped: int = 0,
        item: Mapping[str, Any] | None = None,
    ) -> None:
        root = self._skill_usage_root(agent_id)
        state = self.skill_usage_state(agent_id)
        state = {
            "schema_version": 1,
            "instrumentation_version": SKILL_USAGE_INSTRUMENTATION_VERSION,
            "accepted_events": max(0, int(state.get("accepted_events") or 0)) + accepted,
            "dropped_events": max(0, int(state.get("dropped_events") or 0)) + dropped,
            "coverage_start": state.get("coverage_start"),
            "coverage_end": state.get("coverage_end"),
            "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        if item is not None:
            occurred = str(item["occurred_at"])
            if not state["coverage_start"] or occurred < state["coverage_start"]:
                state["coverage_start"] = occurred
            if not state["coverage_end"] or occurred > state["coverage_end"]:
                state["coverage_end"] = occurred
        self.atomic_json(root / "coverage.json", state)

    def _ensure_skill_usage_rollup(self, item: Mapping[str, Any]) -> None:
        day = _day(str(item["occurred_at"]))
        path = self._skill_usage_rollups_dir(item["agent_id"]) / f"{day}.json"
        rollup = self._read_rollup(path, day)
        event_ids = rollup.setdefault("event_ids", [])
        if item["event_id"] in event_ids:
            return
        event_ids.append(item["event_id"])
        rollup["event_count"] = int(rollup.get("event_count") or 0) + 1
        context_row = rollup.setdefault("contexts", {}).setdefault(
            item["work_context_id"],
            {
                "event_count": 0,
                "total_tool_invocations": 0,
                "unattributed_tool_invocations": 0,
                "multiple_attributed_tool_invocations": 0,
            },
        )
        context_row["event_count"] += 1
        rollup["from"] = min(filter(None, (rollup.get("from"), item["occurred_at"])))
        rollup["to"] = max(filter(None, (rollup.get("to"), item["occurred_at"])))
        rows = rollup.setdefault("items", {})
        for association in _event_associations(item):
            key = json.dumps(
                [
                    item["work_context_id"],
                    association.get("skill_id"),
                    association.get("skill_digest"),
                ],
                separators=(",", ":"),
            )
            row = rows.setdefault(
                key,
                {
                    "skill_id": association.get("skill_id"),
                    "skill_digest": association.get("skill_digest"),
                    "requested_count": 0,
                    "loaded_count": 0,
                    "reference_reads": 0,
                    "tool_invocations": 0,
                    "tool_completed": 0,
                    "errors": 0,
                    "run_ids": [],
                    "session_ids": [],
                    "duration_total_ms": 0,
                    "duration_count": 0,
                    "last_used_at": None,
                    "multiple_attribution": False,
                },
            )
            _accumulate_rollup_row(row, item)
        if item["event_type"] == "skill.tool_invoked":
            if not _event_associations(item):
                rollup["unattributed_tool_invocations"] += 1
            elif item.get("attribution") == "multiple":
                rollup["multiple_attributed_tool_invocations"] += 1
            rollup["total_tool_invocations"] += 1
            context_row["total_tool_invocations"] += 1
            if not _event_associations(item):
                context_row["unattributed_tool_invocations"] += 1
            elif item.get("attribution") == "multiple":
                context_row["multiple_attributed_tool_invocations"] += 1
        payload = (json.dumps(rollup, ensure_ascii=False, indent=2) + "\n").encode()
        if len(payload) > SKILL_USAGE_MAX_ROLLUP_BYTES:
            raise StoreError(
                "daily skill usage rollup limit reached",
                status=507,
                code="skill_usage_rollup_limit",
            )
        self.atomic_write(path, payload)

    @staticmethod
    def _read_rollup(path: Path, day: str) -> dict[str, Any]:
        if path.is_file() and not path.is_symlink():
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(value, dict) and value.get("day") == day:
                    return value
            except (OSError, json.JSONDecodeError):
                pass
        return {
            "schema_version": 1,
            "instrumentation_version": SKILL_USAGE_INSTRUMENTATION_VERSION,
            "day": day,
            "from": None,
            "to": None,
            "event_count": 0,
            "event_ids": [],
            "items": {},
            "total_tool_invocations": 0,
            "unattributed_tool_invocations": 0,
            "multiple_attributed_tool_invocations": 0,
            "contexts": {},
        }

    def _prune_skill_usage(self, agent_id: Any, now_epoch: float) -> None:
        raw_cutoff = datetime.fromtimestamp(now_epoch, timezone.utc).date() - timedelta(
            days=SKILL_USAGE_RAW_RETENTION_DAYS
        )
        rollup_cutoff = datetime.fromtimestamp(now_epoch, timezone.utc).date() - timedelta(
            days=SKILL_USAGE_ROLLUP_RETENTION_DAYS
        )
        events = self._skill_usage_events_dir(agent_id)
        if events.is_dir():
            for path in events.iterdir():
                if not path.is_dir() or path.is_symlink():
                    continue
                try:
                    date = datetime.strptime(path.name, "%Y-%m-%d").date()
                except ValueError:
                    continue
                if date < raw_cutoff:
                    for event_path in path.glob("*.json"):
                        (events / event_path.name).unlink(missing_ok=True)
                    shutil.rmtree(path)
        rollups = self._skill_usage_rollups_dir(agent_id)
        if rollups.is_dir():
            for path in rollups.glob("*.json"):
                try:
                    date = datetime.strptime(path.stem, "%Y-%m-%d").date()
                except ValueError:
                    continue
                if date < rollup_cutoff:
                    path.unlink()
        for directory in (events, rollups):
            if directory.is_dir():
                self._sync_dir(directory)

    def has_skill_usage_events(self, agent_id: Any) -> bool:
        events = self._skill_usage_events_dir(agent_id)
        rollups = self._skill_usage_rollups_dir(agent_id)
        return bool(
            (events.is_dir() and next(events.rglob("*.json"), None) is not None)
            or (rollups.is_dir() and next(rollups.glob("*.json"), None) is not None)
        )

    def list_skill_usage_events(
        self,
        agent_id: Any,
        *,
        start_epoch: float,
        end_epoch: float,
        work_context_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Compatibility raw reader, bounded by retained days and file limits."""
        event_root = self._skill_usage_events_dir(agent_id)
        if not event_root.is_dir():
            return []
        paths = [path for path in event_root.rglob("*.json") if path.is_file()]
        result = self._read_event_paths(
            paths,
            start_epoch,
            end_epoch,
            {work_context_id} if work_context_id else None,
        )
        deduplicated = {item["event_id"]: item for item in result}
        return sorted(
            deduplicated.values(),
            key=lambda item: (item["occurred_at"], item["event_id"]),
        )

    def read_skill_usage_window(
        self,
        agent_id: Any,
        *,
        start_epoch: float,
        end_epoch: float,
        work_context_ids: set[str] | None = None,
        include_rollups: bool = True,
    ) -> dict[str, Any]:
        """Read at most 367 day partitions, using rollups after raw retention."""
        if not start_epoch < end_epoch:
            raise StoreError("invalid skill usage range")
        days = int((end_epoch - start_epoch) // 86400) + 2
        if days > SKILL_USAGE_MAX_QUERY_DAYS + 1:
            raise StoreError("skill usage range exceeds 366 days")
        event_root = self._skill_usage_events_dir(agent_id)
        rollup_root = self._skill_usage_rollups_dir(agent_id)
        events: list[dict[str, Any]] = []
        rollups: list[dict[str, Any]] = []
        current = datetime.fromtimestamp(start_epoch, timezone.utc).date()
        last = datetime.fromtimestamp(end_epoch, timezone.utc).date()
        while current <= last:
            day = current.isoformat()
            directory = event_root / day
            day_events = self._read_event_directory(
                directory,
                start_epoch,
                end_epoch,
                work_context_ids,
            )
            if day_events:
                events.extend(day_events)
            elif include_rollups:
                rollup = self._bounded_rollup(rollup_root / f"{day}.json")
                if rollup is not None:
                    # Rollups contain all contexts. They are usable only when the
                    # selected context is represented by each row's scoped key.
                    filtered = _filter_rollup_contexts(rollup, work_context_ids)
                    if filtered["event_count"]:
                        rollups.append(filtered)
            current += timedelta(days=1)
        if event_root.is_dir() and not any(
            path.is_dir() and not path.is_symlink() for path in event_root.iterdir()
        ):
            legacy = sorted(
                path
                for path in event_root.glob("*.json")
                if path.is_file() and not path.is_symlink()
            )
            if len(legacy) > SKILL_USAGE_MAX_LEGACY_FILES:
                raise StoreError(
                    "legacy skill usage event set exceeds read limit",
                    status=503,
                    code="skill_usage_read_limit",
                )
            events.extend(
                self._read_event_paths(
                    legacy,
                    start_epoch,
                    end_epoch,
                    work_context_ids,
                )
            )
        events.sort(key=lambda item: (item["occurred_at"], item["event_id"]))
        return {
            "events": events,
            "rollups": rollups,
            "state": self.skill_usage_state(agent_id),
        }

    def _read_event_directory(
        self,
        directory: Path,
        start_epoch: float,
        end_epoch: float,
        contexts: set[str] | None,
    ) -> list[dict[str, Any]]:
        if not directory.is_dir() or directory.is_symlink():
            return []
        paths = sorted(path for path in directory.glob("*.json") if path.is_file())
        if len(paths) > SKILL_USAGE_MAX_EVENTS_PER_DAY:
            raise StoreError(
                "daily skill usage event set exceeds read limit",
                status=503,
                code="skill_usage_read_limit",
            )
        return self._read_event_paths(paths, start_epoch, end_epoch, contexts)

    def _read_event_paths(
        self,
        paths: list[Path],
        start_epoch: float,
        end_epoch: float,
        contexts: set[str] | None,
    ) -> list[dict[str, Any]]:
        result = []
        for path in paths:
            try:
                item = self._read_skill_usage_event(path)
                occurred = _timestamp(item["occurred_at"])
            except (OSError, ValueError, StoreError):
                continue
            if not start_epoch < occurred <= end_epoch:
                continue
            if contexts and item["work_context_id"] not in contexts:
                continue
            result.append(item)
        return result

    @staticmethod
    def _bounded_rollup(path: Path) -> dict[str, Any] | None:
        if (
            not path.is_file()
            or path.is_symlink()
            or path.stat().st_size > (SKILL_USAGE_MAX_ROLLUP_BYTES)
        ):
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

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
            item["skill_digest"] = _digest(digest)
        associated = item.get("associated_skills")
        if associated is not None:
            if not isinstance(associated, list) or len(associated) > 64:
                raise StoreError("invalid associated skills")
            normalized = []
            for association in associated:
                if not isinstance(association, Mapping):
                    raise StoreError("invalid associated skills")
                if set(association) - {"skill_id", "skill_digest"}:
                    raise StoreError("invalid associated skills")
                normalized.append(
                    {
                        "skill_id": _safe_label(association.get("skill_id"), "skill id", 256),
                        "skill_digest": (
                            _digest(association["skill_digest"])
                            if association.get("skill_digest") is not None
                            else None
                        ),
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
        if duration is not None and (
            isinstance(duration, bool) or not isinstance(duration, int) or duration < 0
        ):
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


def _event_associations(item: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    associations = item.get("associated_skills")
    if isinstance(associations, list):
        return [entry for entry in associations if isinstance(entry, Mapping)]
    if item.get("skill_id"):
        return [item]
    return []


def _accumulate_rollup_row(row: dict[str, Any], item: Mapping[str, Any]) -> None:
    event_type = item["event_type"]
    fields = {
        "skill.requested": "requested_count",
        "skill.loaded": "loaded_count",
        "skill.reference_read": "reference_reads",
        "skill.tool_invoked": "tool_invocations",
        "skill.tool_completed": "tool_completed",
        "skill.tool_failed": "errors",
    }
    field = fields.get(event_type)
    if field:
        row[field] += 1
    if event_type in {"skill.loaded", "skill.run_associated"}:
        if item["run_id"] not in row["run_ids"]:
            row["run_ids"].append(item["run_id"])
    if item["session_id"] not in row["session_ids"]:
        row["session_ids"].append(item["session_id"])
    duration = item.get("duration_ms")
    if duration is not None:
        row["duration_total_ms"] += int(duration)
        row["duration_count"] += 1
    occurred = item["occurred_at"]
    if not row["last_used_at"] or occurred > row["last_used_at"]:
        row["last_used_at"] = occurred
    row["multiple_attribution"] = bool(
        row["multiple_attribution"] or item.get("attribution") == "multiple"
    )


def _filter_rollup_contexts(rollup: Mapping[str, Any], contexts: set[str] | None) -> dict[str, Any]:
    result = dict(rollup)
    rows = {}
    for key, row in (rollup.get("items") or {}).items():
        try:
            context_id = json.loads(key)[0]
        except (TypeError, ValueError, json.JSONDecodeError, IndexError):
            continue
        if contexts and context_id not in contexts:
            continue
        rows[key] = row
    result["items"] = rows
    selected_contexts = [
        row
        for context_id, row in (rollup.get("contexts") or {}).items()
        if not contexts or context_id in contexts
    ]
    if selected_contexts:
        for field in (
            "event_count",
            "total_tool_invocations",
            "unattributed_tool_invocations",
            "multiple_attributed_tool_invocations",
        ):
            result[field] = sum(int(row.get(field) or 0) for row in selected_contexts)
    else:
        # Rollups written before context counters can safely expose associated
        # skill rows, but unknown-provenance totals remain unknown/zero.
        result["event_count"] = sum(
            sum(
                int(row.get(field) or 0)
                for field in (
                    "requested_count",
                    "loaded_count",
                    "reference_reads",
                    "tool_invocations",
                    "tool_completed",
                    "errors",
                )
            )
            for row in rows.values()
        )
        result["total_tool_invocations"] = sum(
            int(row.get("tool_invocations") or 0) for row in rows.values()
        )
        result["unattributed_tool_invocations"] = 0
        result["multiple_attributed_tool_invocations"] = sum(
            int(row.get("tool_invocations") or 0)
            for row in rows.values()
            if row.get("multiple_attribution")
        )
    return result


def _safe_label(value: Any, field: str, maximum: int) -> str:
    result = str(value or "").strip()
    if not result or len(result) > maximum or any(char in result for char in "\x00\r\n"):
        raise StoreError(f"invalid {field}")
    return result


def _digest(value: Any) -> str:
    result = str(value)
    if not result.startswith("sha256:") or len(result) != 71:
        raise StoreError("invalid skill digest")
    try:
        int(result[7:], 16)
    except ValueError as error:
        raise StoreError("invalid skill digest") from error
    return result


def _timestamp(value: str) -> float:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.timestamp()


def _day(value: str) -> str:
    return datetime.fromtimestamp(_timestamp(value), timezone.utc).date().isoformat()
