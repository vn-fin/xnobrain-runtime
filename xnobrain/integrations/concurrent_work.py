"""Run-scoped limits and immutable inheritance for concurrent work."""

from __future__ import annotations

import hashlib
import json
import threading
from types import MappingProxyType
from typing import Any, Callable, Mapping

from xnobrain.runtime_limits import (
    concurrent_work_max_depth,
    concurrent_work_max_turns,
    max_parallel_agents,
)


class ConcurrentWorkLimitError(RuntimeError):
    """A shared parent-run execution limit was exhausted."""


class RunExecutionCoordinator:
    """Share turn, depth, concurrency, cancellation, and grants across a run."""

    def __init__(
        self,
        run_id: str,
        ownership_context: Mapping[str, Any] | None,
        emit: Callable[..., None],
    ) -> None:
        self.run_id = run_id
        self.turn_limit = concurrent_work_max_turns()
        self.depth_limit = concurrent_work_max_depth()
        self.concurrency_limit = max_parallel_agents()
        self._ownership_context = MappingProxyType(dict(ownership_context or {}))
        self._emit = emit
        self._lock = threading.RLock()
        self._request_ids: set[str] = set()
        self._turns_used = 0
        self._active_children = 0
        self._peak_children = 0
        self._cancelled = False

    @property
    def ownership_context(self) -> Mapping[str, Any]:
        return self._ownership_context

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True

    def install(self, agent: Any) -> None:
        """Install one idempotent provider-request guard on an agent."""
        if getattr(agent, "_xnobrain_run_coordinator", None) is self:
            return
        agent._xnobrain_run_coordinator = self
        agent._xnobrain_ownership_context = self._ownership_context
        original = getattr(agent, "_build_api_kwargs", None)
        if not callable(original):
            return

        def guarded_build_api_kwargs(*args: Any, **kwargs: Any) -> dict[str, Any]:
            self.reserve_provider_request(agent)
            return original(*args, **kwargs)

        agent._build_api_kwargs = guarded_build_api_kwargs

    def install_child(self, child: Any) -> None:
        """Narrow one child to this run and account for its lifetime."""
        self.install(child)
        depth = max(0, int(getattr(child, "_delegate_depth", 0) or 0))
        original = getattr(child, "run_conversation", None)
        if not callable(original) or getattr(child, "_xnobrain_coordinated_child", False):
            return
        child._xnobrain_coordinated_child = True

        def coordinated_run(*args: Any, **kwargs: Any) -> Any:
            if depth > self.depth_limit:
                raise ConcurrentWorkLimitError(
                    f"shared delegation depth limit reached ({self.depth_limit})"
                )
            self.acquire_child()
            try:
                return original(*args, **kwargs)
            finally:
                self.release_child()

        child.run_conversation = coordinated_run

    def reserve_provider_request(self, agent: Any) -> None:
        """Reserve exactly one shared turn for a logical provider request."""
        depth = max(0, int(getattr(agent, "_delegate_depth", 0) or 0))
        if depth > self.depth_limit:
            raise ConcurrentWorkLimitError(
                f"shared delegation depth limit reached ({self.depth_limit})"
            )
        material = {
            "run_id": self.run_id,
            "session_id": str(getattr(agent, "session_id", "") or ""),
            "turn_id": str(getattr(agent, "_current_turn_id", "") or ""),
            "api_call": int(getattr(agent, "_api_call_count", 0) or 0),
            "depth": depth,
        }
        request_id = (
            "req_"
            + hashlib.sha256(
                json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()[:24]
        )
        with self._lock:
            if self._cancelled:
                raise ConcurrentWorkLimitError("parent run cancellation was requested")
            if request_id in self._request_ids:
                return
            if self._turns_used >= self.turn_limit:
                raise ConcurrentWorkLimitError(
                    f"shared provider-turn limit reached ({self.turn_limit})"
                )
            self._request_ids.add(request_id)
            self._turns_used += 1
            used = self._turns_used
        self._emit(
            "execution.budget",
            "provider",
            "",
            None,
            request_id=request_id,
            turns_used=used,
            turn_limit=self.turn_limit,
            depth=depth,
        )

    def acquire_child(self) -> None:
        with self._lock:
            if self._cancelled:
                raise ConcurrentWorkLimitError("parent run cancellation was requested")
            if self._active_children >= self.concurrency_limit:
                raise ConcurrentWorkLimitError(
                    f"shared child concurrency limit reached ({self.concurrency_limit})"
                )
            self._active_children += 1
            self._peak_children = max(self._peak_children, self._active_children)
            active = self._active_children
            peak = self._peak_children
        self._emit(
            "execution.concurrency",
            "delegate_task",
            "",
            None,
            active_children=active,
            peak_children=peak,
            concurrency_limit=self.concurrency_limit,
        )

    def release_child(self) -> None:
        with self._lock:
            self._active_children = max(0, self._active_children - 1)
            active = self._active_children
            peak = self._peak_children
        self._emit(
            "execution.concurrency",
            "delegate_task",
            "",
            None,
            active_children=active,
            peak_children=peak,
            concurrency_limit=self.concurrency_limit,
        )
