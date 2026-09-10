"""Runtime budget settings and local operational analytics.

Managed financial reads require Control/Router accounting. Legacy profile sums
are available only for explicitly non-Router execution and are not invoice facts.
Budget settings import once to first-party SQLite; YAML is never written here.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml

from ..integrations.accounting_context import accounting_enabled
from ..integrations.control_accounting import ControlAccountingClient
from ..integrations.router_accounting import AccountingUnavailable
from ..repositories.agent_budgets import AgentBudgetStore
from ..integrations.analytics import (
    aggregate_profile,
    bucket_start_iso,
    period_spend,
    profile_model_usage,
    skill_usage_profile,
)
from ..repositories.skill_usage import SKILL_USAGE_INSTRUMENTATION_VERSION
from .base import ServiceError

_TOKEN_COLS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)
_BUDGET_KEY = "xnobrain_budget"
DEFAULT_WEEKLY_BUDGET_USD = 20.0
_MERGED_TTL = 20.0
_CACHE_CAP = 512


class AnalyticsService:
    """Compute-on-read usage analytics across the named agent profiles."""

    def __init__(self, agents: Any, router: Any, repository: Any):
        self.agents = agents
        self.router = router
        self.repository = repository
        self.accounting = ControlAccountingClient()
        self._budget_store = None
        self._partials: dict[tuple, tuple[int, dict[str, Any]]] = {}
        self._merged: dict[tuple, tuple[float, dict[str, Any]]] = {}
        self._workspace: dict[tuple, tuple[float, dict[str, Any]]] = {}
        self._workspace_lock = asyncio.Lock()
        self._weekly_cost_lock = asyncio.Lock()
        self._weekly_cost_cache: tuple[float, str, dict[str, float]] | None = None
        self._sem = asyncio.Semaphore(8)

    # ---- public API ---------------------------------------------------------

    def list_selectable_agents(self) -> dict[str, Any]:
        """[{agent_id, display_name}] for the picker. No ``state.db`` reads."""
        rows = [
            {"agent_id": name, "display_name": self._display(item, name)}
            for item in self._agents()
            for name in (str(item.get("name") or ""),)
            if name
        ]
        rows.sort(key=lambda row: row["display_name"].lower())
        return {"agents": rows}

    async def usage_summary(
        self,
        *,
        agent_ids: list[str],
        start_epoch: float,
        end_epoch: float,
        bucket: str,
    ) -> dict[str, Any]:
        self._require_financial_mode()
        items, available = self._resolve_items(agent_ids)
        summary = await self._workspace_summary(
            items,
            selected=bool(agent_ids),
            start=start_epoch,
            end=end_epoch,
            bucket=bucket,
        )
        return await self._decorate_overview(summary, items, agent_ids, available)

    async def usage_overview(
        self,
        *,
        agent_ids: list[str],
        start_epoch: float,
        end_epoch: float,
        bucket: str,
    ) -> dict[str, Any]:
        """Return dashboard totals and attribution without chart payloads."""
        self._require_financial_mode()
        items, available = self._resolve_items(agent_ids)
        summary = await self._workspace_summary(
            items,
            selected=bool(agent_ids),
            start=start_epoch,
            end=end_epoch,
            bucket=bucket,
        )
        complete = await self._decorate_overview(
            summary,
            items,
            agent_ids,
            available,
        )
        return {
            key: value
            for key, value in complete.items()
            if key not in {"by_model", "by_provider", "series"}
        }

    async def _decorate_overview(
        self,
        summary: dict[str, Any],
        items: list[dict[str, Any]],
        agent_ids: list[str],
        agents_available: int,
    ) -> dict[str, Any]:
        by_path = {str(item.get("name") or ""): item for item in items}
        attribution_items = items if len(items) == agents_available else None
        for row in summary["agents"]:
            item = by_path.get(row["agent_id"])
            row["budget"] = (
                await self._budget_status(
                    row["agent_id"],
                    item,
                    attribution_items=attribution_items,
                )
                if item
                else None
            )
        summary["agents_selected"] = (
            [str(item.get("name") or "") for item in items] if agent_ids else []
        )
        summary["agents_available"] = agents_available
        summary["quota"] = await self._quota_overlay()
        return summary

    async def agent_usage(
        self,
        agent_id: str,
        *,
        start_epoch: float,
        end_epoch: float,
        bucket: str,
    ) -> dict[str, Any]:
        self._require_financial_mode()
        item = self._require_item(agent_id)
        summary = await self._summary([item], start_epoch, end_epoch, bucket)
        agent_row = (
            summary["agents"][0]
            if summary["agents"]
            else {
                "agent_id": agent_id,
                "display_name": self._display(item, agent_id),
                "totals": summary["totals"],
            }
        )
        agent_row["budget"] = await self._budget_status(agent_id, item)
        agent_row["by_model"] = summary["by_model"]
        agent_row["series"] = summary["series"]
        agent_row["range_from"] = _epoch_iso(start_epoch)
        agent_row["range_to"] = _epoch_iso(end_epoch)
        agent_row["bucket"] = bucket
        agent_row["timezone"] = "UTC"
        return agent_row

    async def models_breakdown(
        self,
        *,
        agent_ids: list[str],
        start_epoch: float,
        end_epoch: float,
        bucket: str,
    ) -> dict[str, Any]:
        self._require_financial_mode()
        items, _ = self._resolve_items(agent_ids)
        summary = await self._workspace_summary(
            items,
            selected=bool(agent_ids),
            start=start_epoch,
            end=end_epoch,
            bucket=bucket,
        )
        return {
            "range_from": _epoch_iso(start_epoch),
            "range_to": _epoch_iso(end_epoch),
            "by_model": summary["by_model"],
            "totals": summary["totals"],
            "by_provider": summary["by_provider"],
            "source": summary["source"],
            "timezone": "UTC",
        }

    async def timeseries(
        self,
        *,
        agent_ids: list[str],
        start_epoch: float,
        end_epoch: float,
        bucket: str,
    ) -> dict[str, Any]:
        self._require_financial_mode()
        items, _ = self._resolve_items(agent_ids)
        summary = await self._workspace_summary(
            items,
            selected=bool(agent_ids),
            start=start_epoch,
            end=end_epoch,
            bucket=bucket,
        )
        return {
            "range_from": _epoch_iso(start_epoch),
            "range_to": _epoch_iso(end_epoch),
            "bucket": bucket,
            "series": summary["series"],
            "source": summary["source"],
            "timezone": "UTC",
        }

    async def skill_usage(
        self,
        agent_id: str,
        *,
        start_epoch: float,
        end_epoch: float,
        work_context_id: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Return paged, privacy-safe skill lifecycle metrics for one agent."""
        item = self._require_item(agent_id)
        profile = self._profile_dir(item)
        context_id = work_context_id or "personal"
        _validate_work_context_id(context_id)
        after = _decode_skill_cursor(cursor) if cursor else None
        page_limit = max(1, min(100, int(limit or 100)))
        if profile is None:
            return _empty_skill_usage(agent_id, context_id, start_epoch, end_epoch)

        has_events = await asyncio.to_thread(
            self.repository.has_skill_usage_events,
            agent_id,
        )
        if has_events:
            events = await asyncio.to_thread(
                self.repository.list_skill_usage_events,
                agent_id,
                start_epoch=start_epoch,
                end_epoch=end_epoch,
                work_context_id=context_id,
            )
            result = _aggregate_skill_events(
                events,
                start_epoch=start_epoch,
                end_epoch=end_epoch,
                context_id=context_id,
            )
        elif work_context_id is None or context_id == "personal":
            result = await asyncio.to_thread(
                skill_usage_profile,
                profile,
                start_epoch=start_epoch,
                end_epoch=end_epoch,
            )
            result["work_context_id"] = context_id
            result["coverage"]["instrumentation_version"] = None
        else:
            result = _empty_skill_usage(agent_id, context_id, start_epoch, end_epoch)

        items = result["items"]
        if after is not None:
            items = [item for item in items if _skill_item_key(item) > after]
        page = items[:page_limit]
        next_cursor = (
            _encode_skill_cursor(_skill_item_key(page[-1]))
            if len(items) > page_limit and page
            else None
        )
        result["items"] = page
        result["next_cursor"] = next_cursor
        result["limit"] = page_limit
        return {"agent_id": agent_id, **result}

    async def get_budget(self, agent_id: str) -> dict[str, Any]:
        item = self._require_item(agent_id)
        return await self._budget_status(agent_id, item)

    async def set_budget(
        self,
        agent_id: str,
        patch: Mapping[str, Any],
    ) -> dict[str, Any]:
        item = self._require_item(agent_id)
        settings = self._budget_settings(agent_id)
        self._budgets().set(
            settings["workspace_id"], settings["context_id"], agent_id,
            patch.get("weekly_usd"), patch.get("revision"),
        )
        self._merged.clear()
        return await self._budget_status(agent_id, item)

    async def require_execution_budget(self, agent_id: str) -> dict[str, Any]:
        """Reject a new top-level execution once weekly spend reaches its limit."""
        status = await self.get_budget(agent_id)
        if status.get("status") == "unavailable":
            raise ServiceError(
                "Authoritative budget check unavailable. New work is paused; accepted work may finish.",
                status=503, code="budget_check_unavailable",
            )
        if not status["accepting_chats"]:
            raise ServiceError(
                "Weekly budget reached. Increase the agent budget or wait until Sunday.",
                status=429,
                code="weekly_budget_exceeded",
            )
        return status

    async def require_chat_budget(self, agent_id: str) -> dict[str, Any]:
        """Compatibility alias for callers using the former chat-only name."""
        return await self.require_execution_budget(agent_id)

    # ---- internals ----------------------------------------------------------

    def _agents(self) -> list[dict[str, Any]]:
        return list(self.agents.list_agents()["agents"])

    def _resolve_items(self, agent_ids: list[str]) -> tuple[list[dict[str, Any]], int]:
        items = self._agents()
        available = len(items)
        if agent_ids:
            want = set(agent_ids)
            items = [item for item in items if str(item.get("name") or "") in want]
        return items, available

    def _require_item(self, agent_id: str) -> dict[str, Any]:
        # 404 through the Hermes manager if the agent does not exist.
        self.agents.describe_agent(agent_id, include_memory=False)
        for item in self._agents():
            if str(item.get("name") or "") == agent_id:
                return item
        raise ServiceError("agent not found", status=404, code="agent_not_found")

    @staticmethod
    def _display(item: Mapping[str, Any], fallback: str) -> str:
        meta = item.get("metadata") or {}
        return str(meta.get("display_name") or meta.get("title") or fallback)

    @staticmethod
    def _profile_dir(item: Mapping[str, Any]) -> Path | None:
        raw = str(item.get("profile_path") or "").strip()
        return Path(raw) if raw else None

    async def _summary(
        self,
        items: list[Mapping[str, Any]],
        start: float,
        end: float,
        bucket: str,
    ) -> dict[str, Any]:
        key = (
            frozenset(str(item.get("name") or "") for item in items),
            round(start),
            round(end),
            bucket,
        )
        now = time.time()
        cached = self._merged.get(key)
        if cached and now - cached[0] < _MERGED_TTL:
            return cached[1]
        effective = "day" if bucket == "week" else bucket
        partials = await self._collect_partials(items, start, end, effective)
        summary = self._merge(partials, start, end, bucket)
        if len(self._merged) > _CACHE_CAP:
            self._merged.clear()
        self._merged[key] = (now, summary)
        return summary

    async def _workspace_summary(
        self,
        items: list[Mapping[str, Any]],
        *,
        selected: bool,
        start: float,
        end: float,
        bucket: str,
    ) -> dict[str, Any]:
        """Single-flight computation shared by parallel dashboard endpoints."""
        key = (
            frozenset(str(item.get("name") or "") for item in items),
            selected,
            round(start),
            round(end),
            bucket,
        )
        now = time.time()
        cached = self._workspace.get(key)
        if cached and now - cached[0] < _MERGED_TTL:
            return copy.deepcopy(cached[1])
        async with self._workspace_lock:
            now = time.time()
            cached = self._workspace.get(key)
            if cached and now - cached[0] < _MERGED_TTL:
                return copy.deepcopy(cached[1])
            live = await self._summary(items, start, end, bucket)
            summary = await self._with_durable_workspace(
                live,
                selected=selected,
                start=start,
                end=end,
                bucket=bucket,
            )
            if len(self._workspace) > _CACHE_CAP:
                self._workspace.clear()
            self._workspace[key] = (now, summary)
            return copy.deepcopy(summary)

    async def _collect_partials(
        self,
        items: list[Mapping[str, Any]],
        start: float,
        end: float,
        effective: str,
    ) -> list[dict[str, Any]]:
        async def read_one(item: Mapping[str, Any]) -> dict[str, Any]:
            agent_id = str(item.get("name") or "")
            display = self._display(item, agent_id)
            profile_dir = self._profile_dir(item)
            partial = {"totals": {}, "by_model": [], "series": []}
            if profile_dir is not None:
                db = profile_dir / "state.db"
                try:
                    mtime = db.stat().st_mtime_ns
                except OSError:
                    mtime = None
                ckey = (agent_id, round(start), round(end), effective)
                hit = self._partials.get(ckey)
                if hit and mtime is not None and hit[0] == mtime:
                    partial = hit[1]
                else:
                    async with self._sem:
                        partial = await asyncio.to_thread(
                            aggregate_profile,
                            profile_dir,
                            start_epoch=start,
                            end_epoch=end,
                            bucket=effective,
                        )
                    if mtime is not None:
                        if len(self._partials) > _CACHE_CAP:
                            self._partials.clear()
                        self._partials[ckey] = (mtime, partial)
            return {"agent_id": agent_id, "display_name": display, "partial": partial}

        return list(await asyncio.gather(*(read_one(item) for item in items)))

    async def _with_durable_workspace(
        self,
        live: dict[str, Any],
        *,
        selected: bool,
        start: float,
        end: float,
        bucket: str,
    ) -> dict[str, Any]:
        """Decorate profile totals without reading any router-owned state."""
        api_calls = int(live["totals"]["api_calls"])
        message = (
            "Agent filters use live profile attribution. Usage from deleted "
            "conversations or agents cannot be assigned to this selection."
            if selected
            else "Runtime totals use current agent profiles. Central usage is available in Control."
        )
        return {
            **live,
            "source": {
                "kind": "live_profiles",
                "durable": False,
                "label": "Current agent profiles",
                "message": message,
            },
            "by_provider": _providers_from_models(live["by_model"]),
            "request_status": {
                "total": api_calls,
                "successful": api_calls,
                "failed": 0,
                "success_rate": 100.0 if api_calls else 0.0,
            },
            "attribution": _attribution(live["totals"], live["totals"], durable=False),
        }

    def _merge(
        self,
        partials: list[dict[str, Any]],
        start: float,
        end: float,
        bucket: str,
    ) -> dict[str, Any]:
        totals = _zero_totals()
        models: dict[tuple[str, str], dict[str, Any]] = {}
        series: dict[str, dict[str, Any]] = {}
        agents: list[dict[str, Any]] = []
        for entry in partials:
            partial = entry["partial"] or {}
            agent_totals = _accumulate_totals(_zero_totals(), partial.get("totals") or {})
            _accumulate_totals(totals, partial.get("totals") or {})
            agents.append(
                {
                    "agent_id": entry["agent_id"],
                    "display_name": entry["display_name"],
                    "totals": _finish_totals(agent_totals),
                }
            )
            for row in partial.get("by_model") or []:
                mk = (str(row.get("model") or "unknown"), str(row.get("provider") or ""))
                _accumulate_model(models.setdefault(mk, _zero_model(*mk)), row)
            for row in partial.get("series") or []:
                label = _fold_bucket(str(row.get("bucket") or ""), bucket)
                _accumulate_bucket(series.setdefault(label, _zero_bucket(label)), row)
        return {
            "range_from": _epoch_iso(start),
            "range_to": _epoch_iso(end),
            "period_days": max(1, round((end - start) / 86400)),
            "bucket": bucket,
            "generated_at": _iso(),
            "timezone": "UTC",
            "totals": _finish_totals(totals),
            "agents": agents,
            "by_model": [
                _finish_model(models[key])
                for key in sorted(
                    models, key=lambda k: -(models[k]["input_tokens"] + models[k]["output_tokens"])
                )
            ],
            "series": [
                _finish_bucket(series.get(label, _zero_bucket(label)))
                for label in _dense_buckets(start, end, bucket)
            ],
        }

    async def _budget_status(
        self,
        agent_id: str,
        item: Mapping[str, Any] | None,
        *,
        attribution_items: list[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        settings = self._budget_settings(agent_id)
        weekly = settings["weekly_usd"]
        configured_weekly = weekly if settings["configured"] else None
        basis = "estimated"
        if accounting_enabled():
            common = {
                "weekly_usd": weekly, "configured": settings["configured"],
                "default_weekly_usd": DEFAULT_WEEKLY_BUDGET_USD,
                "revision": settings["revision"], "accounting_id": settings["accounting_id"],
                "enforcement": "soft_admission", "cost_basis": "accounted", "currency": "USD",
                "source": "gorouter", "accounting_status": "unavailable",
            }
            try:
                decision = await self.accounting.weekly(agent_id, weekly)
                return {**common, **decision}
            except AccountingUnavailable:
                return {
                    **common, "status": "unavailable", "accepting_chats": False,
                    "spend_usd": None, "remaining_usd": None, "percent_used": None,
                    "period_start": None, "period_end": None, "week_starts_on": None,
                    "severity": "normal",
                }
        profile_dir = self._profile_dir(item or {})
        now = datetime.now(timezone.utc)
        week_start = _sunday_start(now)
        week_end = week_start + timedelta(days=7)
        if profile_dir is None:
            spend = 0.0
        elif basis == "actual":
            spend = await asyncio.to_thread(
                period_spend,
                profile_dir,
                since_epoch=week_start.timestamp(),
                until_epoch=now.timestamp(),
                cost_basis=basis,
            )
        else:
            spends = await self._weekly_estimated_spends(
                week_start,
                now,
                items=attribution_items,
            )
            spend = spends.get(agent_id, 0.0)
        percent = max(0.0, spend / weekly * 100)
        accepting = spend < weekly
        severity = (
            "red"
            if percent >= 90
            else "orange"
            if percent >= 80
            else "yellow"
            if percent >= 70
            else "normal"
        )
        return {
            "source": "legacy_local",
            "enforcement": "soft_admission",
            "revision": settings["revision"],
            "weekly_usd": weekly,
            "configured": configured_weekly is not None,
            "default_weekly_usd": DEFAULT_WEEKLY_BUDGET_USD,
            "cost_basis": basis,
            "currency": "USD",
            "period_start": _iso(week_start),
            "period_end": _iso(week_end),
            "week_starts_on": "sunday",
            "spend_usd": round(spend, 6),
            "remaining_usd": round(max(0.0, weekly - spend), 6),
            "percent_used": round(percent, 2),
            "status": "ok" if accepting else "exceeded",
            "severity": severity,
            "accepting_chats": accepting,
        }

    async def _weekly_estimated_spends(
        self,
        week_start: datetime,
        now: datetime,
        *,
        items: list[Mapping[str, Any]] | None = None,
    ) -> dict[str, float]:
        """Read the locally recorded estimated costs for each live agent."""
        cache_key = _iso(week_start)
        cached = self._weekly_cost_cache
        current = time.time()
        if cached and cached[1] == cache_key and current - cached[0] < 5.0:
            return cached[2]
        async with self._weekly_cost_lock:
            cached = self._weekly_cost_cache
            current = time.time()
            if cached and cached[1] == cache_key and current - cached[0] < 5.0:
                return cached[2]

            items = items if items is not None else self._agents()

            async def read(item: Mapping[str, Any]) -> tuple[str, list[dict[str, Any]]]:
                agent_id = str(item.get("name") or "")
                profile_dir = self._profile_dir(item)
                if not agent_id or profile_dir is None:
                    return agent_id, []
                async with self._sem:
                    rows = await asyncio.to_thread(
                        profile_model_usage,
                        profile_dir,
                        since_epoch=week_start.timestamp(),
                        until_epoch=now.timestamp(),
                    )
                return agent_id, rows

            attributed = dict(await asyncio.gather(*(read(item) for item in items)))
            result = {
                agent_id: sum(float(row.get("estimated_cost_usd") or 0) for row in rows)
                for agent_id, rows in attributed.items()
            }
            self._weekly_cost_cache = (current, cache_key, result)
            return result

    async def conversation_estimated_cost(
        self,
        agent_id: str,
        conversation_id: str,
    ) -> float:
        """Return the estimated cost recorded in one conversation ledger."""
        self._require_financial_mode()
        item = self._require_item(agent_id)
        profile_dir = self._profile_dir(item)
        if profile_dir is None:
            return 0.0
        now = datetime.now(timezone.utc)
        week_start = _sunday_start(now)
        rows = await asyncio.to_thread(
            profile_model_usage,
            profile_dir,
            since_epoch=week_start.timestamp(),
            until_epoch=now.timestamp(),
            session_id=conversation_id,
        )
        return round(sum(float(row.get("estimated_cost_usd") or 0) for row in rows), 6)

    def _require_financial_mode(self):
        if accounting_enabled():
            raise ServiceError(
                "Financial usage is owned by Control/Router. Use the financial usage API; legacy totals are not authoritative.",
                status=503, code="router_accounting_required",
            )

    def _budgets(self):
        if self._budget_store is None:
            self._budget_store = AgentBudgetStore(self.repository.data_dir)
        return self._budget_store

    def _budget_settings(self, agent_id):
        # Settings remain local to this mounted workspace. This namespace is
        # not sent to Router and never substitutes for Control's binding.
        return self._budgets().get(
            os.environ.get("RUNTIME_ACCOUNTING_WORKSPACE_ID", "local_workspace"),
            "personal", agent_id, self.repository.profile_path(agent_id) / "config.yaml",
        )

    def _read_config(self, agent_id: str) -> tuple[dict[str, Any], Path]:
        path = self.repository.profile_path(agent_id) / "config.yaml"
        if not path.is_file():
            return {}, path
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            data = {}
        return (data if isinstance(data, dict) else {}), path

    async def _quota_overlay(self) -> dict[str, Any]:
        return {
            "available": False,
            "provider": "",
            "model": "auto",
            "plan": "",
            "message": "Central limits are available from Control.",
            "quotas": [],
        }


def _empty_skill_usage(
    agent_id: str,
    context_id: str,
    start_epoch: float,
    end_epoch: float,
) -> dict[str, Any]:
    return {
        "agent_id": agent_id,
        "work_context_id": context_id,
        "items": [],
        "next_cursor": None,
        "limit": 100,
        "coverage": {
            "source": "xnobrain_skill_lifecycle_events",
            "attribution": "explicit_lifecycle",
            "from": start_epoch,
            "to": end_epoch,
            "instrumented": False,
            "instrumentation_version": SKILL_USAGE_INSTRUMENTATION_VERSION,
            "event_count": 0,
            "unattributed_tool_invocations": 0,
            "message": "No measured data",
        },
    }


def _aggregate_skill_events(
    events: list[Mapping[str, Any]],
    *,
    start_epoch: float,
    end_epoch: float,
    context_id: str,
) -> dict[str, Any]:
    measured: dict[tuple[str, str], dict[str, Any]] = {}
    unattributed_tools = 0
    for event in events:
        event_type = str(event.get("event_type") or "")
        associations = event.get("associated_skills")
        if isinstance(associations, list) and associations:
            linked = [item for item in associations if isinstance(item, Mapping)]
        elif event.get("skill_id"):
            linked = [event]
        else:
            linked = []
        if event_type == "skill.tool_invoked" and not linked:
            unattributed_tools += 1
        for association in linked:
            skill_id = str(association.get("skill_id") or "")
            digest = str(association.get("skill_digest") or "")
            if not skill_id:
                continue
            key = (skill_id, digest)
            item = measured.setdefault(
                key,
                {
                    "skill_id": skill_id,
                    "skill_digest": digest or None,
                    "requested_count": 0,
                    "loaded_count": 0,
                    "reference_reads": 0,
                    "distinct_runs": set(),
                    "last_used_at": None,
                    "tool_invocations": 0,
                    "tool_completed": 0,
                    "errors": 0,
                    "attribution": "observed",
                },
            )
            if event_type == "skill.requested":
                item["requested_count"] += 1
            elif event_type == "skill.loaded":
                item["loaded_count"] += 1
                item["distinct_runs"].add(str(event.get("run_id") or ""))
            elif event_type == "skill.reference_read":
                item["reference_reads"] += 1
            elif event_type == "skill.tool_invoked":
                item["tool_invocations"] += 1
            elif event_type == "skill.tool_completed":
                item["tool_completed"] += 1
            elif event_type == "skill.tool_failed":
                item["errors"] += 1
            occurred_at = str(event.get("occurred_at") or "")
            if occurred_at and (item["last_used_at"] is None or occurred_at > item["last_used_at"]):
                item["last_used_at"] = occurred_at
            if str(event.get("attribution") or "") == "multiple":
                item["attribution"] = "multiple"
    items = []
    for item in measured.values():
        item["distinct_runs"] = len(item["distinct_runs"])
        items.append(item)
    items.sort(key=_skill_item_key)
    return {
        "work_context_id": context_id,
        "items": items,
        "coverage": {
            "source": "xnobrain_skill_lifecycle_events",
            "attribution": "explicit_lifecycle",
            "from": start_epoch,
            "to": end_epoch,
            "instrumented": True,
            "instrumentation_version": SKILL_USAGE_INSTRUMENTATION_VERSION,
            "event_count": len(events),
            "unattributed_tool_invocations": unattributed_tools,
            "message": (
                "Measured skill lifecycle metadata"
                if events
                else "No measured data in selected range"
            ),
        },
    }


def _validate_work_context_id(value: str) -> None:
    if (
        not value
        or len(value) > 256
        or value[0] not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
        or any(
            char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:-"
            for char in value
        )
    ):
        raise ValueError("invalid work context id")


def _skill_item_key(item: Mapping[str, Any]) -> tuple[str, str]:
    return str(item.get("skill_id") or ""), str(item.get("skill_digest") or "")


def _encode_skill_cursor(key: tuple[str, str]) -> str:
    payload = json.dumps(list(key), separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_skill_cursor(cursor: str) -> tuple[str, str]:
    try:
        if not cursor or len(cursor) > 1024:
            raise ValueError
        padding = "=" * (-len(cursor) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(cursor + padding))
        if not isinstance(decoded, list) or len(decoded) != 2:
            raise ValueError
        key = str(decoded[0]), str(decoded[1])
        if not key[0] or len(key[0]) > 256 or len(key[1]) > 71:
            raise ValueError
        return key
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("invalid skill usage cursor") from error


# ---- module helpers ---------------------------------------------------------


def _iso(value: datetime | None = None) -> str:
    return (value or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")


def _epoch_iso(value: float) -> str:
    return _iso(datetime.fromtimestamp(value, tz=timezone.utc))


def _sunday_start(value: datetime) -> datetime:
    current = value.astimezone(timezone.utc)
    midnight = current.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - timedelta(days=(midnight.weekday() + 1) % 7)


def _num_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _zero_totals() -> dict[str, Any]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "reasoning_tokens": 0,
        "estimated_cost_usd": 0.0,
        "actual_cost_usd": 0.0,
        "sessions": 0,
        "api_calls": 0,
    }


def _accumulate_totals(acc: dict[str, Any], row: Mapping[str, Any]) -> dict[str, Any]:
    for col in _TOKEN_COLS:
        acc[col] += int(row.get(col) or 0)
    acc["estimated_cost_usd"] += float(row.get("estimated_cost_usd") or 0)
    acc["actual_cost_usd"] += float(row.get("actual_cost_usd") or 0)
    acc["sessions"] += int(row.get("sessions") or 0)
    acc["api_calls"] += int(row.get("api_calls") or 0)
    return acc


def _finish_totals(acc: dict[str, Any]) -> dict[str, Any]:
    acc = dict(acc)
    acc["total_tokens"] = int(acc["input_tokens"]) + int(acc["output_tokens"])
    return _apply_cost(acc)


def _zero_model(model: str, provider: str) -> dict[str, Any]:
    return {
        "model": model,
        "provider": provider,
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost_usd": 0.0,
        "actual_cost_usd": 0.0,
        "sessions": 0,
    }


def _accumulate_model(acc: dict[str, Any], row: Mapping[str, Any]) -> None:
    acc["input_tokens"] += int(row.get("input_tokens") or 0)
    acc["output_tokens"] += int(row.get("output_tokens") or 0)
    acc["estimated_cost_usd"] += float(row.get("estimated_cost_usd") or 0)
    acc["actual_cost_usd"] += float(row.get("actual_cost_usd") or 0)
    acc["sessions"] += int(row.get("sessions") or 0)


def _finish_model(acc: dict[str, Any]) -> dict[str, Any]:
    return _apply_cost(dict(acc))


def _zero_bucket(label: str) -> dict[str, Any]:
    return {
        "bucket": label,
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost_usd": 0.0,
        "actual_cost_usd": 0.0,
        "sessions": 0,
    }


def _accumulate_bucket(acc: dict[str, Any], row: Mapping[str, Any]) -> None:
    acc["input_tokens"] += int(row.get("input_tokens") or 0)
    acc["output_tokens"] += int(row.get("output_tokens") or 0)
    acc["estimated_cost_usd"] += float(row.get("estimated_cost_usd") or 0)
    acc["actual_cost_usd"] += float(row.get("actual_cost_usd") or 0)
    acc["sessions"] += int(row.get("sessions") or 0)


def _finish_bucket(acc: dict[str, Any]) -> dict[str, Any]:
    acc = dict(acc)
    acc["total_tokens"] = int(acc["input_tokens"]) + int(acc["output_tokens"])
    return _apply_cost(acc)


def _providers_from_models(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    providers: dict[str, dict[str, Any]] = {}
    for row in rows:
        provider = str(row.get("provider") or "unknown")
        _accumulate_model(
            providers.setdefault(provider, _zero_model("", provider)),
            row,
        )
    result = [_finish_model(row) for row in providers.values()]
    result.sort(key=lambda row: -(row["input_tokens"] + row["output_tokens"]))
    return result


def _attribution(
    live_totals: Mapping[str, Any],
    workspace_totals: Mapping[str, Any],
    *,
    durable: bool,
) -> dict[str, Any]:
    live_tokens = int(live_totals.get("total_tokens") or 0)
    workspace_tokens = int(workspace_totals.get("total_tokens") or 0)
    attributed = min(live_tokens, workspace_tokens) if durable else live_tokens
    unattributed = max(0, workspace_tokens - attributed)
    coverage = round(attributed / workspace_tokens * 100, 1) if workspace_tokens else 0.0
    return {
        "live_totals": dict(live_totals),
        "attributed_tokens": attributed,
        "unattributed_tokens": unattributed,
        "coverage_percent": coverage,
        "deleted_usage_included": durable,
    }


def _apply_cost(row: dict[str, Any]) -> dict[str, Any]:
    est = float(row.get("estimated_cost_usd") or 0)
    act = float(row.get("actual_cost_usd") or 0)
    row["estimated_cost_usd"] = round(est, 6)
    row["actual_cost_usd"] = round(act, 6)
    if act > 0:
        row["cost_usd"], row["cost_basis"] = round(act, 6), "actual"
    else:
        row["cost_usd"], row["cost_basis"] = round(est, 6), "estimated"
    return row


def _fold_bucket(label: str, bucket: str) -> str:
    """Normalize an integration bucket to the requested UTC granularity."""
    if not label:
        return label
    try:
        stamp = datetime.fromisoformat(label.replace("Z", "+00:00"))
    except ValueError:
        # Accept legacy labels while old cached/runtime data drains out.
        try:
            stamp = datetime.strptime(label, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return label
    return bucket_start_iso(stamp, bucket, timezone.utc)


def _dense_buckets(start_epoch: float, end_epoch: float, bucket: str) -> list[str]:
    zone = timezone.utc
    start_utc = datetime.fromtimestamp(start_epoch, tz=timezone.utc)
    end_utc = datetime.fromtimestamp(end_epoch, tz=timezone.utc)
    start = start_utc.astimezone(zone)
    end = end_utc.astimezone(zone)
    labels: list[str] = []
    if bucket == "hour":
        cur = start_utc.replace(minute=0, second=0, microsecond=0)
        while cur <= end_utc:
            labels.append(bucket_start_iso(cur, bucket, zone))
            cur += timedelta(hours=1)
    elif bucket == "week":
        cur = (start - timedelta(days=start.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        while cur <= end:
            labels.append(bucket_start_iso(cur, bucket, zone))
            cur += timedelta(weeks=1)
    elif bucket == "month":
        cur = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        while cur <= end:
            labels.append(bucket_start_iso(cur, bucket, zone))
            year, month = cur.year + (cur.month // 12), cur.month % 12 + 1
            cur = cur.replace(year=year, month=month)
    else:  # day
        cur = start.replace(hour=0, minute=0, second=0, microsecond=0)
        while cur <= end:
            labels.append(bucket_start_iso(cur, bucket, zone))
            cur += timedelta(days=1)
    # Guard against an unbounded hour range blowing up the response.
    return labels[:1000]
