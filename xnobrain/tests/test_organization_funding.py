"""Organization conversations run only with the funding Control registered."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from xnobrain.integrations import organization_funding
from xnobrain.integrations.accounting_context import accounting_binding
from xnobrain.integrations.router_accounting import AccountingUnavailable
from xnobrain.services.analytics import AnalyticsService
from xnobrain.services.base import ServiceError

ORG = "org_aaaaaaaa11111111"


class OrganizationFundingTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        for conversation in ("conv-org", "conv-owner"):
            organization_funding.release(conversation)

    def test_registry_requires_an_organization_model_for_organization_funding(self):
        with self.assertRaises(ValueError):
            organization_funding.register("conv-org", organization_funding.ORGANIZATION, "gpt-5")
        with self.assertRaises(ValueError):
            organization_funding.register("conv-org", "sponsor")
        organization_funding.register(
            "conv-org", organization_funding.ORGANIZATION, "org/acme/fast"
        )
        self.assertEqual(organization_funding.funding_for("conv-org")["model"], "org/acme/fast")
        organization_funding.release("conv-org")
        self.assertIsNone(organization_funding.funding_for("conv-org"))

    def test_binding_refuses_unfunded_organization_conversations(self):
        with patch.dict(os.environ, {"RUNTIME_LLM_API_KEY": "member-key"}, clear=True):
            with self.assertRaises(AccountingUnavailable):
                accounting_binding("agent", ORG, "conv-org")
            organization_funding.register(
                "conv-org", organization_funding.ORGANIZATION, "org/acme/fast"
            )
            binding = accounting_binding("agent", ORG, "conv-org")
        self.assertEqual(binding["user_key"], "member-key")
        self.assertEqual(binding["context_id"], ORG)

    def test_owned_workspace_uses_its_key_without_a_delegation(self):
        environment = {
            "RUNTIME_WORKSPACE_ID": "workspace-org",
            "RUNTIME_WORKSPACE_OWNER_KIND": "organization",
            "RUNTIME_WORKSPACE_ORGANIZATION_ID": ORG,
            "RUNTIME_LLM_API_KEY": "organization-key",
        }
        with patch.dict(os.environ, environment, clear=True):
            binding = accounting_binding("agent", ORG, "direct-chat")
            self.assertEqual(binding["user_key"], "organization-key")
            with self.assertRaises(AccountingUnavailable):
                accounting_binding("agent", "org_other", "direct-chat")
            for field in (
                "RUNTIME_WORKSPACE_ID",
                "RUNTIME_WORKSPACE_OWNER_KIND",
                "RUNTIME_WORKSPACE_ORGANIZATION_ID",
            ):
                with self.subTest(missing=field), patch.dict(os.environ, {field: ""}):
                    with self.assertRaises(AccountingUnavailable):
                        accounting_binding("agent", ORG, "direct-chat")
            with patch.dict(os.environ, {"RUNTIME_WORKSPACE_OWNER_KIND": "user"}):
                with self.assertRaises(AccountingUnavailable):
                    accounting_binding("agent", ORG, "direct-chat")

    async def test_owned_workspace_keeps_authoritative_agent_budget(self):
        service = AnalyticsService(SimpleNamespace(), None, SimpleNamespace())
        service.get_budget = AsyncMock(return_value={"status": "ok", "accepting_chats": True})
        environment = {
            "RUNTIME_ACCOUNTING_MODE": "router",
            "RUNTIME_WORKSPACE_ID": "workspace-org",
            "RUNTIME_WORKSPACE_OWNER_KIND": "organization",
            "RUNTIME_WORKSPACE_ORGANIZATION_ID": ORG,
        }
        with patch.dict(os.environ, environment, clear=True):
            status = await service.require_execution_budget("agent", ORG, "direct-chat")
            self.assertTrue(status["accepting_chats"])
            service.get_budget.assert_awaited_once_with("agent")
            for decision, code in (
                ({"status": "exceeded", "accepting_chats": False}, "weekly_budget_exceeded"),
                ({"status": "unavailable", "accepting_chats": False}, "budget_check_unavailable"),
            ):
                service.get_budget.return_value = decision
                with self.assertRaises(ServiceError) as raised:
                    await service.require_execution_budget("agent", ORG, "direct-chat")
                self.assertEqual(raised.exception.code, code)
            service.get_budget.reset_mock()
            with self.assertRaises(ServiceError):
                await service.require_execution_budget("agent", "org_other", "direct-chat")
            service.get_budget.assert_not_awaited()

    async def test_budget_follows_funding_mode(self):
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
                with self.assertRaises(ServiceError) as raised:
                    await service.require_execution_budget("agent", ORG, "conv-org")
                self.assertEqual(raised.exception.code, "budget_check_unavailable")

                organization_funding.register(
                    "conv-org", organization_funding.ORGANIZATION, "org/acme/fast"
                )
                status = await service.require_execution_budget("agent", ORG, "conv-org")
                self.assertEqual(status["status"], "organization_funded")
                service.accounting.weekly.assert_not_awaited()

                # Owner funding is the owner's own money under the agent budget.
                organization_funding.register("conv-owner", organization_funding.OWNER)
                with self.assertRaises(ServiceError):
                    await service.require_execution_budget("agent", ORG, "conv-owner")
                service.accounting.weekly.assert_awaited()
