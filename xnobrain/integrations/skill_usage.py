"""Privacy-safe skill lifecycle instrumentation at embedded engine seams."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..repositories.skill_usage import (
    SKILL_USAGE_INSTRUMENTATION_VERSION,
    SKILL_USAGE_SCHEMA_VERSION,
)


class SkillUsageInstrumentationMixin:
    """Best-effort lifecycle recording that never persists engine payloads."""

    def _skill_usage_record_requested(
        self,
        prepared: Mapping[str, Any],
        *,
        run_id: str,
    ) -> None:
        profile_dir = Path(prepared["profile_dir"])
        agent_id = str(prepared.get("name") or "").strip()
        if not agent_id:
            return
        session_id = str(prepared.get("conversation_id") or run_id)
        context_id = str(prepared.get("work_context_id") or f"personal:{agent_id}")
        occurred_at = _iso_now()
        requested_skills = list(prepared.get("requested_skills") or [])
        feature_skill = {
            "optimize_skills": "skill-optimizer",
            "agent_maker": "agent-maker",
        }.get(str(prepared.get("feature") or ""))
        if feature_skill:
            requested_skills.append(feature_skill)
        for requested in dict.fromkeys(requested_skills):
            identity = self._skill_usage_identity(profile_dir, requested)
            if identity is None:
                continue
            skill_id, skill_digest = identity
            self._skill_usage_append(
                {
                    "schema_version": SKILL_USAGE_SCHEMA_VERSION,
                    "instrumentation_version": SKILL_USAGE_INSTRUMENTATION_VERSION,
                    "event_id": _event_id("requested", run_id, skill_id, skill_digest),
                    "event_type": "skill.requested",
                    "agent_id": agent_id,
                    "work_context_id": context_id,
                    "run_id": run_id,
                    "session_id": session_id,
                    "skill_id": skill_id,
                    "skill_digest": skill_digest,
                    "attribution": "observed",
                    "occurred_at": occurred_at,
                    "duration_ms": None,
                    "outcome": None,
                }
            )

    def _skill_usage_callbacks(
        self,
        prepared: Mapping[str, Any],
        *,
        run_id: str,
    ) -> tuple[Any, Any]:
        """Return structured start/end callbacks for one embedded run."""
        profile_dir = Path(prepared["profile_dir"])
        agent_id = str(prepared["name"])
        session_id = str(prepared.get("conversation_id") or run_id)
        context_id = str(prepared.get("work_context_id") or f"personal:{agent_id}")
        active_skills: dict[str, str] = {}
        starts: dict[str, tuple[float, str, list[dict[str, str | None]], str]] = {}

        def associations_for(
            tool_name: str,
            args: Any,
        ) -> tuple[list[dict[str, str | None]], str]:
            if tool_name == "skill_view" and isinstance(args, Mapping):
                identity = self._skill_usage_identity(profile_dir, args.get("name"))
                if identity is not None:
                    skill_id, digest = identity
                    return ([{"skill_id": skill_id, "skill_digest": digest}], "observed")
            associations = [
                {"skill_id": skill_id, "skill_digest": digest}
                for skill_id, digest in sorted(active_skills.items())
            ]
            if not associations:
                return [], "unattributed"
            return associations, "observed" if len(associations) == 1 else "multiple"

        def on_start(tool_call_id: Any, function_name: Any, function_args: Any) -> None:
            call_id = str(tool_call_id or "").strip()
            tool_name = str(function_name or "").strip()
            if not call_id or not tool_name:
                return
            associations, attribution = associations_for(tool_name, function_args)
            starts[call_id] = (time.time(), tool_name, associations, attribution)
            self._skill_usage_append(
                self._skill_usage_tool_event(
                    event_id=_event_id("tool_invoked", run_id, call_id),
                    event_type="skill.tool_invoked",
                    agent_id=agent_id,
                    context_id=context_id,
                    run_id=run_id,
                    session_id=session_id,
                    tool_name=tool_name,
                    associations=associations,
                    attribution=attribution,
                    occurred_at=_iso_now(),
                )
            )

        def on_complete(
            tool_call_id: Any,
            function_name: Any,
            function_args: Any,
            function_result: Any,
        ) -> None:
            call_id = str(tool_call_id or "").strip()
            tool_name = str(function_name or "").strip()
            if not call_id or not tool_name:
                return
            started, _, associations, attribution = starts.pop(
                call_id,
                (time.time(), tool_name, *associations_for(tool_name, function_args)),
            )
            failed = _tool_failed(function_result)
            duration_ms = max(0, int((time.time() - started) * 1000))
            if tool_name == "skill_view" and not failed and isinstance(function_args, Mapping):
                identity = self._skill_usage_identity(profile_dir, function_args.get("name"))
                if identity is not None:
                    skill_id, skill_digest = identity
                    active_skills[skill_id] = skill_digest
                    event_type = (
                        "skill.reference_read"
                        if str(function_args.get("file_path") or "").strip()
                        else "skill.loaded"
                    )
                    self._skill_usage_append(
                        {
                            "schema_version": SKILL_USAGE_SCHEMA_VERSION,
                            "instrumentation_version": SKILL_USAGE_INSTRUMENTATION_VERSION,
                            "event_id": _event_id(event_type, run_id, call_id),
                            "event_type": event_type,
                            "agent_id": agent_id,
                            "work_context_id": context_id,
                            "run_id": run_id,
                            "session_id": session_id,
                            "skill_id": skill_id,
                            "skill_digest": skill_digest,
                            "attribution": "observed",
                            "occurred_at": _iso_now(),
                            "duration_ms": duration_ms,
                            "outcome": None,
                        }
                    )
                    associations = [{"skill_id": skill_id, "skill_digest": skill_digest}]
                    attribution = "observed"
            terminal_type = "skill.tool_failed" if failed else "skill.tool_completed"
            event = self._skill_usage_tool_event(
                event_id=_event_id(terminal_type, run_id, call_id),
                event_type=terminal_type,
                agent_id=agent_id,
                context_id=context_id,
                run_id=run_id,
                session_id=session_id,
                tool_name=tool_name,
                associations=associations,
                attribution=attribution,
                occurred_at=_iso_now(),
            )
            event["duration_ms"] = duration_ms
            event["outcome"] = "failed" if failed else "completed"
            self._skill_usage_append(event)

        return on_start, on_complete

    @staticmethod
    def _skill_usage_tool_event(
        *,
        event_id: str,
        event_type: str,
        agent_id: str,
        context_id: str,
        run_id: str,
        session_id: str,
        tool_name: str,
        associations: list[dict[str, str | None]],
        attribution: str,
        occurred_at: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": SKILL_USAGE_SCHEMA_VERSION,
            "instrumentation_version": SKILL_USAGE_INSTRUMENTATION_VERSION,
            "event_id": event_id,
            "event_type": event_type,
            "agent_id": agent_id,
            "work_context_id": context_id,
            "run_id": run_id,
            "session_id": session_id,
            "skill_id": associations[0]["skill_id"] if len(associations) == 1 else None,
            "skill_digest": (associations[0]["skill_digest"] if len(associations) == 1 else None),
            "associated_skills": associations,
            "attribution": attribution,
            "occurred_at": occurred_at,
            "duration_ms": None,
            "outcome": None,
            "tool_name": tool_name,
        }

    def _skill_usage_identity(
        self,
        profile_dir: Path,
        raw_skill_id: Any,
    ) -> tuple[str, str] | None:
        skill_id = str(raw_skill_id or "").strip()
        if not skill_id or len(skill_id) > 256:
            return None
        try:
            skill_dir = self._find_agent_skill(profile_dir, skill_id)
        except Exception:
            return None
        if skill_dir is None:
            return None
        skill_file = skill_dir / "SKILL.md"
        try:
            profile_skills = (profile_dir / "skills").resolve()
            resolved_file = skill_file.resolve(strict=True)
            resolved_file.relative_to(profile_skills)
            content = resolved_file.read_bytes()
            frontmatter = self._read_skill_frontmatter(resolved_file)
        except (OSError, ValueError):
            return None
        canonical_id = str(frontmatter.get("name") or skill_dir.name).strip()
        if not canonical_id or len(canonical_id) > 256:
            return None
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        return canonical_id, digest

    def _skill_usage_append(self, event: Mapping[str, Any]) -> None:
        repository = getattr(self, "skill_usage_repository", None)
        if repository is None:
            return
        try:
            repository.append_skill_usage_event(event)
        except Exception:
            # Usage metadata is best effort and must never interrupt a chat.
            return


def _event_id(*parts: Any) -> str:
    canonical = json.dumps(parts, ensure_ascii=True, separators=(",", ":"))
    return "sue_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _tool_failed(result: Any) -> bool:
    try:
        decoded = json.loads(result) if isinstance(result, str) else result
    except (TypeError, ValueError, json.JSONDecodeError):
        decoded = None
    if isinstance(decoded, Mapping):
        if decoded.get("success") is False or decoded.get("error"):
            return True
        status = str(decoded.get("status") or "").lower()
        return status in {"error", "failed", "blocked", "cancelled", "rejected"}
    text = str(result or "").lstrip().lower()
    return text.startswith(("error", "tool error", "failed"))
