"""Slow delegated Control traffic must not stall the native dispatcher."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from xnobrain.services.kanban_delegated_tasks import KanbanDelegatedTasks


class KanbanDelegatedWorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.platform = SimpleNamespace(cron=SimpleNamespace(reconcile_deliveries=Mock()))
        self.client = SimpleNamespace(close=AsyncMock())
        self.service = KanbanDelegatedTasks(self.platform, self.client)

    async def asyncTearDown(self):
        await self.service.close()

    async def test_stalled_control_does_not_block_ticks_or_spawn_parallel_work(self):
        entered, release, second = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = 0

        async def control_wait():
            nonlocal calls
            calls += 1
            entered.set()
            if calls == 2:
                second.set()
            await release.wait()

        self.service.reconcile = control_wait
        await asyncio.wait_for(self.service.tick(), 0.25)
        await asyncio.wait_for(entered.wait(), 2)
        worker = self.service.worker
        native_ticks = []
        for index in range(50):
            await asyncio.wait_for(self.service.tick(), 0.25)
            native_ticks.append(index)
        self.assertEqual(len(native_ticks), 50)
        self.assertIs(self.service.worker, worker)
        self.assertEqual(calls, 1)
        release.set()
        await asyncio.wait_for(second.wait(), 2)
        # The fifty notifications coalesce to one follow-up reconciliation.
        self.assertEqual(calls, 2)

    async def test_close_cancels_and_awaits_worker_before_closing_transport(self):
        entered, stopped = asyncio.Event(), asyncio.Event()

        async def control_wait():
            entered.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()

        async def close_transport():
            self.assertTrue(stopped.is_set())

        self.client.close.side_effect = close_transport
        self.service.reconcile = control_wait
        await self.service.tick()
        await asyncio.wait_for(entered.wait(), 2)
        worker = self.service.worker
        await asyncio.wait_for(self.service.close(), 1)
        self.assertTrue(worker.cancelled())
        self.client.close.assert_awaited_once()
        self.assertIsNone(self.service.worker)
        await self.service.tick()
        self.assertIsNone(self.service.worker)

    async def test_failed_reconciliation_does_not_kill_worker_or_log_user_content(self):
        retried = asyncio.Event()
        attempts = 0

        async def reconcile():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ValueError("private task content")
            retried.set()

        self.service.reconcile = reconcile
        with self.assertLogs("xnobrain.services.kanban_delegated_tasks", level="WARNING") as logs:
            await self.service.tick()
            for _ in range(200):
                if attempts:
                    break
                await asyncio.sleep(0.005)
        self.assertEqual(attempts, 1)
        self.assertNotIn("private task content", " ".join(logs.output))
        await self.service.tick()
        await asyncio.wait_for(retried.wait(), 2)
        self.assertFalse(self.service.worker.done())
