"""Narrow Runtime -> Control accounting API; never use a Router master key."""

import os
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from .accounting_context import accounting_binding
from .router_accounting import AccountingUnavailable, weekly_budget_decision


class ControlAccountingClient:
    def __init__(self):
        # Reuse one connection pool. Creating an AsyncClient per usage read adds
        # DNS/TCP/TLS setup to every chat metadata and admission request.
        self._client = httpx.AsyncClient(timeout=8, follow_redirects=False)

    async def close(self):
        await self._client.aclose()

    async def weekly(self, agent_id, weekly_usd, context_id="personal"):
        binding = accounting_binding(agent_id, context_id)
        endpoint = os.environ.get("RUNTIME_CONTROL_URL", "").rstrip("/")
        if not endpoint:
            raise AccountingUnavailable("Control accounting endpoint unavailable")
        try:
            response = await self._client.get(
                endpoint + "/xnobrain/api/control/internal/v1/accounting/weekly",
                headers={
                    "Authorization": "Bearer " + binding["user_key"],
                    "X-GoRouter-Agent-Id": binding["agent_id"],
                },
            )
            if response.status_code != 200:
                raise AccountingUnavailable("Authoritative budget check unavailable")
            body = response.json()
            if body.get("success") is not True:
                raise AccountingUnavailable("Authoritative budget check unavailable")
            payload = body["data"]
            return weekly_budget_decision(
                payload,
                application="",
                environment="",
                workspace_id="",
                agent_id=binding["agent_id"],
                weekly_usd=weekly_usd,
                now=datetime.now(timezone.utc),
            )
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as error:
            raise AccountingUnavailable("Authoritative budget check unavailable") from error

    async def conversation(self, agent_id, conversation_id, context_id="personal"):
        binding = accounting_binding(agent_id, context_id)
        endpoint = os.environ.get("RUNTIME_CONTROL_URL", "").rstrip("/")
        if not endpoint:
            raise AccountingUnavailable("Control accounting endpoint unavailable")
        if not conversation_id:
            raise AccountingUnavailable("Conversation accounting scope unavailable")
        try:
            response = await self._client.get(
                endpoint
                + "/xnobrain/api/control/internal/v1/accounting/conversations/"
                + quote(str(conversation_id), safe=""),
                headers={
                    "Authorization": "Bearer " + binding["user_key"],
                    "X-GoRouter-Agent-Id": binding["agent_id"],
                },
            )
            if response.status_code != 200:
                raise AccountingUnavailable("Conversation accounting unavailable")
            body = response.json()
            if body.get("success") is not True or not isinstance(body.get("data"), dict):
                raise AccountingUnavailable("Conversation accounting unavailable")
            return body["data"]
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as error:
            raise AccountingUnavailable("Conversation accounting unavailable") from error

    async def weekly_agents(self, settings):
        """One Control call for all local agent limits and Router week totals."""
        if not settings:
            return {}
        agent_ids = list(settings)
        binding = accounting_binding(agent_ids[0])
        endpoint = os.environ.get("RUNTIME_CONTROL_URL", "").rstrip("/")
        if not endpoint or len(agent_ids) > 100:
            raise AccountingUnavailable("Weekly accounting unavailable")
        try:
            response = await self._client.get(
                endpoint + "/xnobrain/api/control/internal/v1/accounting/agents/weekly",
                headers={
                    "Authorization": "Bearer " + binding["user_key"],
                    "X-GoRouter-Agent-Id": ",".join(agent_ids),
                },
            )
            body = response.json()
            if response.status_code != 200 or body.get("success") is not True:
                raise AccountingUnavailable("Weekly accounting unavailable")
            result = {}
            for payload in body["data"]:
                agent_id = payload["agent_ids"][0]
                if agent_id not in settings or agent_id in result:
                    raise ValueError
                result[agent_id] = weekly_budget_decision(
                    payload,
                    application="",
                    environment="",
                    workspace_id="",
                    agent_id=agent_id,
                    weekly_usd=settings[agent_id]["weekly_usd"],
                )
            if set(result) != set(settings):
                raise ValueError
            return result
        except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError) as error:
            raise AccountingUnavailable("Weekly accounting unavailable") from error
