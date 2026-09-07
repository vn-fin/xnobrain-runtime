"""Reusable in-memory caching helpers for single-instance services."""

from __future__ import annotations

import asyncio
import copy
import inspect
import threading
from functools import wraps
from typing import Any, Callable

_MISSING = object()


class MemoryCache:
    """Process-local method cache with per-key sync and async single-flight."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._values: dict[str, Any] = {}
        self._versions: dict[str, int] = {}
        self._sync_locks: dict[str, threading.Lock] = {}
        self._async_locks: dict[str, asyncio.Lock] = {}

    def get(self, key: str) -> tuple[bool, Any]:
        with self._guard:
            value = self._values.get(key, _MISSING)
        if value is _MISSING:
            return False, None
        return True, copy.deepcopy(value)

    def contains(self, key: str) -> bool:
        with self._guard:
            return key in self._values

    def version(self, key: str) -> int:
        with self._guard:
            return self._versions.get(key, 0)

    def store(self, key: str, value: Any, version: int) -> bool:
        with self._guard:
            if self._versions.get(key, 0) != version:
                return False
            self._values[key] = copy.deepcopy(value)
            return True

    def invalidate(self, *keys: str) -> None:
        with self._guard:
            for key in keys:
                self._values.pop(key, None)
                self._versions[key] = self._versions.get(key, 0) + 1

    def sync_lock(self, key: str) -> threading.Lock:
        with self._guard:
            return self._sync_locks.setdefault(key, threading.Lock())

    def async_lock(self, key: str) -> asyncio.Lock:
        with self._guard:
            return self._async_locks.setdefault(key, asyncio.Lock())


def cached_method(
    key: str,
    *,
    cache_when: Callable[[Any], bool] | None = None,
    cache_attribute: str = "_cache",
):
    """Cache a parameterless service method in its ``MemoryCache`` instance."""

    predicate = cache_when or (lambda _result: True)

    def decorator(method):
        if inspect.iscoroutinefunction(method):

            @wraps(method)
            async def async_wrapper(instance, *args, **kwargs):
                if args or kwargs:
                    raise TypeError("cached service methods must be parameterless")
                cache = _service_cache(instance, cache_attribute)
                hit, value = cache.get(key)
                if hit:
                    return value
                async with cache.async_lock(key):
                    hit, value = cache.get(key)
                    if hit:
                        return value
                    version = cache.version(key)
                    result = await method(instance)
                    if predicate(result):
                        cache.store(key, result, version)
                    return copy.deepcopy(result)

            return async_wrapper

        @wraps(method)
        def sync_wrapper(instance, *args, **kwargs):
            if args or kwargs:
                raise TypeError("cached service methods must be parameterless")
            cache = _service_cache(instance, cache_attribute)
            hit, value = cache.get(key)
            if hit:
                return value
            with cache.sync_lock(key):
                hit, value = cache.get(key)
                if hit:
                    return value
                version = cache.version(key)
                result = method(instance)
                if predicate(result):
                    cache.store(key, result, version)
                return copy.deepcopy(result)

        return sync_wrapper

    return decorator


def _service_cache(instance: Any, attribute: str) -> MemoryCache:
    cache = getattr(instance, attribute, None)
    if not isinstance(cache, MemoryCache):
        raise RuntimeError(f"{type(instance).__name__} must initialize {attribute} = MemoryCache()")
    return cache
