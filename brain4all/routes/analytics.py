"""Analytics API route declarations."""

from ..models import AgentBudgetPatch
from .definition import route

ROUTES = (
    route("GET", "/analytics/agents", "analytics_agents", tags=("Analytics",)),
    route("GET", "/analytics/usage", "analytics_usage", tags=("Analytics",)),
    route("GET", "/analytics/overview", "analytics_overview", tags=("Analytics",)),
    route("GET", "/analytics/models", "analytics_models", tags=("Analytics",)),
    route("GET", "/analytics/timeseries", "analytics_timeseries", tags=("Analytics",)),
    route("GET", "/analytics/agents/{agent_id}/usage", "analytics_agent_usage", tags=("Analytics",)),
    route("GET", "/analytics/agents/{agent_id}/budget", "analytics_budget_get", tags=("Analytics",)),
    route("PUT", "/analytics/agents/{agent_id}/budget", "analytics_budget_set", AgentBudgetPatch, tags=("Analytics",)),
)
