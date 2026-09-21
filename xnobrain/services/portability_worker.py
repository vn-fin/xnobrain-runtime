"""Lifecycle-managed execution of durable portability claims."""

from __future__ import annotations

import asyncio
import errno
import logging
import uuid
from collections.abc import Callable
from typing import Any

from ..repositories.portability_tasks import PortabilityTaskStore

logger = logging.getLogger(__name__)


class ClaimLostError(RuntimeError):
    """The worker no longer has authority to publish this task."""


class PortabilityWorker:
    """Run blocking archive operations off-loop, renewing their durable lease.

    Executors must acquire the store publication lock and check their claim
    immediately before every filesystem publication. Shutdown waits for running
    work rather than cancelling an asyncio wrapper while its thread keeps writing.
    """

    def __init__(
        self,
        store: PortabilityTaskStore,
        execute: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        lease_seconds: float = 30,
        poll_seconds: float = 1,
        maintenance: Callable[[], None] | None = None,
    ):
        self.store = store
        self.execute = execute
        self.owner = uuid.uuid4().hex
        self.lease_seconds = lease_seconds
        self.poll_seconds = poll_seconds
        self.maintenance = maintenance
        self._next_maintenance = 0.0
        self._stop = asyncio.Event()
        self._loop_task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._loop_task is not None and not self._loop_task.done():
            return
        self._stop.clear()
        self._loop_task = asyncio.create_task(self._run(), name="xnobrain-portability-worker")

    async def shutdown(self) -> None:
        self._stop.set()
        if self._loop_task is not None:
            await asyncio.shield(self._loop_task)
            self._loop_task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                now = asyncio.get_running_loop().time()
                if self.maintenance is not None and now >= self._next_maintenance:
                    # A failed cleanup must neither starve claims nor become a
                    # tight retry loop. Queue execution is independent of maintenance.
                    self._next_maintenance = now + 60
                    try:
                        await asyncio.to_thread(self.maintenance)
                    except Exception:
                        logger.warning("Portability maintenance failed")
                claim = await asyncio.to_thread(
                    self.store.claim, self.owner, lease_seconds=self.lease_seconds
                )
                if claim is not None:
                    await self._execute_claim(claim)
                    continue
            except Exception:
                # Do not include exception text: archive/storage errors can contain
                # private paths or supplied content. Recovery is owned by the lease.
                logger.warning("Portability worker iteration failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass

    async def _execute_claim(self, claim: dict[str, Any]) -> None:
        completed = asyncio.Event()
        lost = asyncio.Event()

        async def renew() -> None:
            while not completed.is_set():
                try:
                    await asyncio.wait_for(completed.wait(), timeout=self.lease_seconds / 3)
                    return
                except TimeoutError:
                    pass
                try:
                    valid = await asyncio.to_thread(
                        self.store.heartbeat,
                        claim["id"],
                        self.owner,
                        claim["fence"],
                        lease_seconds=self.lease_seconds,
                    )
                except Exception:
                    valid = False
                if not valid:
                    lost.set()
                    return

        heartbeat = asyncio.create_task(renew(), name="xnobrain-portability-heartbeat")
        try:
            try:
                result = await asyncio.to_thread(self.execute, claim)
                error = None
            except ClaimLostError:
                return
            except Exception as cause:
                transient = getattr(cause, "code", "") == "task_storage_busy" or (
                    isinstance(cause, OSError)
                    and cause.errno
                    in {
                        errno.EAGAIN,
                        errno.EBUSY,
                        errno.ETIMEDOUT,
                    }
                )
                if transient and not lost.is_set():
                    retried = await asyncio.to_thread(
                        self.store.retry_claim,
                        claim["id"],
                        self.owner,
                        claim["fence"],
                        delay_seconds=min(30, 5 * claim["attempts"]),
                    )
                    if retried:
                        return
                result = None
                # Before durable publication intent, every effect is private
                # staging. Such a failed request can release its input pin for a
                # deliberate new-key retry. Once any intent exists, preserve all
                # evidence: an interrupted rename may have installed live data.
                if claim["kind"] == "IMPORT":
                    journal = await asyncio.to_thread(self.store.journal, claim["id"])
                    recovery_required = any(
                        entry["phase"] in {"PREPARED", "PUBLISHED"} for entry in journal
                    )
                    error = {
                        "code": "import_recovery_required"
                        if recovery_required
                        else "import_failed",
                        "message": "Import requires recovery"
                        if recovery_required
                        else "Import failed",
                        "retryable": False,
                    }
                else:
                    error = {
                        "code": "snapshot_generation_failed",
                        "message": "Snapshot generation failed",
                        "retryable": False,
                    }
            if not lost.is_set():
                await asyncio.to_thread(
                    self.store.finish,
                    claim["id"],
                    self.owner,
                    claim["fence"],
                    result=result,
                    error=error,
                )
        finally:
            completed.set()
            await heartbeat
