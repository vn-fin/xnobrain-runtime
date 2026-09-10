"""Strict consumer for the Router v0.2.0 workload accounting contract."""

import math
from datetime import datetime, timezone
from typing import Any

CAPABILITY = "gorouter-workload-usage-v1"


class AccountingUnavailable(RuntimeError):
    pass


def weekly_budget_decision(
    payload: dict[str, Any],
    *,
    application: str,
    environment: str,
    workspace_id: str,
    agent_id: str,
    weekly_usd: float,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    try:
        if any(
            payload.get(key) != expected
            for key, expected in {
                "capability_version": CAPABILITY,
                "application": application,
                "environment": environment,
                "workspace_id": workspace_id,
                "agent_ids": [agent_id],
                "timezone": "UTC",
                "accounting_state": "settled",
                "completeness": "durable_records",
                "freshness": "settled_only",
            }.items()
        ):
            raise ValueError
        start = datetime.fromisoformat(payload["period_start"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(payload["period_end"].replace("Z", "+00:00"))
        checked = datetime.fromisoformat(payload["as_of"].replace("Z", "+00:00"))
        if not start <= now < end or not -5 <= (now - checked).total_seconds() <= 30:
            raise ValueError
        if (end - start).total_seconds() != 7 * 86400:
            raise ValueError
        if payload.get("week_starts_on") != start.strftime("%A").lower():
            raise ValueError
        if isinstance(payload["summary"]["cost_usd"], bool):
            raise ValueError
        cost = float(payload["summary"]["cost_usd"])
        if not math.isfinite(cost) or cost < 0 or not math.isfinite(weekly_usd) or weekly_usd < 1:
            raise ValueError
        if payload.get("attribution_coverage") not in {"attributed", "no_usage"}:
            raise ValueError
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise AccountingUnavailable("Authoritative budget check unavailable") from error
    percent = cost / weekly_usd * 100
    return {
        "source": "gorouter",
        "capability_version": CAPABILITY,
        "cost_basis": "accounted",
        "enforcement": "soft_admission",
        "currency": "USD",
        "weekly_usd": weekly_usd,
        "spend_usd": cost,
        "remaining_usd": max(0, weekly_usd - cost),
        "percent_used": percent,
        "period_start": payload["period_start"],
        "period_end": payload["period_end"],
        "week_starts_on": payload["week_starts_on"],
        "as_of": payload["as_of"],
        "accounting_status": "settled",
        "accepting_chats": cost < weekly_usd,
        "status": "ok" if cost < weekly_usd else "exceeded",
        "severity": "red"
        if percent >= 90
        else "orange"
        if percent >= 80
        else "yellow"
        if percent >= 70
        else "normal",
    }
