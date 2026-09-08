"""Cron automation API orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml

from ..defaults import (
    BIG_BROTHER_AGENT_ID,
    BIG_BROTHER_APPROVAL_DEFAULT_MARKER,
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
from ..integrations import AgentAPIError, ConfigAPIError, LLMRouterAPIError
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


class AutomationServiceMixin:
    def list_crons(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        return self.cron.list_jobs(agent_id)

    def get_job_detail(self, cron_id: str, agent_id: str | None = None) -> dict[str, Any]:
        return self.cron.get_job_detail(cron_id, agent_id)

    def preview_cron_schedule(self, body: Mapping[str, Any]) -> dict[str, Any]:
        from ..integrations.schedule_preview import preview_calendar_runs
        from ..models.automation import CronSchedulePreview

        request = CronSchedulePreview.model_validate(dict(body))
        cutoff = request.after or datetime.now(timezone.utc)
        return {
            "occurrences": preview_calendar_runs(
                request.schedule, request.timezone, cutoff, count=request.count
            ),
            "dst_policy": "skip_gap_earlier_fold",
            "executor_parity_verified": False,
        }

    def create_cron(self, body: Mapping[str, Any]) -> dict[str, Any]:
        return self.cron.create_job(body)

    def set_cron_enabled(
        self,
        cron_id: str,
        enabled: bool,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        return self.cron.set_enabled(cron_id, enabled, agent_id)

    def delete_cron(self, cron_id: str, agent_id: str | None = None) -> dict[str, Any]:
        return self.cron.delete_job(cron_id, agent_id)

    def run_cron(self, cron_id: str, agent_id: str | None = None) -> dict[str, Any]:
        return self.cron.request_run(cron_id, agent_id)

    @staticmethod
    def _schedule_job(task: Mapping[str, Any]) -> dict[str, Any]:
        schedule = dict(task.get("schedule") or {})
        interval = schedule.get("interval_minutes")
        return {
            "id": str(task["id"]),
            "agent_id": task.get("assignee"),
            "name": str(task.get("title") or "Scheduled task"),
            "prompt": str(task.get("description") or ""),
            "schedule": (
                f"@every {interval}m"
                if schedule.get("recurrence") == "interval"
                else schedule.get("next_run_at")
            ),
            "timezone": str(schedule.get("timezone") or "Etc/UTC"),
            "enabled": bool(schedule.get("enabled")),
            "next_run_at": schedule.get("next_run_at"),
            "last_run_at": schedule.get("last_run_at"),
            "mode": "local",
            "kanban_board": "default",
            "kanban_task_id": str(task["id"]),
        }

    @staticmethod
    def _schedule_seconds(schedule: str) -> int:
        match = EVERY_SCHEDULE.fullmatch(schedule.strip())
        if not match:
            raise ServiceError("schedule must use @every <number>s|m|h|d")
        value = int(match.group(1))
        return value * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2)]

    def list_cron_blueprints(self) -> dict[str, Any]:
        return self.cron.list_blueprints()

    def instantiate_cron_blueprint(self, body: Mapping[str, Any]) -> dict[str, Any]:
        return self.cron.instantiate_blueprint(body)

    def list_cron_delivery_targets(self, agent_id: str | None = None) -> dict[str, Any]:
        return self.cron.list_delivery_target_options(agent_id)

    def list_cron_job_targets(self, cron_id: str, agent_id: str | None = None) -> dict[str, Any]:
        return self.cron.list_job_targets(cron_id, agent_id)

    def add_cron_job_target(
        self,
        cron_id: str,
        body: Mapping[str, Any],
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        return self.cron.add_delivery_target(cron_id, body, agent_id)

    def remove_cron_job_target(
        self,
        cron_id: str,
        target_id: str,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        return self.cron.remove_delivery_target(cron_id, target_id, agent_id)

    def list_cron_runs(
        self,
        cron_id: str,
        limit: int = 20,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        return self.cron.list_job_runs(cron_id, limit, agent_id)
