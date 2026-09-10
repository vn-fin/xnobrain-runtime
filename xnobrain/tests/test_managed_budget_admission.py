"""Router-only budget decisions never use a local SUM or mutate bindings."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from xnobrain.integrations.router_accounting import AccountingUnavailable
from xnobrain.services.analytics import AnalyticsService
from xnobrain.services.base import ServiceError


class ManagedBudgetAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_unavailable_does_not_admit_and_settings_remain_editable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "agent"
            profile.mkdir()
            (profile / "config.yaml").write_text("model: {}\n")
            agents = SimpleNamespace(
                describe_agent=lambda *args, **kwargs: {},
                list_agents=lambda: {"agents": [{"name": "agent", "profile_path": str(profile)}]},
            )
            repo = SimpleNamespace(data_dir=root, profile_path=lambda agent: profile)
            service = AnalyticsService(agents, None, repo)
            service.accounting.weekly = AsyncMock(side_effect=AccountingUnavailable())
            with patch.dict(os.environ, {"RUNTIME_ACCOUNTING_MODE": "router"}):
                status = await service.get_budget("agent")
                self.assertIsNone(status["spend_usd"])
                self.assertFalse(status["accepting_chats"])
                with self.assertRaises(ServiceError) as raised:
                    await service.require_execution_budget("agent")
                self.assertEqual(raised.exception.code, "budget_check_unavailable")
                updated = await service.set_budget(
                    "agent", {"weekly_usd": 30, "revision": status["revision"]}
                )
                self.assertEqual(updated["weekly_usd"], 30)
                self.assertEqual((profile / "config.yaml").read_text(), "model: {}\n")
