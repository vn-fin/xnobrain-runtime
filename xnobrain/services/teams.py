"""Team definition and synchronous run orchestration."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import time
from typing import Any, Mapping
import uuid

import yaml

from ..defaults import (
    BIG_BROTHER_APPROVAL_DEFAULT_MARKER,
    BIG_BROTHER_AGENT_ID,
    BIG_BROTHER_DESCRIPTION,
    BIG_BROTHER_DISPLAY_NAME,
    BIG_BROTHER_MODEL_DEFAULT_MARKER,
    BIG_BROTHER_NATIVE_TOOLSETS,
    BIG_BROTHER_SKILL_CATEGORY,
    BIG_BROTHER_SKILL_ID,
    CUSTOM_SKILL_CATEGORY,
    DEFAULT_PROFILE_MODEL,
    LEGACY_BIG_BROTHER_TOOLSET,
)
from ..integrations import AgentAPIError, ConfigAPIError, NineRouterAPIError
from ..repositories import StoreError
from .base import ServiceError, iso, utc_now
from .constants import (
    API_KEY_PROVIDERS,
    DEFAULT_TEAM_COORDINATOR_PROMPT,
    DEFAULT_TEAM_SYNTHESIS_PROMPT,
    EVERY_SCHEDULE,
    FREE_MODEL_PROVIDERS,
    NO_AUTH_PROVIDERS,
    OPENAI_COMPATIBLE_PROVIDER_DEFINITIONS,
    PROVIDER_DEFINITIONS,
    SAFE_TOOLSETS,
    SUPPORTED_PROVIDERS,
)
from .cron import CronServiceError
from .helpers import cached_method
from .workspace_preview import WorkspacePreview, WorkspacePreviewError
from .workspace_upload import WorkspaceUploadError

class TeamsServiceMixin:
    @staticmethod
    def _team_with_description(team: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(team)
        if not str(result.get("description") or "").strip():
            count = len(result.get("members") or []) + 1
            result["description"] = (
                f"A coordinated team of {count} agents for multi-stage work."
            )
        return result

    def list_teams(self) -> list[dict[str, Any]]:
        return [
            self._team_with_description(team)
            for team in self.repository.list_teams()
        ]

    def get_team(self, team_id: str) -> dict[str, Any]:
        return self._team_with_description(self.repository.get_team(team_id))

    def create_team(self, body: Mapping[str, Any]) -> dict[str, Any]:
        return self._put_team(uuid.uuid4().hex, body, created_at=iso())

    def update_team(self, team_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        current = self.repository.get_team(team_id)
        payload = dict(body)
        if "description" not in payload:
            payload["description"] = current.get("description")
        return self._put_team(team_id, payload, created_at=current["created_at"])

    def delete_team(self, team_id: str) -> dict[str, Any]:
        if not self.repository.delete_team(team_id):
            raise ServiceError("team not found", status=404, code="not_found")
        self.repository.delete_team_runs(team_id)
        return {"deleted": True}

    def _build_team_workflow(self, team: Mapping[str, Any], body: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Build and validate the executable workflow for a team run.

        Shared by the legacy sync ``run_team`` and the async ``TeamRunService`` so
        both paths schedule one identical DAG. Raises the same synchronous 400s as
        before on invalid workflows.
        """
        task = str(body.get("task") or "").strip()
        raw_workflow = body.get("workflow") or team.get("workflow") or []
        if not task and not raw_workflow:
            raise ServiceError("task or workflow is required")
        members = [item for item in team["members"] if item.get("enabled", True)]
        configured = {
            str(item["agent_id"]): {
                "agent_id": str(item["agent_id"]),
                "role": str(item["role"]),
                "allowed_tools": [tool for tool in item.get("allowed_tools", []) if tool in SAFE_TOOLSETS],
            }
            for item in members
        }
        configured.setdefault(str(team["orchestrator_id"]), {
            "agent_id": str(team["orchestrator_id"]),
            "role": "coordinator",
            "allowed_tools": ["todo"],
        })
        if raw_workflow:
            workflow = self._team_workflow(raw_workflow, configured, members, task)
        else:
            if not members:
                raise ServiceError("team has no enabled workers", status=409, code="team_has_no_workers")
            workflow = [
                {
                    "id": f"worker-{index + 1}",
                    "task": task,
                    "needs": [],
                    "skills": self._enabled_team_skills(str(member["agent_id"])),
                    **configured[str(member["agent_id"])],
                }
                for index, member in enumerate(members)
            ]
        self._validate_team_workflow(workflow)
        return workflow

    async def run_team(self, team_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """Legacy synchronous run: execute the DAG inline via the shared engine and
        return the historical response dict. Sync runs also persist a run record."""
        record = await self.team_runs.run_sync(team_id, body)
        results = [self._legacy_step_result(step) for step in record["steps"]]
        return {
            "team_id": team_id,
            "member_results": results,
            "workflow_results": results,
            "orchestrator_summary": record["orchestrator_summary"],
            "started_at": record["started_at"],
            "completed_at": record["ended_at"],
        }

    @staticmethod
    def _legacy_step_result(step: Mapping[str, Any]) -> dict[str, Any]:
        base = {
            "id": step["id"], "agent_id": step["agent_id"], "role": step["role"],
            "needs": list(step["needs"]), "status": step["status"],
        }
        if step["status"] == "failed":
            base["error"] = step["error"]
        else:
            base["summary"] = step["summary"]
        return base

    def _team_workflow(
        self,
        raw_workflow: Any,
        configured: Mapping[str, Mapping[str, Any]],
        members: list[Mapping[str, Any]],
        parent_task: str,
    ) -> list[dict[str, Any]]:
        if not isinstance(raw_workflow, list) or not raw_workflow or len(raw_workflow) > 64:
            raise ServiceError("workflow must contain between 1 and 64 steps", code="invalid_workflow")
        workers = [str(item["agent_id"]) for item in members]
        workflow: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_workflow):
            if not isinstance(raw, Mapping):
                raise ServiceError("workflow steps must be objects", code="invalid_workflow")
            step_id = str(raw.get("id") or "").strip()
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", step_id):
                raise ServiceError("workflow step id is invalid", code="invalid_workflow")
            step_task = str(raw.get("task") or raw.get("goal") or parent_task).strip()
            if not step_task:
                raise ServiceError("workflow step task is required", code="invalid_workflow")
            requested_agent = str(raw.get("agent_id") or "").strip()
            requested_role = str(raw.get("role") or "").strip()
            if not requested_agent and requested_role:
                requested_agent = next(
                    (agent_id for agent_id, item in configured.items() if item["role"] == requested_role),
                    "",
                )
            if not requested_agent:
                if not workers:
                    raise ServiceError("workflow has no available worker", status=409, code="team_has_no_workers")
                requested_agent = workers[index % len(workers)]
            assignment = configured.get(requested_agent)
            if assignment is None:
                raise ServiceError("workflow references an agent outside the team", code="invalid_workflow_agent")
            allowed = list(assignment["allowed_tools"])
            requested_tools = raw.get("allowed_tools")
            if requested_tools is not None:
                if not isinstance(requested_tools, list):
                    raise ServiceError("allowed_tools must be a list", code="invalid_workflow")
                if any(str(tool) not in SAFE_TOOLSETS for tool in requested_tools):
                    raise ServiceError("workflow step contains a privileged toolset", code="invalid_workflow_tools")
                if allowed and any(str(tool) not in allowed for tool in requested_tools):
                    raise ServiceError("workflow step exceeds its assigned tool policy", code="invalid_workflow_tools")
                allowed = sorted({str(tool) for tool in requested_tools})
            needs = raw.get("needs") or []
            if not isinstance(needs, list):
                raise ServiceError("workflow needs must be a list", code="invalid_workflow")
            workflow.append({
                "id": step_id,
                "task": step_task,
                "agent_id": requested_agent,
                "role": requested_role or str(assignment["role"]),
                "allowed_tools": allowed,
                "skills": (
                    self._enabled_team_skills(requested_agent)
                    if raw.get("skills") is None
                    else self._team_step_skills(raw.get("skills"))
                ),
                "needs": [str(item).strip() for item in needs],
            })
        return workflow

    @staticmethod
    def _validate_team_workflow(workflow: list[Mapping[str, Any]]) -> None:
        ids = [str(step["id"]) for step in workflow]
        if len(set(ids)) != len(ids):
            raise ServiceError("workflow step ids must be unique", code="invalid_workflow")
        known = set(ids)
        remaining = set(ids)
        completed: set[str] = set()
        by_id = {str(step["id"]): step for step in workflow}
        for step in workflow:
            needs = list(step.get("needs") or [])
            if len(set(needs)) != len(needs) or str(step["id"]) in needs:
                raise ServiceError("workflow dependencies are invalid", code="invalid_workflow")
            if any(need not in known for need in needs):
                raise ServiceError("workflow dependency does not exist", code="invalid_workflow_dependency")
        while remaining:
            ready = {step_id for step_id in remaining if all(need in completed for need in by_id[step_id].get("needs", []))}
            if not ready:
                raise ServiceError("workflow contains a dependency cycle", code="workflow_cycle")
            completed.update(ready)
            remaining.difference_update(ready)

    def _put_team(self, team_id: str, body: Mapping[str, Any], *, created_at: str) -> dict[str, Any]:
        name = str(body.get("name") or "").strip()
        orchestrator = str(body.get("orchestrator_id") or "").strip()
        if not name or not orchestrator:
            raise ServiceError("name and orchestrator_id are required")
        self.agents.describe_agent(orchestrator, include_memory=False)
        synthesis_agent = str(body.get("synthesis_agent_id") or orchestrator).strip()
        self.agents.describe_agent(synthesis_agent, include_memory=False)
        seen: set[str] = set()
        members = []
        for raw in body.get("members") or []:
            agent_id = str(raw.get("agent_id") or "").strip()
            role = str(raw.get("role") or "").strip()
            if not agent_id or not role or agent_id in seen:
                raise ServiceError("team members require unique agent_id and role")
            self.agents.describe_agent(agent_id, include_memory=False)
            seen.add(agent_id)
            raw_tools = raw.get("allowed_tools")
            tools = sorted(set(raw_tools if raw_tools is not None else []))
            if any(tool not in SAFE_TOOLSETS for tool in tools):
                raise ServiceError("team member contains a privileged toolset")
            members.append({"agent_id": agent_id, "role": role, "allowed_tools": tools, "enabled": bool(raw.get("enabled", True))})
        configured = {
            str(item["agent_id"]): {
                "agent_id": str(item["agent_id"]),
                "role": str(item["role"]),
                "allowed_tools": list(item["allowed_tools"]),
            }
            for item in members
        }
        configured.setdefault(orchestrator, {
            "agent_id": orchestrator,
            "role": "coordinator",
            "allowed_tools": ["todo"],
        })
        workflow = []
        if body.get("workflow"):
            workflow = self._team_workflow(body["workflow"], configured, members, "")
            self._validate_team_workflow(workflow)
        team = {
            "id": team_id,
            "user_id": "local",
            "name": name,
            "description": (
                str(body.get("description") or "").strip()
                or f"A coordinated team of {len(members) + 1} agents for multi-stage work."
            ),
            "orchestrator_id": orchestrator,
            "coordinator_prompt": (
                str(body.get("coordinator_prompt") or "").strip()
                or DEFAULT_TEAM_COORDINATOR_PROMPT
            ),
            "coordinator_allowed_tools": self._team_toolsets(body.get("coordinator_allowed_tools")),
            "coordinator_skills": (
                self._enabled_team_skills(orchestrator)
                if body.get("coordinator_skills") is None
                else self._team_step_skills(body.get("coordinator_skills"))
            ),
            "synthesis_agent_id": synthesis_agent,
            "synthesis_allowed_tools": self._team_toolsets(body.get("synthesis_allowed_tools")),
            "synthesis_skills": (
                self._enabled_team_skills(synthesis_agent)
                if body.get("synthesis_skills") is None
                else self._team_step_skills(body.get("synthesis_skills"))
            ),
            "members": members,
            "workflow": workflow,
            "shared_workspace": bool(body.get("shared_workspace", False)),
            "communication_level": max(0, min(3, int(body.get("communication_level", 1)))),
            "synthesis_instruction": (
                str(body.get("synthesis_instruction") or "").strip()
                or DEFAULT_TEAM_SYNTHESIS_PROMPT
            ),
            "max_parallel": max(1, int(body.get("max_parallel") or 1)),
            "max_depth": max(1, int(body.get("max_depth") or 1)),
            "enabled": bool(body.get("enabled", True)),
            "created_at": created_at,
            "updated_at": iso(),
        }
        return self.repository.put_team(team)

    @staticmethod
    def _team_step_skills(value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ServiceError("skills must be a list", code="invalid_workflow")
        skills = sorted({str(item).strip() for item in value if str(item).strip()})
        if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", skill) for skill in skills):
            raise ServiceError("workflow skill id is invalid", code="invalid_workflow")
        return skills

    def _enabled_team_skills(self, agent_id: str) -> list[str]:
        try:
            skills = self.agents.list_skills(agent_id).get("skills", [])
        except (AgentAPIError, StoreError):
            return []
        return sorted({
            str(item.get("skill_id") or "").strip()
            for item in skills
            if item.get("enabled", True) and str(item.get("skill_id") or "").strip()
        })

    @staticmethod
    def _team_toolsets(value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ServiceError("allowed tools must be a list", code="invalid_workflow")
        tools = sorted({str(item).strip() for item in value if str(item).strip()})
        if any(tool not in SAFE_TOOLSETS for tool in tools):
            raise ServiceError("team role contains a privileged toolset", code="invalid_workflow_tools")
        return tools

