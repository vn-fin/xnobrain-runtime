"""Control facade consumer uses workload key only; no direct Router access."""

import json
import os
import tempfile
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
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bindings.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "bindings": [
                            {
                                "local_agent_id": "local",
                                "context_id": "personal",
                                "application": "xnobrain",
                                "workspace_id": "workspace_test",
                                "agent_id": "agent_test",
                                "environment": "test",
                                "workload_key": "synthetic-workload",
                                "capability_version": "gorouter-workload-usage-v1",
                                "cutover_at": start.isoformat(),
                            }
                        ],
                    }
                )
            )
            path.chmod(0o600)
            with patch.dict(
                os.environ,
                {
                    "RUNTIME_ACCOUNTING_BINDINGS_FILE": str(path),
                    "RUNTIME_CONTROL_URL": "https://control.invalid",
                },
            ):
                response = httpx.Response(200, json={"success": True, "data": fixture})
                with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=response)) as get:
                    result = await ControlAccountingClient().weekly("local", 20)
                    self.assertTrue(result["accepting_chats"])
                    args, kwargs = get.call_args
                    self.assertEqual(
                        args[0],
                        "https://control.invalid/xnobrain/api/control/internal/v1/accounting/weekly",
                    )
                    self.assertEqual(
                        kwargs["headers"], {"Authorization": "Bearer synthetic-workload"}
                    )
                fixture["agent_ids"] = ["foreign"]
                with (
                    patch(
                        "httpx.AsyncClient.get",
                        new=AsyncMock(
                            return_value=httpx.Response(
                                200, json={"success": True, "data": fixture}
                            )
                        ),
                    ),
                    self.assertRaises(AccountingUnavailable),
                ):
                    await ControlAccountingClient().weekly("local", 20)

    async def test_unconfigured_control_does_not_fall_back_to_local_spend(self):
        with (
            patch.dict(
                os.environ, {"RUNTIME_ACCOUNTING_BINDINGS_FILE": "", "RUNTIME_CONTROL_URL": ""}
            ),
            self.assertRaises(AccountingUnavailable),
        ):
            await ControlAccountingClient().weekly("local", 20)
