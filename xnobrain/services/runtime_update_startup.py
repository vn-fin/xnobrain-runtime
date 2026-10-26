"""Defer managed profile repairs until durable rollout verification completes."""

from __future__ import annotations

import asyncio

from ..repositories.base import StoreError
from ..repositories.runtime_update_gate import WorkspaceActivity


async def finish_startup(platform, *, deferred: bool = False) -> None:
    # Maintenance is durable across process replacement. Read it on every
    # iteration; both verified resume and verified abort release this barrier.
    deferred = deferred or platform.runtime_updates.dispatch_paused
    while True:
        while platform.runtime_updates.dispatch_paused:
            await asyncio.sleep(0.25)
        try:
            activity = WorkspaceActivity(platform.repository.data_dir)
        except StoreError as error:
            if error.code not in {
                "runtime_update_maintenance",
                "workspace_rebalance_maintenance",
                "workspace_memory_maintenance",
            }:
                raise
            deferred = True
            await asyncio.sleep(0.25)
            continue
        with activity:
            if deferred:
                platform.portability._cleanup_transfers()
                platform.config.ensure_write_approval_defaults()
                platform._ensure_existing_write_approval_defaults()
            await platform.ensure_default_agent()
        return
