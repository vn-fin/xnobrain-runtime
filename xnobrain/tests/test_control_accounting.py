"""Control facade consumer uses the canonical Router user key only; no direct Router access."""

import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from xnobrain.integrations.control_accounting import ControlAccountingClient
from xnobrain.integrations.router_accounting import AccountingUnavailable


class ControlAccountingTests(unittest.IsolatedAsyncioTestCase):
    async def test_authoritative_fixture_zero_and_wrong_scope(self):
        now = datetime.now(timezone.utc)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start -= timedelta(days=(start.weekday() + 1) % 7)
        fixture = json.loads(
            (Path(__file__).parent / "fixtures/gorouter_workload_weekly_v1.json").read_text()
        )
        fixture.update(
            period_start=start.isoformat(),
            period_end=(start + timedelta(days=7)).isoformat(),
            as_of=now.isoformat(),
        )
        fixture["summary"]["cost_usd"] = 0
        with patch.dict(
            os.environ,
            {
                "RUNTIME_CONTROL_URL": "https://control.invalid",
                "RUNTIME_LLM_API_KEY": "synthetic-user-key",
                "RUNTIME_LLM_API_KEY_FILE": "",
            },
        ):
            response = httpx.Response(200, json={"success": True, "data": fixture})
            with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=response)) as get:
                accounting = ControlAccountingClient()
                self.addAsyncCleanup(accounting.close)
                result = await accounting.weekly("local", 20)
                self.assertTrue(result["accepting_chats"])
                args, kwargs = get.call_args
                self.assertEqual(
                    args[0],
                    "https://control.invalid/xnobrain/api/control/internal/v1/accounting/weekly",
                )
                self.assertEqual(
                    kwargs["headers"],
                    {"Authorization": "Bearer synthetic-user-key", "X-GoRouter-Agent-Id": "local"},
                )
            fixture["agent_ids"] = ["foreign"]
            with (
                patch(
                    "httpx.AsyncClient.get",
                    new=AsyncMock(
                        return_value=httpx.Response(200, json={"success": True, "data": fixture})
                    ),
                ),
                self.assertRaises(AccountingUnavailable),
            ):
                await accounting.weekly("local", 20)

    async def test_conversation_usage_uses_user_key_and_encoded_id(self):
        response = httpx.Response(
            200, json={"success": True, "data": {"requests": 2, "cost_usd": 0.2}}
        )
        with (
            patch.dict(
                os.environ,
                {
                    "RUNTIME_CONTROL_URL": "https://control.invalid",
                    "RUNTIME_LLM_API_KEY": "synthetic-user-key",
                    "RUNTIME_LLM_API_KEY_FILE": "",
                },
            ),
            patch("httpx.AsyncClient.get", new=AsyncMock(return_value=response)) as get,
        ):
            accounting = ControlAccountingClient()
            self.addAsyncCleanup(accounting.close)
            result = await accounting.conversation("local", "ses/encoded")
        self.assertEqual(result["requests"], 2)
        args, kwargs = get.call_args
        self.assertTrue(args[0].endswith("/conversations/ses%2Fencoded"))
        self.assertEqual(
            kwargs["headers"],
            {"Authorization": "Bearer synthetic-user-key", "X-GoRouter-Agent-Id": "local"},
        )

    async def test_unconfigured_control_does_not_fall_back_to_local_spend(self):
        with (
            patch.dict(os.environ, {"RUNTIME_LLM_API_KEY": "", "RUNTIME_CONTROL_URL": ""}),
            self.assertRaises(AccountingUnavailable),
        ):
            accounting = ControlAccountingClient()
            self.addAsyncCleanup(accounting.close)
            await accounting.weekly("local", 20)
