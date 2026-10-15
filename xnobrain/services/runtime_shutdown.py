"""Metadata-only diagnostics for shutdown stages without abandoning durable I/O."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager, suppress

logger = logging.getLogger(__name__)


@asynccontextmanager
async def shutdown_stage(stage: str, *, warn_after: float = 10):
    started = time.monotonic()

    async def report_slow():
        await asyncio.sleep(warn_after)
        logger.warning(
            "Runtime shutdown stage still waiting",
            extra={"shutdown_stage": stage, "elapsed_seconds": time.monotonic() - started},
        )

    reporter = asyncio.create_task(report_slow(), name="xnobrain-shutdown-observer")
    try:
        yield
    finally:
        reporter.cancel()
        with suppress(asyncio.CancelledError):
            await reporter
        logger.info(
            "Runtime shutdown stage exited",
            extra={"shutdown_stage": stage, "elapsed_seconds": time.monotonic() - started},
        )
