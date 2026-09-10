"""Strict consumer gates for gorouter-workload-usage-v1 fixtures."""

import copy
import unittest
from datetime import datetime, timezone

from xnobrain.integrations.router_accounting import AccountingUnavailable, weekly_budget_decision

FIXTURE = {
    "capability_version": "gorouter-workload-usage-v1",
    "application": "xnobrain",
    "environment": "test",
    "workspace_id": "workspace_test",
    "agent_ids": ["agent_test"],
    "period_start": "2026-09-06T00:00:00Z",
    "period_end": "2026-09-13T00:00:00Z",
    "timezone": "UTC",
    "week_starts_on": "sunday",
    "as_of": "2026-09-10T00:00:00Z",
    "accounting_state": "settled",
    "completeness": "durable_records",
    "freshness": "settled_only",
    "attribution_coverage": "no_usage",
    "summary": {"requests": 0, "cost_usd": 0},
}


class RouterAccountingTests(unittest.TestCase):
    def decision(self, value, limit=20):
        return weekly_budget_decision(
            value,
            application="xnobrain",
            environment="test",
            workspace_id="workspace_test",
            agent_id="agent_test",
            weekly_usd=limit,
            now=datetime(2026, 9, 10, tzinfo=timezone.utc),
        )

    def test_authoritative_zero_and_soft_threshold(self):
        self.assertTrue(self.decision(FIXTURE)["accepting_chats"])
        for spend, allowed in [(19.99, True), (20, False), (22, False)]:
            value = copy.deepcopy(FIXTURE)
            value["summary"]["cost_usd"] = spend
            self.assertEqual(self.decision(value)["accepting_chats"], allowed)
        self.assertEqual(self.decision(FIXTURE)["enforcement"], "soft_admission")

    def test_wrong_scope_stale_missing_and_incomplete_rejected(self):
        for key, value in [
            ("capability_version", "old"),
            ("workspace_id", "foreign"),
            ("agent_ids", ["foreign"]),
            ("completeness", "pending"),
            ("as_of", "2026-09-01T00:00:00Z"),
            ("summary", {}),
            ("period_end", "2026-09-09T00:00:00Z"),
        ]:
            with self.subTest(key=key), self.assertRaises(AccountingUnavailable):
                self.decision({**FIXTURE, key: value})

    def test_router_week_bounds_not_runtime_rolling_week(self):
        result = self.decision(FIXTURE)
        self.assertEqual(result["period_start"], FIXTURE["period_start"])
        self.assertEqual(result["period_end"], FIXTURE["period_end"])

    def test_nonfinite_and_missing_cost_are_not_free(self):
        for value in [None, float("nan"), float("inf"), -1]:
            with self.subTest(value=value), self.assertRaises(AccountingUnavailable):
                self.decision({**FIXTURE, "summary": {"cost_usd": value}})
