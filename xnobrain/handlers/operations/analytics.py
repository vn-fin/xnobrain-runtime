"""Feature-owned operation handlers."""

import time
from typing import Any, Callable

from ..query import bucket, csv, time_range, user_timezone

Operation = tuple[Callable[[], Any], str, int]


def operations(handler: Any, request: Any, body: dict[str, Any]) -> dict[str, Operation]:
    p, q, s = request.path_params, request.query_params, handler.service
    agent = lambda: str(q.get("agent") or "").strip() or (_ for _ in ()).throw(ValueError("agent is required"))
    return {
        "analytics_agents": (s.analytics.list_selectable_agents, "analytics agents retrieved successfully", 200),
        "analytics_usage": (lambda: s.analytics.usage_summary(agent_ids=csv(q.get("agents")), **time_range(q), bucket=bucket(q), timezone_name=user_timezone(q)), "usage analytics retrieved successfully", 200),
        "analytics_overview": (lambda: s.analytics.usage_overview(agent_ids=csv(q.get("agents")), **time_range(q), bucket=bucket(q), timezone_name=user_timezone(q)), "usage overview retrieved successfully", 200),
        "analytics_agent_usage": (lambda: s.analytics.agent_usage(p["agent_id"], **time_range(q), bucket=bucket(q), timezone_name=user_timezone(q)), "agent usage retrieved successfully", 200),
        "analytics_models": (lambda: s.analytics.models_breakdown(agent_ids=csv(q.get("agents")), **time_range(q), bucket=bucket(q), timezone_name=user_timezone(q)), "model usage retrieved successfully", 200),
        "analytics_timeseries": (lambda: s.analytics.timeseries(agent_ids=csv(q.get("agents")), **time_range(q), bucket=bucket(q), timezone_name=user_timezone(q)), "usage timeseries retrieved successfully", 200),
        "analytics_budget_get": (lambda: s.analytics.get_budget(p["agent_id"]), "budget retrieved successfully", 200),
        "analytics_budget_set": (lambda: s.analytics.set_budget(p["agent_id"], body), "budget updated successfully", 200),
    }
