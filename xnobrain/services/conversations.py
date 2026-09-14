"""Conversation and streaming run service behavior."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..integrations.accounting_context import accounting_enabled
from ..integrations.router_accounting import AccountingUnavailable
from ..models.conversations import ConversationOwnershipContext
from ..repositories import StoreError
from ..repositories.conversation_creation import ConversationCreationRepository
from .base import ServiceError, iso
from .conversation_creation import create_bound, signed_creation


class ConversationsServiceMixin:
    @staticmethod
    def _personal_context() -> dict[str, Any]:
        return {
            "schema_version": 1,
            "id": "personal",
            "owner_kind": "personal",
            "organization_id": None,
            "payer_kind": "personal",
            "sponsor_grant_id": None,
            "membership_revision_at_create": None,
            "policy_revision_at_create": None,
            "state": "active",
        }

    def _conversation_profile(self, agent_id: str) -> Path:
        profile = self._agent_profile_path(agent_id)
        if profile.is_symlink() or not profile.is_dir():
            raise ServiceError("agent profile not found", status=404, code="not_found")
        return profile

    def _normalize_context(
        self,
        body: Mapping[str, Any],
        trusted_context: Any = None,
    ) -> dict[str, Any]:
        raw = body.get("ownership_context")
        creation_intent = str(body.get("creation_intent") or "").strip()
        verified = getattr(trusted_context, "ownership_context", None)
        if raw is None and creation_intent:
            if (
                not isinstance(verified, Mapping)
                or str(verified.get("creation_intent") or "") != creation_intent
            ):
                raise ServiceError(
                    "conversation creation intent was not verified by Control",
                    status=403,
                    code="conversation_context_not_verified",
                )
            raw = verified
        if raw is None:
            return self._personal_context()
        trusted_subject = str(getattr(trusted_context, "subject", "") or "").strip()
        if not trusted_subject:
            raise ServiceError(
                "trusted conversation context is required",
                status=401,
                code="trusted_context_required",
            )
        verified = getattr(trusted_context, "ownership_context", None)
        if not isinstance(verified, Mapping) or dict(verified) != dict(raw):
            raise ServiceError(
                "conversation ownership context was not verified by Control",
                status=403,
                code="conversation_context_not_verified",
            )
        try:
            normalized = {
                key: value
                for key, value in raw.items()
                if key not in {"creation_intent", "conversation_id"}
            }
            context = ConversationOwnershipContext.model_validate(normalized).model_dump(
                mode="json"
            )
            if context.get("revocation_version") is None:
                context.pop("revocation_version", None)
        except Exception as exc:
            raise ServiceError(
                "conversation ownership context is invalid",
                code="invalid_conversation_context",
            ) from exc
        # The exact owner snapshot is independently signed by Control after
        # active-membership resolution. The principal's optional organization
        # claim represents login/default context and need not equal the new
        # conversation owner.
        return context

    def _stored_context(
        self,
        agent_id: str,
        conversation_id: str,
        *,
        backfill: bool = True,
    ) -> dict[str, Any]:
        profile = self._conversation_profile(agent_id)
        stored = self.repository.get_conversation_context(profile, conversation_id)
        if stored is None:
            # Never leave a Personal sidecar for a forged/missing Hermes session.
            self.agents.get_conversation(agent_id, conversation_id)
            if not backfill:
                raise ServiceError(
                    "conversation ownership context is unavailable",
                    status=500,
                    code="conversation_context_missing",
                )
            context = self._personal_context()
            record = {
                **context,
                "conversation_id": conversation_id,
                "agent_id": agent_id,
                "actor_user_id": None,
                "executor_workspace_id": None,
                "created_at": iso(),
                "legacy_backfill": True,
            }
            try:
                stored = self.repository.create_conversation_context(profile, record)
            except StoreError as exc:
                if exc.code != "conversation_context_exists":
                    raise
                stored = self.repository.get_conversation_context(profile, conversation_id)
        if not isinstance(stored, Mapping):
            raise ServiceError(
                "conversation ownership context is unavailable",
                status=500,
                code="conversation_context_missing",
            )
        return self._public_context(stored)

    @staticmethod
    def _public_context(context: Mapping[str, Any]) -> dict[str, Any]:
        return {
            field: context.get(field)
            for field in ConversationOwnershipContext.model_fields
            if field not in {"owner_label", "revocation_version"} or context.get(field) is not None
        }

    def _bind_conversation_context(
        self,
        agent_id: str,
        conversation_id: str,
        context: Mapping[str, Any],
        trusted_context: Any = None,
    ) -> dict[str, Any]:
        profile = self._conversation_profile(agent_id)
        record = {
            **dict(context),
            "conversation_id": conversation_id,
            "agent_id": agent_id,
            "actor_user_id": str(getattr(trusted_context, "subject", "") or "") or None,
            "actor_tenant_id": str(getattr(trusted_context, "tenant_id", "") or ""),
            "executor_workspace_id": None,
            "created_at": iso(),
            "legacy_backfill": False,
        }
        return self._public_context(self.repository.create_conversation_context(profile, record))

    def authorize_conversation(self, agent_id, conversation_id, trusted_context, *, active=False):
        from .conversation_authority import require_binding

        if getattr(trusted_context, "subject", ""):
            return require_binding(
                self.repository, agent_id, conversation_id, trusted_context, active=active
            )
        return None

    def list_conversations(
        self, agent_id: str, *, page: int = 1, limit: int = 50, trusted_context=None
    ) -> dict[str, Any]:
        bounded_page = max(1, int(page))
        bounded_limit = max(1, min(int(limit), 1000))
        payload = self.agents.list_conversations(
            agent_id,
            {
                "page": bounded_page,
                "limit": bounded_limit,
            },
        )
        conversations = payload["conversations"]
        if getattr(trusted_context, "subject", ""):
            visible = []
            for item in conversations:
                identifier = str(item.get("id") or item.get("session_id") or "")
                try:
                    self.authorize_conversation(agent_id, identifier, trusted_context)
                except ServiceError as error:
                    if error.code != "conversation_owner_forbidden":
                        raise
                else:
                    visible.append(item)
            conversations = visible
        pagination = dict(payload.get("pagination") or {})
        return {
            "conversations": [self._conversation_dto(agent_id, item) for item in conversations],
            "pagination": {
                "page": int(pagination.get("page") or bounded_page),
                "limit": int(pagination.get("limit") or bounded_limit),
                "has_more": bool(pagination.get("has_more", False)),
            },
        }

    def create_conversation(
        self,
        agent_id: str,
        body: Mapping[str, Any],
        trusted_context: Any = None,
    ) -> dict[str, Any]:
        context = self._normalize_context(body, trusted_context)
        if context["state"] != "active":
            raise ServiceError(
                "conversation context is inactive", status=403, code="conversation_context_revoked"
            )
        bound_id = signed_creation(body, trusted_context)
        if bound_id:
            return create_bound(self, agent_id, bound_id, body, context, trusted_context)
        conversation_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        bound = self._bind_conversation_context(
            agent_id,
            conversation_id,
            context,
            trusted_context,
        )
        try:
            payload = self.agents.create_conversation(
                agent_id,
                {
                    "id": conversation_id,
                    "title": body.get("title") or "New Session",
                },
            )
        except Exception:
            self.repository.delete_conversation_context(
                self._conversation_profile(agent_id), conversation_id
            )
            raise
        return self._conversation_dto(
            agent_id,
            payload["conversation"],
            ownership_context=bound,
        )

    def get_conversation(self, agent_id: str, conversation_id: str) -> dict[str, Any]:
        payload = self.agents.get_conversation(agent_id, conversation_id)
        result = self._conversation_dto(agent_id, payload["conversation"])
        result["messages"] = payload["messages"]
        return result

    async def conversation_usage(
        self,
        agent_id: str,
        conversation_id: str,
    ) -> dict[str, Any]:
        name = self.agents._agent_name(agent_id)
        profile = self.agents._require_profile(name)
        session_key = self.agents._session_id(conversation_id)
        stored_session = self.agents._session(profile, session_key)
        if stored_session is None:
            raise AgentAPIError(
                f"Conversation not found: {session_key}",
                code="conversation_not_found",
                status=404,
            )
        session = dict(stored_session)

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

        # Usage needs counts and token totals, never message content. Use
        # session counters when available; query only missing aggregates.
        count_missing = "message_count" not in session
        steps_missing = "tool_call_count" not in session
        message_count = integer("message_count")
        tool_steps = integer("tool_call_count")
        message_tokens = 0
        token_fields = (
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
        )
        needs_message_tokens = not any(integer(field) for field in token_fields)
        db_path = profile / "state.db"
        if db_path.is_file() and (count_missing or steps_missing or needs_message_tokens):
            connection = self.agents._open_readonly_db(db_path)
            try:
                message_columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(messages)").fetchall()
                }
                projections = []
                if count_missing:
                    projections.append("COUNT(*) AS message_count")
                if steps_missing:
                    projections.append(
                        "SUM(CASE WHEN role = 'tool' THEN 1 ELSE 0 END) AS tool_steps"
                    )
                if needs_message_tokens and "token_count" in message_columns:
                    projections.append("COALESCE(SUM(MAX(token_count, 0)), 0) AS tokens")
                if projections:
                    # Column projections are fixed above; only the session ID is input.
                    statement = (
                        "SELECT " + ", ".join(projections) + " FROM messages WHERE session_id = ?"
                    )
                    row = connection.execute(statement, (session_key,)).fetchone()
                    if row is not None:
                        if count_missing:
                            message_count = max(0, int(row["message_count"] or 0))
                        if steps_missing:
                            tool_steps = max(0, int(row["tool_steps"] or 0))
                        if needs_message_tokens and "token_count" in message_columns:
                            message_tokens = max(0, int(row["tokens"] or 0))
            finally:
                connection.close()

        input_tokens = integer("input_tokens")
        output_tokens = integer("output_tokens")
        cache_read_tokens = integer("cache_read_tokens")
        cache_write_tokens = integer("cache_write_tokens")
        reasoning_tokens = integer("reasoning_tokens")
        total_tokens = input_tokens + output_tokens + cache_read_tokens + cache_write_tokens
        if total_tokens == 0:
            total_tokens = message_tokens

        started_at = number("started_at")
        ended_at = number("ended_at")
        execution_seconds = max(0.0, ended_at - started_at) if started_at and ended_at else 0.0
        actual_cost = session.get("actual_cost_usd")
        cost = (
            number("actual_cost_usd") if actual_cost is not None else number("estimated_cost_usd")
        )
        cost_source = str(session.get("cost_source") or "")
        cost_status = str(session.get("cost_status") or "")
        if actual_cost is None and cost <= 0 and not accounting_enabled():
            cost = await self.analytics.conversation_estimated_cost(
                agent_id,
                conversation_id,
            )
            if cost > 0:
                cost_source = "profile_ledger"
                cost_status = "estimated"
        model_config = session.get("model_config")
        context = model_config.get("xnobrain_context") if isinstance(model_config, Mapping) else {}
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
            context_payload.update(
                {
                    "limit": context_limit,
                    "percent": round(min(100.0, context_used / context_limit * 100), 2),
                }
            )
        if context_threshold:
            context_payload.update(
                {
                    "threshold": context_threshold,
                    "pressure_percent": round(
                        min(100.0, context_used / context_threshold * 100), 2
                    ),
                    "auto_compaction": bool(context.get("auto_compaction")),
                }
            )

        managed_accounting = accounting_enabled()
        weekly_budget = None
        if managed_accounting:
            cost = None
            cost_source = "gorouter"
            cost_status = "unavailable"
            try:
                accounted, weekly_budget = await asyncio.gather(
                    self.analytics.accounting.conversation(agent_id, conversation_id),
                    self.analytics.get_budget(agent_id),
                    return_exceptions=True,
                )
                if isinstance(weekly_budget, Exception):
                    weekly_budget = None
                if isinstance(accounted, Exception):
                    raise AccountingUnavailable() from accounted
                session["api_call_count"] = max(0, int(accounted.get("requests") or 0))
                input_tokens = max(0, int(accounted.get("prompt_tokens") or 0))
                output_tokens = max(0, int(accounted.get("completion_tokens") or 0))
                cache_read_tokens = max(0, int(accounted.get("cache_read_tokens") or 0))
                cache_write_tokens = max(0, int(accounted.get("cache_write_tokens") or 0))
                total_tokens = input_tokens + output_tokens + cache_read_tokens + cache_write_tokens
                cost = max(0.0, float(accounted["cost_usd"]))
                cost_status = "accounted" if session["api_call_count"] > 0 else "unknown"
            except (AccountingUnavailable, KeyError, TypeError, ValueError):
                pass
        return {
            "conversation_id": conversation_id,
            "api_calls": integer("api_call_count"),
            "duration": f"{execution_seconds:.2f}s",
            "execution_seconds": round(execution_seconds, 3),
            "messages": message_count,
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
            # One browser response includes session totals and the agent-wide
            # quota-week status. Admission still obtains a fresh check on POST.
            "weekly_budget": weekly_budget
            if managed_accounting
            else await self.analytics.get_budget(agent_id),
        }

    def rename_conversation(
        self, agent_id: str, conversation_id: str, body: Mapping[str, Any]
    ) -> dict[str, Any]:
        payload = self.agents.update_conversation(
            agent_id, conversation_id, {"title": body.get("title")}
        )
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
        creation = ConversationCreationRepository(
            self.repository, self._conversation_profile(agent_id), agent_id, conversation_id
        )
        with creation.locked():
            receipt = creation.read()
            if receipt is not None:
                receipt["state"] = "deleted"
                creation.save(receipt)
            result = self.agents.delete_conversation(agent_id, conversation_id)
        self.repository.delete_conversation_runs(agent_id, conversation_id)
        self.repository.delete_conversation_context(
            self._conversation_profile(agent_id), conversation_id
        )
        return result

    async def stream_conversation(
        self, agent_id: str, conversation_id: str, body: Mapping[str, Any], trusted_context=None
    ):
        if getattr(trusted_context, "subject", ""):
            run = await self.start_conversation_run(
                agent_id, conversation_id, body, trusted_context
            )
            async for event in self.conversation_runs.events(agent_id, conversation_id, run["id"]):
                self.authorize_conversation(agent_id, conversation_id, trusted_context)
                yield self.conversation_runs._sse(event)
            yield b"data: [DONE]\n\n"
            return
        context = self._stored_context(agent_id, conversation_id)
        async for event in self.conversation_runs.legacy_stream(
            agent_id,
            conversation_id,
            body,
            ownership_context=context,
        ):
            yield event

    async def start_conversation_run(
        self,
        agent_id: str,
        conversation_id: str,
        body: Mapping[str, Any],
        trusted_context: Any = None,
        *,
        custom_page_datasets: list[str] | None = None,
        custom_page_revision: int | None = None,
        dispatch_guard=None,
        custom_page_schedule: str | None = None,
        ui_assistance: dict | None = None,
    ) -> dict[str, Any]:
        if trusted_context is not None and getattr(trusted_context, "subject", ""):
            from .conversation_authority import require_binding

            context = self._public_context(
                require_binding(
                    self.repository, agent_id, conversation_id, trusted_context, active=True
                )
            )
        else:
            context = self._stored_context(agent_id, conversation_id)
        return await self.conversation_runs.start_run(
            agent_id,
            conversation_id,
            body,
            ownership_context=context,
            trusted_context=trusted_context,
            custom_page_datasets=custom_page_datasets,
            custom_page_revision=custom_page_revision,
            dispatch_guard=dispatch_guard,
            custom_page_schedule=custom_page_schedule,
            ui_assistance=ui_assistance,
        )

    def _with_context(
        self,
        agent_id: str,
        conversation_id: str,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            **dict(result),
            "ownership_context": self._stored_context(agent_id, conversation_id),
        }

    def get_conversation_goal(self, agent_id: str, conversation_id: str) -> dict[str, Any]:
        return self._with_context(
            agent_id,
            conversation_id,
            self.agents.get_conversation_goal(agent_id, conversation_id),
        )

    async def create_conversation_goal(
        self,
        agent_id: str,
        conversation_id: str,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        if self.conversation_runs.active_run(agent_id, conversation_id) is not None:
            raise ServiceError(
                "pause the current response before starting a goal",
                status=409,
                code="conversation_running",
            )
        result = self.agents.set_conversation_goal(agent_id, conversation_id, body)
        try:
            run = await self.conversation_runs.start_run(
                agent_id,
                conversation_id,
                {
                    "input": str(body.get("objective") or ""),
                    "run_mode": "background",
                },
                ownership_context=self._stored_context(agent_id, conversation_id),
            )
        except Exception:
            self.agents.clear_conversation_goal(agent_id, conversation_id)
            raise
        return self._with_context(agent_id, conversation_id, {**result, "run": run})

    def update_conversation_goal(
        self,
        agent_id: str,
        conversation_id: str,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        if self.conversation_runs.active_run(agent_id, conversation_id) is not None:
            # Editing is a safe preemption: the current model turn may finish,
            # but the runner observes the paused replacement before judging it.
            self.agents.pause_conversation_goal(agent_id, conversation_id)
        return self._with_context(
            agent_id,
            conversation_id,
            self.agents.update_conversation_goal(agent_id, conversation_id, body),
        )

    def pause_conversation_goal(self, agent_id: str, conversation_id: str) -> dict[str, Any]:
        # Persist first: the runner reloads this state after its current model turn.
        return self._with_context(
            agent_id,
            conversation_id,
            self.agents.pause_conversation_goal(agent_id, conversation_id),
        )

    async def resume_conversation_goal(self, agent_id: str, conversation_id: str) -> dict[str, Any]:
        if self.conversation_runs.active_run(agent_id, conversation_id) is not None:
            raise ServiceError(
                "the goal is already running", status=409, code="conversation_running"
            )
        result = self.agents.resume_conversation_goal(agent_id, conversation_id)
        objective = str((result.get("goal") or {}).get("objective") or "")
        try:
            run = await self.conversation_runs.start_run(
                agent_id,
                conversation_id,
                {
                    "input": objective,
                    "run_mode": "background",
                    "goal_resume": True,
                },
                ownership_context=self._stored_context(agent_id, conversation_id),
            )
        except Exception:
            self.agents.pause_conversation_goal(agent_id, conversation_id)
            raise
        return self._with_context(agent_id, conversation_id, {**result, "run": run})

    def delete_conversation_goal(self, agent_id: str, conversation_id: str) -> dict[str, Any]:
        return self._with_context(
            agent_id,
            conversation_id,
            self.agents.clear_conversation_goal(agent_id, conversation_id),
        )

    def add_conversation_subgoal(
        self,
        agent_id: str,
        conversation_id: str,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self._with_context(
            agent_id,
            conversation_id,
            self.agents.add_conversation_subgoal(agent_id, conversation_id, body),
        )

    def remove_conversation_subgoal(
        self,
        agent_id: str,
        conversation_id: str,
        index: Any,
    ) -> dict[str, Any]:
        return self._with_context(
            agent_id,
            conversation_id,
            self.agents.remove_conversation_subgoal(agent_id, conversation_id, index),
        )

    def active_conversation_run(self, agent_id: str, conversation_id: str) -> dict[str, Any]:
        return {
            "run": self.conversation_runs.active_run(agent_id, conversation_id),
            "ownership_context": self._stored_context(agent_id, conversation_id),
        }

    def get_conversation_run(
        self,
        agent_id: str,
        conversation_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        return self.conversation_runs.get_run(agent_id, conversation_id, run_id)

    async def stop_conversation_child_run(
        self,
        agent_id: str,
        conversation_id: str,
        run_id: str,
        child_run_id: str,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._stored_context(agent_id, conversation_id)
        return await self.conversation_runs.cancel_child_run(
            agent_id,
            conversation_id,
            run_id,
            child_run_id,
            body,
        )

    def update_conversation_run_todo(
        self,
        agent_id: str,
        conversation_id: str,
        run_id: str,
        todo_id: str,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._stored_context(agent_id, conversation_id)
        return self.conversation_runs.update_todo(
            agent_id,
            conversation_id,
            run_id,
            todo_id,
            body,
        )

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
            result = {
                **result,
                "choice": "always",
                "subsystem": subsystem,
                "write_approval_disabled": True,
            }
        return result

    def _conversation_dto(
        self,
        agent_id: str,
        item: Mapping[str, Any],
        *,
        ownership_context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        conversation_id = str(item.get("id") or item.get("session_id") or "")
        return {
            "id": conversation_id,
            "agent_id": agent_id,
            "title": str(item.get("title") or item.get("name") or "New Session"),
            "preview": str(item.get("preview") or ""),
            "model": str(item.get("model") or ""),
            "messages": int(item.get("message_count") or item.get("messages") or 0),
            "tools": int(item.get("tool_call_count") or item.get("tools") or 0),
            "created_at": item.get("created_at") or item.get("started_at"),
            "updated_at": item.get("last_active_at")
            or item.get("updated_at")
            or item.get("ended_at")
            or item.get("started_at")
            or item.get("created_at"),
            "ownership_context": dict(ownership_context)
            if ownership_context is not None
            else self._stored_context(agent_id, conversation_id),
        }
