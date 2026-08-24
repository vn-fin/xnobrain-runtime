"""Conversation and streaming run service behavior."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import hashlib
import json
import logging
import os
from pathlib import Path
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

class ConversationsServiceMixin:
    def list_conversations(self, agent_id: str, *, page: int = 1, limit: int = 50) -> dict[str, Any]:
        bounded_page = max(1, int(page))
        bounded_limit = max(1, min(int(limit), 1000))
        payload = self.agents.list_conversations(agent_id, {
            "page": bounded_page,
            "limit": bounded_limit,
        })
        conversations = payload["conversations"]
        pagination = dict(payload.get("pagination") or {})
        return {
            "conversations": [self._conversation_dto(agent_id, item) for item in conversations],
            "pagination": {
                "page": int(pagination.get("page") or bounded_page),
                "limit": int(pagination.get("limit") or bounded_limit),
                "has_more": bool(pagination.get("has_more", False)),
            },
        }

    def create_conversation(self, agent_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        payload = self.agents.create_conversation(agent_id, {"title": body.get("title") or "New Session"})
        return self._conversation_dto(agent_id, payload["conversation"])

    def get_conversation(self, agent_id: str, conversation_id: str) -> dict[str, Any]:
        payload = self.agents.get_conversation(agent_id, conversation_id)
        result = self._conversation_dto(agent_id, payload["conversation"])
        result["messages"] = payload["messages"]
        return result

    async def conversation_usage(
        self, agent_id: str, conversation_id: str,
    ) -> dict[str, Any]:
        payload = self.agents.get_conversation(agent_id, conversation_id)
        session = dict(payload["conversation"])
        messages = list(payload["messages"])

        def integer(field: str) -> int:
            try:
                return max(0, int(session.get(field) or 0))
            except (TypeError, ValueError):
                return 0

        def number(field: str) -> float:
            try:
                return max(0.0, float(session.get(field) or 0))
            except (TypeError, ValueError):
                return 0.0

        input_tokens = integer("input_tokens")
        output_tokens = integer("output_tokens")
        cache_read_tokens = integer("cache_read_tokens")
        cache_write_tokens = integer("cache_write_tokens")
        reasoning_tokens = integer("reasoning_tokens")
        total_tokens = input_tokens + output_tokens + cache_read_tokens + cache_write_tokens
        if total_tokens == 0:
            total_tokens = sum(
                max(0, int(message.get("token_count") or 0))
                for message in messages
                if str(message.get("token_count") or "").lstrip("-").isdigit()
            )

        started_at = number("started_at")
        ended_at = number("ended_at")
        execution_seconds = max(0.0, ended_at - started_at) if started_at and ended_at else 0.0
        tool_steps = sum(1 for message in messages if message.get("role") == "tool")
        actual_cost = session.get("actual_cost_usd")
        cost = number("actual_cost_usd") if actual_cost is not None else number("estimated_cost_usd")
        cost_source = str(session.get("cost_source") or "")
        cost_status = str(session.get("cost_status") or "")
        if actual_cost is None and cost <= 0:
            cost = await self.analytics.conversation_estimated_cost(
                agent_id, conversation_id,
            )
            if cost > 0:
                cost_source = "profile_ledger"
                cost_status = "estimated"
        model_config = session.get("model_config")
        context = (
            model_config.get("xnobrain_context")
            if isinstance(model_config, Mapping)
            else {}
        )
        context = context if isinstance(context, Mapping) else {}
        try:
            context_used = max(0, int(context.get("used") or 0))
        except (TypeError, ValueError):
            context_used = 0
        try:
            context_limit = max(0, int(context.get("limit") or 0))
        except (TypeError, ValueError):
            context_limit = 0
        try:
            context_threshold = max(0, int(context.get("threshold") or 0))
        except (TypeError, ValueError):
            context_threshold = 0
        context_payload: dict[str, Any] = {"used": context_used}
        if context_limit:
            context_payload.update({
                "limit": context_limit,
                "percent": round(min(100.0, context_used / context_limit * 100), 2),
            })
        if context_threshold:
            context_payload.update({
                "threshold": context_threshold,
                "pressure_percent": round(min(100.0, context_used / context_threshold * 100), 2),
                "auto_compaction": bool(context.get("auto_compaction")),
            })

        return {
            "conversation_id": conversation_id,
            "api_calls": integer("api_call_count"),
            "duration": f"{execution_seconds:.2f}s",
            "execution_seconds": round(execution_seconds, 3),
            "messages": len(messages),
            "steps": tool_steps,
            "tool_calls": tool_steps,
            "model": str(session.get("model") or ""),
            "source": str(session.get("source") or ""),
            "tokens": {
                "cache_read": cache_read_tokens,
                "cache_write": cache_write_tokens,
                "input": input_tokens,
                "output": output_tokens,
                "reasoning": reasoning_tokens,
                "total": total_tokens,
            },
            "cost": {
                "source": cost_source,
                "status": cost_status,
                "total_usd": cost,
            },
            "context": context_payload,
            "weekly_budget": await self.analytics.get_budget(agent_id),
        }

    def rename_conversation(self, agent_id: str, conversation_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        payload = self.agents.update_conversation(agent_id, conversation_id, {"title": body.get("title")})
        return self._conversation_dto(agent_id, payload["conversation"])

    async def compact_conversation(
        self,
        agent_id: str,
        conversation_id: str,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        return await self.agents.compact_conversation(
            agent_id,
            conversation_id,
            focus=str(body.get("focus") or "").strip(),
        )

    def delete_conversation(self, agent_id: str, conversation_id: str) -> dict[str, Any]:
        if self.conversation_runs.active_run(agent_id, conversation_id) is not None:
            raise ServiceError(
                "cancel the active response before deleting this conversation",
                status=409,
                code="conversation_running",
            )
        result = self.agents.delete_conversation(agent_id, conversation_id)
        self.repository.delete_conversation_runs(agent_id, conversation_id)
        return result

    async def stream_conversation(self, agent_id: str, conversation_id: str, body: Mapping[str, Any]):
        async for event in self.conversation_runs.legacy_stream(agent_id, conversation_id, body):
            yield event

    async def start_conversation_run(
        self,
        agent_id: str,
        conversation_id: str,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        return await self.conversation_runs.start_run(agent_id, conversation_id, body)

    def get_conversation_goal(self, agent_id: str, conversation_id: str) -> dict[str, Any]:
        return self.agents.get_conversation_goal(agent_id, conversation_id)

    async def create_conversation_goal(
        self, agent_id: str, conversation_id: str, body: Mapping[str, Any],
    ) -> dict[str, Any]:
        if self.conversation_runs.active_run(agent_id, conversation_id) is not None:
            raise ServiceError("pause the current response before starting a goal", status=409, code="conversation_running")
        result = self.agents.set_conversation_goal(agent_id, conversation_id, body)
        try:
            run = await self.conversation_runs.start_run(agent_id, conversation_id, {
                "input": str(body.get("objective") or ""),
                "run_mode": "background",
            })
        except Exception:
            self.agents.clear_conversation_goal(agent_id, conversation_id)
            raise
        return {**result, "run": run}

    def update_conversation_goal(
        self, agent_id: str, conversation_id: str, body: Mapping[str, Any],
    ) -> dict[str, Any]:
        if self.conversation_runs.active_run(agent_id, conversation_id) is not None:
            # Editing is a safe preemption: the current model turn may finish,
            # but the runner observes the paused replacement before judging it.
            self.agents.pause_conversation_goal(agent_id, conversation_id)
        return self.agents.update_conversation_goal(agent_id, conversation_id, body)

    def pause_conversation_goal(self, agent_id: str, conversation_id: str) -> dict[str, Any]:
        # Persist first: the runner reloads this state after its current model turn.
        return self.agents.pause_conversation_goal(agent_id, conversation_id)

    async def resume_conversation_goal(self, agent_id: str, conversation_id: str) -> dict[str, Any]:
        if self.conversation_runs.active_run(agent_id, conversation_id) is not None:
            raise ServiceError("the goal is already running", status=409, code="conversation_running")
        result = self.agents.resume_conversation_goal(agent_id, conversation_id)
        objective = str((result.get("goal") or {}).get("objective") or "")
        try:
            run = await self.conversation_runs.start_run(agent_id, conversation_id, {
                "input": objective,
                "run_mode": "background",
                "goal_resume": True,
            })
        except Exception:
            self.agents.pause_conversation_goal(agent_id, conversation_id)
            raise
        return {**result, "run": run}

    def delete_conversation_goal(self, agent_id: str, conversation_id: str) -> dict[str, Any]:
        return self.agents.clear_conversation_goal(agent_id, conversation_id)

    def add_conversation_subgoal(
        self, agent_id: str, conversation_id: str, body: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self.agents.add_conversation_subgoal(agent_id, conversation_id, body)

    def remove_conversation_subgoal(
        self, agent_id: str, conversation_id: str, index: Any,
    ) -> dict[str, Any]:
        return self.agents.remove_conversation_subgoal(agent_id, conversation_id, index)

    def active_conversation_run(self, agent_id: str, conversation_id: str) -> dict[str, Any]:
        return {"run": self.conversation_runs.active_run(agent_id, conversation_id)}

    def get_conversation_run(
        self,
        agent_id: str,
        conversation_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        return self.conversation_runs.get_run(agent_id, conversation_id, run_id)

    async def stop_run(
        self,
        agent_id: str,
        conversation_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        return await self.conversation_runs.cancel_run(agent_id, conversation_id, run_id)

    def resolve_approval(
        self,
        run_id: str,
        body: Mapping[str, Any],
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        subsystem = str(body.get("subsystem") or "").strip().lower()
        choice = str(body.get("choice") or "").lower()
        resolution = dict(body)
        if choice == "always" and subsystem in {"skills", "memory"}:
            # Write gates are booleans, not command allowlists: approve this
            # write once, then disable the selected gate for future writes.
            resolution["choice"] = "once"
        result = self.agents.resolve_approval(run_id, resolution)
        if choice == "always" and agent_id and subsystem in {"skills", "memory"}:
            field = f"{subsystem}_write_approval"
            self.update_agent_config(agent_id, {field: False})
            result = {**result, "choice": "always", "subsystem": subsystem, "write_approval_disabled": True}
        return result

    @staticmethod
    def _conversation_dto(agent_id: str, item: Mapping[str, Any]) -> dict[str, Any]:
        return {"id": str(item.get("id") or item.get("session_id") or ""), "agent_id": agent_id, "title": str(item.get("title") or item.get("name") or "New Session"), "preview": str(item.get("preview") or ""), "model": str(item.get("model") or ""), "messages": int(item.get("message_count") or item.get("messages") or 0), "tools": int(item.get("tool_call_count") or item.get("tools") or 0), "created_at": item.get("created_at") or item.get("started_at"), "updated_at": item.get("last_active_at") or item.get("updated_at") or item.get("ended_at") or item.get("started_at") or item.get("created_at")}
