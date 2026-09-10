"""Concurrent attribution scopes must not exchange workload credentials."""

import asyncio
import unittest
from unittest.mock import patch

from xnobrain.integrations.accounting_context import current_accounting, inference_accounting
from xnobrain.integrations.conversation_credentials import workspace_router_key
from xnobrain.integrations.router_accounting import AccountingUnavailable


class AccountingContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_concurrent_agents_and_nested_children_are_context_local(self):
        async def invoke(key, conversation, run):
            with inference_accounting({"workload_key": key}, conversation, run):
                await asyncio.sleep(0)
                self.assertEqual(workspace_router_key(), key)
                parent = current_accounting()
                with inference_accounting(parent["binding"], conversation, run + "_child", run):
                    await asyncio.sleep(0)
                    self.assertEqual(
                        current_accounting()["headers"]["X-GoRouter-Parent-Run-Id"], run
                    )
                    self.assertNotIn("X-GoRouter-Request-Id", current_accounting()["headers"])
                self.assertEqual(current_accounting()["headers"]["X-GoRouter-Run-Id"], run)
            self.assertIsNone(current_accounting())

        with patch.dict("os.environ", {"RUNTIME_LLM_API_KEY": "not-used"}):
            await asyncio.gather(
                invoke("a", "conversation_a", "run_a"), invoke("b", "conversation_b", "run_b")
            )

    async def test_invalid_correlation_is_not_forwarded(self):
        with (
            self.assertRaises(AccountingUnavailable),
            inference_accounting({"workload_key": "synthetic"}, "bad\nheader"),
        ):
            pass

    async def test_native_request_adapter_adds_correlation_without_reusing_request_id(self):
        from types import SimpleNamespace

        from xnobrain.integrations.conversation_runner import ConversationRunnerMixin

        agent = SimpleNamespace(_build_api_kwargs=lambda messages: {"messages": messages})
        ConversationRunnerMixin._install_provider_runtime_request_guard(agent)
        with inference_accounting({"workload_key": "synthetic"}, "conversation", "run"):
            kwargs = agent._build_api_kwargs([])
        self.assertEqual(kwargs["extra_headers"]["X-GoRouter-Run-Id"], "run")
        self.assertNotIn("X-GoRouter-Request-Id", kwargs["extra_headers"])
