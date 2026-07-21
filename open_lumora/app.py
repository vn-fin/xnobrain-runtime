"""Composition root for the unified Open Lumora and Hermes FastAPI server."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import os
from pathlib import Path

from .handlers import APIHandlers
from .repositories import FileRepository
from .routes import setup_routes
from .services import PlatformService


class OpenLumoraApplication:
    """Builds clean layers around the original Hermes runtime objects."""

    def __init__(self, agents, config, router):
        root_profile = Path(os.getenv("HERMES_ROOT_PROFILE") or os.getenv("HERMES_HOME") or Path.home() / ".hermes")
        profiles_root = Path(os.getenv("HERMES_PROFILES_ROOT") or root_profile / "profiles")
        data_dir = Path(os.getenv("DATA_DIR") or root_profile / "open-lumora")
        self.repository = FileRepository(data_dir, profiles_root)
        self.service = PlatformService(self.repository, agents, config, router)
        self.handlers = APIHandlers(self.service)

    def register(self, app) -> None:
        setup_routes(app, self.handlers)
        upstream_lifespan = app.router.lifespan_context

        @asynccontextmanager
        async def lifespan(application):
            async with upstream_lifespan(application):
                scheduler = asyncio.create_task(
                    self.service.scheduler_loop(), name="open-lumora-cron"
                )
                try:
                    yield
                finally:
                    scheduler.cancel()
                    with suppress(asyncio.CancelledError):
                        await scheduler

        app.router.lifespan_context = lifespan
