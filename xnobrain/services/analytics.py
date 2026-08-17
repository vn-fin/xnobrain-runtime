"""Usage analytics and advisory budgets, computed on read.

The unfiltered workspace view uses OmniRoute's durable usage ledger, so deleting a
conversation or agent cannot erase historical totals. Current profile
``state.db`` files remain the live attribution source because OmniRoute has no
agent identifier. Agent-filtered views therefore intentionally use live profile
data. Budget config is the only mutation and is written to the agent's
``config.yaml`` with a snapshot first.
"""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import time
from typing import Any, Mapping

import yaml

from ..integrations.analytics import (
    aggregate_profile,
    aggregate_router_usage,
    bucket_start_iso,
    period_spend,
    resolve_timezone,
)
from .base import ServiceError


_TOKEN_COLS = (
    "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_write_tokens", "reasoning_tokens",
)
_BUDGET_KEY = "xnobrain_budget"
_MERGED_TTL = 20.0
_CACHE_CAP = 512


class AnalyticsService:
    """Compute-on-read usage analytics across the named agent profiles."""

    def __init__(self, agents: Any, router: Any, repository: Any):
        self.agents = agents
        self.router = router
        self.repository = repository
        self._partials: dict[tuple, tuple[int, dict[str, Any]]] = {}
        self._merged: dict[tuple, tuple[float, dict[str, Any]]] = {}
        self._workspace: dict[tuple, tuple[float, dict[str, Any]]] = {}
        self._workspace_lock = asyncio.Lock()
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
        self, *, agent_ids: list[str], start_epoch: float, end_epoch: float,
        bucket: str, timezone_name: str = "UTC",
    ) -> dict[str, Any]:
        items, available = self._resolve_items(agent_ids)
        summary = await self._workspace_summary(
            items, selected=bool(agent_ids),
            start=start_epoch, end=end_epoch, bucket=bucket, timezone_name=timezone_name,
        )
        return await self._decorate_overview(summary, items, agent_ids, available)

    async def usage_overview(
        self, *, agent_ids: list[str], start_epoch: float, end_epoch: float,
        bucket: str, timezone_name: str = "UTC",
    ) -> dict[str, Any]:
        """Return dashboard totals and attribution without chart payloads."""
        items, available = self._resolve_items(agent_ids)
        summary = await self._workspace_summary(
            items, selected=bool(agent_ids),
            start=start_epoch, end=end_epoch, bucket=bucket, timezone_name=timezone_name,
        )
        complete = await self._decorate_overview(
            summary, items, agent_ids, available,
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
        for row in summary["agents"]:
            item = by_path.get(row["agent_id"])
            row["budget"] = self._budget_status(row["agent_id"], item) if item else None
        summary["agents_selected"] = (
            [str(item.get("name") or "") for item in items] if agent_ids else []
        )
        summary["agents_available"] = agents_available
        summary["quota"] = await self._quota_overlay()
        return summary

    async def agent_usage(
        self, agent_id: str, *, start_epoch: float, end_epoch: float,
        bucket: str, timezone_name: str = "UTC",
    ) -> dict[str, Any]:
        item = self._require_item(agent_id)
        summary = await self._summary([item], start_epoch, end_epoch, bucket, timezone_name)
        agent_row = summary["agents"][0] if summary["agents"] else {
            "agent_id": agent_id, "display_name": self._display(item, agent_id),
            "totals": summary["totals"],
        }
        agent_row["budget"] = self._budget_status(agent_id, item)
        agent_row["by_model"] = summary["by_model"]
        agent_row["series"] = summary["series"]
        agent_row["range_from"] = _epoch_iso(start_epoch)
        agent_row["range_to"] = _epoch_iso(end_epoch)
        agent_row["bucket"] = bucket
        agent_row["timezone"] = timezone_name
        return agent_row

    async def models_breakdown(
        self, *, agent_ids: list[str], start_epoch: float, end_epoch: float,
        bucket: str, timezone_name: str = "UTC",
    ) -> dict[str, Any]:
        items, _ = self._resolve_items(agent_ids)
        summary = await self._workspace_summary(
            items, selected=bool(agent_ids),
            start=start_epoch, end=end_epoch, bucket=bucket, timezone_name=timezone_name,
        )
        return {
            "range_from": _epoch_iso(start_epoch), "range_to": _epoch_iso(end_epoch),
            "by_model": summary["by_model"], "totals": summary["totals"],
            "by_provider": summary["by_provider"], "source": summary["source"],
            "timezone": timezone_name,
        }

    async def timeseries(
        self, *, agent_ids: list[str], start_epoch: float, end_epoch: float,
        bucket: str, timezone_name: str = "UTC",
    ) -> dict[str, Any]:
        items, _ = self._resolve_items(agent_ids)
        summary = await self._workspace_summary(
            items, selected=bool(agent_ids),
            start=start_epoch, end=end_epoch, bucket=bucket, timezone_name=timezone_name,
        )
        return {
            "range_from": _epoch_iso(start_epoch), "range_to": _epoch_iso(end_epoch),
            "bucket": bucket, "series": summary["series"], "source": summary["source"],
            "timezone": timezone_name,
        }

    def get_budget(self, agent_id: str) -> dict[str, Any]:
        item = self._require_item(agent_id)
        return self._budget_status(agent_id, item)

    def set_budget(self, agent_id: str, patch: Mapping[str, Any]) -> dict[str, Any]:
        item = self._require_item(agent_id)
        config, path = self._read_config(agent_id)
        monthly = patch.get("monthly_usd")
        daily = patch.get("daily_usd")
        new_config = dict(config)
        if monthly is None and daily is None:
            new_config.pop(_BUDGET_KEY, None)
        else:
            new_config[_BUDGET_KEY] = {
                "monthly_usd": _num_or_none(monthly),
                "daily_usd": _num_or_none(daily),
                "warn_threshold_percent": int(patch.get("warn_threshold_percent", 80)),
                "cost_basis": (
                    "actual" if str(patch.get("cost_basis")) == "actual" else "estimated"
                ),
                "currency": str(patch.get("currency") or "USD"),
            }
        if path.is_file():
            self.repository.snapshot(agent_id, "config", "config", path.read_bytes())
        self.repository.atomic_yaml(path, new_config)
        self._merged.clear()
        return self._budget_status(agent_id, item)

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
        self, items: list[Mapping[str, Any]], start: float, end: float, bucket: str,
        timezone_name: str,
    ) -> dict[str, Any]:
        key = (
            frozenset(str(item.get("name") or "") for item in items),
            round(start), round(end), bucket, timezone_name,
        )
        now = time.time()
        cached = self._merged.get(key)
        if cached and now - cached[0] < _MERGED_TTL:
            return cached[1]
        effective = "day" if bucket == "week" else bucket
        partials = await self._collect_partials(items, start, end, effective, timezone_name)
        summary = self._merge(partials, start, end, bucket, timezone_name)
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
        timezone_name: str,
    ) -> dict[str, Any]:
        """Single-flight computation shared by parallel dashboard endpoints."""
        key = (
            frozenset(str(item.get("name") or "") for item in items),
            selected, round(start), round(end), bucket, timezone_name,
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
            live = await self._summary(items, start, end, bucket, timezone_name)
            summary = await self._with_durable_workspace(
                live, selected=selected, start=start, end=end, bucket=bucket,
                timezone_name=timezone_name,
            )
            if len(self._workspace) > _CACHE_CAP:
                self._workspace.clear()
            self._workspace[key] = (now, summary)
            return copy.deepcopy(summary)

    async def _collect_partials(
        self, items: list[Mapping[str, Any]], start: float, end: float, effective: str,
        timezone_name: str,
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
                ckey = (agent_id, round(start), round(end), effective, timezone_name)
                hit = self._partials.get(ckey)
                if hit and mtime is not None and hit[0] == mtime:
                    partial = hit[1]
                else:
                    async with self._sem:
                        partial = await asyncio.to_thread(
                            aggregate_profile, profile_dir,
                            start_epoch=start, end_epoch=end, bucket=effective,
                            timezone_name=timezone_name,
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
        timezone_name: str,
    ) -> dict[str, Any]:
        """Overlay durable OmniRoute totals for the unfiltered workspace view."""
        if selected:
            return {
                **live,
                "source": {
                    "kind": "live_profiles",
                    "durable": False,
                    "label": "Current agent profiles",
                    "message": (
                        "Agent filters use live profile attribution. Usage from deleted "
                        "conversations or agents cannot be assigned to this selection."
                    ),
                },
                "by_provider": _providers_from_models(live["by_model"]),
                "request_status": {
                    "total": int(live["totals"]["api_calls"]),
                    "successful": int(live["totals"]["api_calls"]),
                    "failed": 0,
                    "success_rate": 100.0 if live["totals"]["api_calls"] else 0.0,
                },
                "attribution": _attribution(live["totals"], live["totals"], durable=False),
            }

        data_dir = getattr(self.router, "data_dir", None)
        if data_dir is None:
            return {
                **live,
                "source": {
                    "kind": "live_profiles",
                    "durable": False,
                    "label": "Current agent profiles",
                    "message": "Provider usage history is unavailable; totals use live profiles.",
                },
                "by_provider": _providers_from_models(live["by_model"]),
                "request_status": {
                    "total": int(live["totals"]["api_calls"]),
                    "successful": int(live["totals"]["api_calls"]),
                    "failed": 0,
                    "success_rate": 100.0 if live["totals"]["api_calls"] else 0.0,
                },
                "attribution": _attribution(live["totals"], live["totals"], durable=False),
            }

        effective = "day" if bucket == "week" else bucket
        async with self._sem:
            router_partial = await asyncio.to_thread(
                aggregate_router_usage,
                Path(data_dir),
                start_epoch=start,
                end_epoch=end,
                bucket=effective,
                timezone_name=timezone_name,
            )
        if not router_partial.get("available"):
            return {
                **live,
                "source": {
                    "kind": "live_profiles",
                    "durable": False,
                    "label": "Current agent profiles",
                    "message": "Provider usage history is unavailable; totals use live profiles.",
                },
                "by_provider": _providers_from_models(live["by_model"]),
                "request_status": {
                    "total": int(live["totals"]["api_calls"]),
                    "successful": int(live["totals"]["api_calls"]),
                    "failed": 0,
                    "success_rate": 100.0 if live["totals"]["api_calls"] else 0.0,
                },
                "attribution": _attribution(live["totals"], live["totals"], durable=False),
            }

        durable = self._merge(
            [{
                "agent_id": "__provider_runtime__",
                "display_name": "Provider history",
                "partial": router_partial,
            }],
            start,
            end,
            bucket,
            timezone_name,
        )
        durable["agents"] = live["agents"]
        # Sessions only exist in XNOBrain's live profile records. Requests,
        # tokens, cost, model/provider rows and the time series are durable.
        durable["totals"]["sessions"] = live["totals"]["sessions"]
        durable["source"] = {
            "kind": "provider_runtime",
            "durable": True,
            "label": "Provider usage history",
            "message": (
                "Workspace totals include historical usage after conversations or "
                "agents are deleted. Agent attribution includes current profiles only."
            ),
        }
        durable["by_provider"] = [
            _finish_model(dict(row)) for row in router_partial.get("by_provider") or []
        ]
        durable["request_status"] = router_partial["request_status"]
        durable["attribution"] = _attribution(
            live["totals"], durable["totals"], durable=True,
        )
        return durable

    def _merge(
        self, partials: list[dict[str, Any]], start: float, end: float, bucket: str,
        timezone_name: str,
    ) -> dict[str, Any]:
        totals = _zero_totals()
        models: dict[tuple[str, str], dict[str, Any]] = {}
        series: dict[str, dict[str, Any]] = {}
        agents: list[dict[str, Any]] = []
        for entry in partials:
            partial = entry["partial"] or {}
            agent_totals = _accumulate_totals(_zero_totals(), partial.get("totals") or {})
            _accumulate_totals(totals, partial.get("totals") or {})
            agents.append({
                "agent_id": entry["agent_id"],
                "display_name": entry["display_name"],
                "totals": _finish_totals(agent_totals),
            })
            for row in partial.get("by_model") or []:
                mk = (str(row.get("model") or "unknown"), str(row.get("provider") or ""))
                _accumulate_model(models.setdefault(mk, _zero_model(*mk)), row)
            for row in partial.get("series") or []:
                label = _fold_bucket(str(row.get("bucket") or ""), bucket, timezone_name)
                _accumulate_bucket(series.setdefault(label, _zero_bucket(label)), row)
        return {
            "range_from": _epoch_iso(start), "range_to": _epoch_iso(end),
            "period_days": max(1, round((end - start) / 86400)),
            "bucket": bucket, "generated_at": _iso(), "timezone": timezone_name,
            "totals": _finish_totals(totals),
            "agents": agents,
            "by_model": [
                _finish_model(models[key])
                for key in sorted(models, key=lambda k: -(
                    models[k]["input_tokens"] + models[k]["output_tokens"]))
            ],
            "series": [
                _finish_bucket(series.get(label, _zero_bucket(label)))
                for label in _dense_buckets(start, end, bucket, timezone_name)
            ],
        }

    def _budget_status(self, agent_id: str, item: Mapping[str, Any] | None) -> dict[str, Any]:
        config, _ = self._read_config(agent_id)
        budget = config.get(_BUDGET_KEY) if isinstance(config.get(_BUDGET_KEY), dict) else {}
        monthly = _num_or_none(budget.get("monthly_usd"))
        daily = _num_or_none(budget.get("daily_usd"))
        basis = "actual" if str(budget.get("cost_basis")) == "actual" else "estimated"
        warn = int(budget.get("warn_threshold_percent") or 80)
        currency = str(budget.get("currency") or "USD")
        base = {
            "monthly_usd": monthly, "daily_usd": daily,
            "warn_threshold_percent": warn, "cost_basis": basis,
            "currency": currency, "advisory": True,
        }
        if monthly is None and daily is None:
            return {**base, "period_start": None, "spend_usd": 0.0,
                    "daily_spend_usd": 0.0, "percent_used": 0, "status": "unset"}
        profile_dir = self._profile_dir(item or {})
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        spend = (
            period_spend(profile_dir, since_epoch=month_start.timestamp(), cost_basis=basis)
            if profile_dir is not None else 0.0
        )
        daily_spend = (
            period_spend(profile_dir, since_epoch=day_start.timestamp(), cost_basis=basis)
            if profile_dir is not None else 0.0
        )
        percent = 0
        exceeded = False
        if monthly:
            percent = round(spend / monthly * 100)
            exceeded = spend >= monthly
        if daily and daily_spend >= daily:
            exceeded = True
        status = "exceeded" if exceeded else ("warning" if percent >= warn else "ok")
        return {
            **base, "period_start": _iso(month_start),
            "spend_usd": round(spend, 6), "daily_spend_usd": round(daily_spend, 6),
            "percent_used": max(0, min(999, percent)), "status": status,
        }

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
        # Best-effort: a router outage (or any error) must degrade to
        # "unavailable" rather than fail the whole analytics response.
        try:
            result = await self.router.usage("auto")
            return result if isinstance(result, dict) else {"available": False, "quotas": []}
        except Exception:  # noqa: BLE001 - overlay is advisory, never load-bearing
            return {"available": False, "provider": "", "model": "auto",
                    "plan": "", "message": "", "quotas": []}


# ---- module helpers ---------------------------------------------------------

def _iso(value: datetime | None = None) -> str:
    return (value or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")


def _epoch_iso(value: float) -> str:
    return _iso(datetime.fromtimestamp(value, tz=timezone.utc))


def _num_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _zero_totals() -> dict[str, Any]:
    return {
        "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
        "cache_write_tokens": 0, "reasoning_tokens": 0, "estimated_cost_usd": 0.0,
        "actual_cost_usd": 0.0, "sessions": 0, "api_calls": 0,
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
    return {"model": model, "provider": provider, "input_tokens": 0,
            "output_tokens": 0, "estimated_cost_usd": 0.0, "actual_cost_usd": 0.0,
            "sessions": 0}


def _accumulate_model(acc: dict[str, Any], row: Mapping[str, Any]) -> None:
    acc["input_tokens"] += int(row.get("input_tokens") or 0)
    acc["output_tokens"] += int(row.get("output_tokens") or 0)
    acc["estimated_cost_usd"] += float(row.get("estimated_cost_usd") or 0)
    acc["actual_cost_usd"] += float(row.get("actual_cost_usd") or 0)
    acc["sessions"] += int(row.get("sessions") or 0)


def _finish_model(acc: dict[str, Any]) -> dict[str, Any]:
    return _apply_cost(dict(acc))


def _zero_bucket(label: str) -> dict[str, Any]:
    return {"bucket": label, "input_tokens": 0, "output_tokens": 0,
            "estimated_cost_usd": 0.0, "actual_cost_usd": 0.0, "sessions": 0}


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


def _fold_bucket(label: str, bucket: str, timezone_name: str) -> str:
    """Normalize an integration bucket to the requested local granularity."""
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
    return bucket_start_iso(stamp, bucket, resolve_timezone(timezone_name))


def _dense_buckets(
    start_epoch: float, end_epoch: float, bucket: str, timezone_name: str,
) -> list[str]:
    zone = resolve_timezone(timezone_name)
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
            hour=0, minute=0, second=0, microsecond=0)
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
