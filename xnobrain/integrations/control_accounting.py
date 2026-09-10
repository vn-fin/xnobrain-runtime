"""Narrow Runtime -> Control accounting API; never use a Router master key."""

import os
from datetime import datetime, timezone

import httpx

from .accounting_context import accounting_binding
from .router_accounting import AccountingUnavailable, weekly_budget_decision


class ControlAccountingClient:
    async def weekly(self, agent_id, weekly_usd, context_id="personal"):
        binding = accounting_binding(agent_id, context_id)
        endpoint = os.environ.get("RUNTIME_CONTROL_URL", "").rstrip("/")
        if not endpoint:
            raise AccountingUnavailable("Control accounting endpoint unavailable")
        try:
            async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
                response = await client.get(
                    endpoint + "/xnobrain/api/control/internal/v1/accounting/weekly",
                    headers={"Authorization": "Bearer " + binding["workload_key"]},
                )
                if response.status_code != 200:
                    raise AccountingUnavailable("Authoritative budget check unavailable")
                body = response.json()
                if body.get("success") is not True:
                    raise AccountingUnavailable("Authoritative budget check unavailable")
                payload = body["data"]
                cutover = datetime.fromisoformat(binding["cutover_at"].replace("Z", "+00:00"))
                period = datetime.fromisoformat(payload["period_start"].replace("Z", "+00:00"))
                if cutover > period:
                    raise AccountingUnavailable("Accounting cutover is not active for this week")
                return weekly_budget_decision(
                    payload, application=binding["application"],
                    environment=binding.get("environment", ""), workspace_id=binding["workspace_id"],
                    agent_id=binding["agent_id"], weekly_usd=weekly_usd,
                    now=datetime.now(timezone.utc),
                )
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as error:
            raise AccountingUnavailable("Authoritative budget check unavailable") from error
