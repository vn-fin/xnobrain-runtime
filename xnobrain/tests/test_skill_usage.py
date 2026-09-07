"""Deterministic tests for durable skill lifecycle intelligence."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from xnobrain.integrations.skill_usage import SkillUsageInstrumentationMixin
from xnobrain.models.analytics import SkillUsageResponse
from xnobrain.repositories import FileRepository, StoreError
from xnobrain.repositories.skill_usage import (
    SKILL_USAGE_INSTRUMENTATION_VERSION,
    SKILL_USAGE_SCHEMA_VERSION,
)
from xnobrain.services.analytics import (
    _aggregate_skill_events,
    _decode_skill_cursor,
    _encode_skill_cursor,
    _skill_item_key,
)


class InstrumentedEngine(SkillUsageInstrumentationMixin):
    def __init__(self, repository: FileRepository):
        self.skill_usage_repository = repository

    @staticmethod
    def _find_agent_skill(profile_dir: Path, skill_id: str) -> Path | None:
        candidate = profile_dir / "skills" / skill_id
        return candidate if (candidate / "SKILL.md").is_file() else None

    @staticmethod
    def _read_skill_frontmatter(path: Path) -> dict:
        return {"name": path.parent.name}


class SkillUsageRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.profiles = root / "profiles"
        self.profile = self.profiles / "analyst"
        self.profile.mkdir(parents=True)
        self.repository = FileRepository(root / "data", self.profiles)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def event(event_id: str = "sue_" + "a" * 64, **changes) -> dict:
        item = {
            "schema_version": SKILL_USAGE_SCHEMA_VERSION,
            "instrumentation_version": SKILL_USAGE_INSTRUMENTATION_VERSION,
            "event_id": event_id,
            "event_type": "skill.loaded",
            "agent_id": "analyst",
            "work_context_id": "personal:analyst",
            "run_id": "run_one",
            "session_id": "session_one",
            "skill_id": "report-writer",
            "skill_digest": "sha256:" + "1" * 64,
            "attribution": "observed",
            "occurred_at": "2026-09-07T12:00:00Z",
            "duration_ms": 12,
            "outcome": None,
        }
        item.update(changes)
        return item

    def test_persistence_restart_and_duplicate_idempotency(self) -> None:
        event = self.event()
        self.repository.append_skill_usage_event(event)
        self.repository.append_skill_usage_event(event)

        restarted = FileRepository(Path(self.temporary.name) / "data", self.profiles)
        rows = restarted.list_skill_usage_events(
            "analyst",
            start_epoch=datetime(2026, 9, 7, tzinfo=timezone.utc).timestamp(),
            end_epoch=datetime(2026, 9, 8, tzinfo=timezone.utc).timestamp(),
            work_context_id="personal:analyst",
        )
        self.assertEqual(rows, [event])
        event_files = list((self.profile / "skill-usage/v1/events").glob("*.json"))
        self.assertEqual(len(event_files), 1)

        with self.assertRaises(StoreError) as conflict:
            restarted.append_skill_usage_event({**event, "event_type": "skill.requested"})
        self.assertEqual(conflict.exception.code, "skill_usage_event_conflict")

    def test_agent_profile_isolation(self) -> None:
        self.repository.append_skill_usage_event(self.event())
        self.assertEqual(
            self.repository.list_skill_usage_events(
                "other-agent",
                start_epoch=0,
                end_epoch=4_000_000_000,
            ),
            [],
        )
        with self.assertRaises(StoreError):
            self.repository.list_skill_usage_events("../analyst", start_epoch=0, end_epoch=1)

    def test_date_and_context_filters(self) -> None:
        self.repository.append_skill_usage_event(self.event())
        self.repository.append_skill_usage_event(
            self.event(
                "sue_" + "b" * 64,
                work_context_id="org:example",
                occurred_at="2026-09-08T12:00:00Z",
            )
        )
        rows = self.repository.list_skill_usage_events(
            "analyst",
            start_epoch=datetime(2026, 9, 7, 11, tzinfo=timezone.utc).timestamp(),
            end_epoch=datetime(2026, 9, 7, 13, tzinfo=timezone.utc).timestamp(),
            work_context_id="personal:analyst",
        )
        self.assertEqual([item["event_id"] for item in rows], ["sue_" + "a" * 64])

    def test_sensitive_fields_are_rejected_and_never_persisted(self) -> None:
        for field in ("prompt", "args", "result", "content", "path", "authorization"):
            with self.subTest(field=field), self.assertRaises(StoreError):
                self.repository.append_skill_usage_event(
                    self.event(
                        "sue_" + hashlib.sha256(field.encode()).hexdigest(), **{field: "secret"}
                    )
                )
        self.assertFalse((self.profile / "skill-usage/v1/events").exists())

    def test_typed_response_accepts_instrumented_and_legacy_shapes(self) -> None:
        instrumented = _aggregate_skill_events(
            [self.event()],
            start_epoch=0,
            end_epoch=2_000_000_000,
            context_id="personal",
        )
        model = SkillUsageResponse.model_validate(
            {"agent_id": "analyst", "next_cursor": None, "limit": 100, **instrumented}
        )
        self.assertEqual(model.items[0].skill_id, "report-writer")
        legacy = {
            "agent_id": "analyst",
            "work_context_id": "personal",
            "items": [
                {
                    "skill_id": "report-writer",
                    "loaded_count": 2,
                    "distinct_runs": 1,
                    "last_used_at": 123.0,
                    "tool_invocations": None,
                    "errors": None,
                    "attribution": "observed",
                }
            ],
            "coverage": {
                "source": "hermes_tool_calls",
                "attribution": "observed_load_only",
                "from": 0,
                "to": 1000,
                "instrumented": True,
                "message": "Measured skill loads",
            },
            "next_cursor": None,
            "limit": 100,
        }
        self.assertEqual(SkillUsageResponse.model_validate(legacy).items[0].loaded_count, 2)

    def test_cursor_round_trip_and_aggregate_filters_are_deterministic(self) -> None:
        key = ("report-writer", "sha256:" + "1" * 64)
        self.assertEqual(_decode_skill_cursor(_encode_skill_cursor(key)), key)
        with self.assertRaises(ValueError):
            _decode_skill_cursor("not-json")

        events = [
            self.event(event_type="skill.requested"),
            self.event("sue_" + "b" * 64, event_type="skill.loaded"),
            self.event("sue_" + "c" * 64, event_type="skill.reference_read"),
            self.event(
                "sue_" + "d" * 64,
                event_type="skill.tool_invoked",
                skill_id=None,
                skill_digest=None,
                associated_skills=[],
                attribution="unattributed",
                tool_name="terminal",
            ),
        ]
        result = _aggregate_skill_events(
            events,
            start_epoch=0,
            end_epoch=2_000_000_000,
            context_id="personal:analyst",
        )
        item = result["items"][0]
        self.assertEqual(item["requested_count"], 1)
        self.assertEqual(item["loaded_count"], 1)
        self.assertEqual(item["reference_reads"], 1)
        self.assertEqual(item["distinct_runs"], 1)
        self.assertEqual(result["coverage"]["unattributed_tool_invocations"], 1)
        self.assertEqual(_skill_item_key(item), key)


class SkillUsageInstrumentationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        profiles = root / "profiles"
        self.profile = profiles / "analyst"
        skill = self.profile / "skills" / "report-writer"
        skill.mkdir(parents=True)
        self.skill_content = b"---\nname: report-writer\n---\n# Report\n"
        (skill / "SKILL.md").write_bytes(self.skill_content)
        self.repository = FileRepository(root / "data", profiles)
        self.engine = InstrumentedEngine(self.repository)
        self.prepared = {
            "name": "analyst",
            "profile_dir": self.profile,
            "conversation_id": "session_one",
            "work_context_id": "personal:analyst",
            "requested_skills": ["report-writer"],
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_requested_loaded_reference_tool_and_terminal_events_are_safe(self) -> None:
        self.engine._skill_usage_record_requested(self.prepared, run_id="run_one")
        start, complete = self.engine._skill_usage_callbacks(self.prepared, run_id="run_one")
        start("call_load", "skill_view", {"name": "report-writer", "prompt": "secret"})
        complete(
            "call_load",
            "skill_view",
            {"name": "report-writer"},
            json.dumps({"success": True, "content": "private skill text"}),
        )
        start(
            "call_ref",
            "skill_view",
            {"name": "report-writer", "file_path": "references/private.md"},
        )
        complete(
            "call_ref",
            "skill_view",
            {"name": "report-writer", "file_path": "references/private.md"},
            json.dumps({"success": True, "content": "private reference"}),
        )
        start("call_tool", "terminal", {"command": "cat ~/.env"})
        complete("call_tool", "terminal", {"command": "cat ~/.env"}, "API_KEY=secret")
        start("call_fail", "read_file", {"path": "/private"})
        complete("call_fail", "read_file", {"path": "/private"}, '{"error":"secret"}')

        events = self.repository.list_skill_usage_events(
            "analyst",
            start_epoch=0,
            end_epoch=4_000_000_000,
            work_context_id="personal:analyst",
        )
        types = [event["event_type"] for event in events]
        self.assertIn("skill.requested", types)
        self.assertIn("skill.loaded", types)
        self.assertIn("skill.reference_read", types)
        self.assertIn("skill.tool_invoked", types)
        self.assertIn("skill.tool_completed", types)
        self.assertIn("skill.tool_failed", types)
        persisted = json.dumps(events)
        for sensitive in ("prompt", "command", "API_KEY", "private reference", "/private"):
            self.assertNotIn(sensitive, persisted)
        digest = "sha256:" + hashlib.sha256(self.skill_content).hexdigest()
        self.assertTrue(any(event.get("skill_digest") == digest for event in events))
        self.assertTrue(all(event["run_id"] == "run_one" for event in events))
