"""Tests for reusable service-level in-memory caching."""

from __future__ import annotations

import asyncio
import unittest

from brain4all.services.helpers import MemoryCache, cached_method


class ExampleService:
    def __init__(self) -> None:
        self._cache = MemoryCache()
        self.sync_calls = 0
        self.async_calls = 0
        self.ready = True

    @cached_method("sync_rows")
    def sync_rows(self) -> list[dict[str, int]]:
        self.sync_calls += 1
        return [{"value": self.sync_calls}]

    @cached_method("async_rows", cache_when=lambda result: result["ready"])
    async def async_rows(self) -> dict[str, int | bool]:
        self.async_calls += 1
        await asyncio.sleep(0.01)
        return {"ready": self.ready, "value": self.async_calls}


class MemoryCacheTests(unittest.IsolatedAsyncioTestCase):
    def test_sync_cache_returns_copies_and_supports_invalidation(self) -> None:
        service = ExampleService()

        first = service.sync_rows()
        first[0]["value"] = 999
        cached = service.sync_rows()
        service._cache.invalidate("sync_rows")
        refreshed = service.sync_rows()

        self.assertEqual(cached, [{"value": 1}])
        self.assertEqual(refreshed, [{"value": 2}])
        self.assertEqual(service.sync_calls, 2)

    async def test_async_cache_single_flights_and_can_reject_a_result(self) -> None:
        service = ExampleService()

        first, concurrent = await asyncio.gather(
            service.async_rows(),
            service.async_rows(),
        )
        self.assertEqual(first, concurrent)
        self.assertEqual(service.async_calls, 1)

        service._cache.invalidate("async_rows")
        service.ready = False
        await service.async_rows()
        await service.async_rows()

        self.assertEqual(service.async_calls, 3)


if __name__ == "__main__":
    unittest.main()
