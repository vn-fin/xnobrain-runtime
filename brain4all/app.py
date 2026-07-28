"""Composition root for the unified Brain4All and Hermes FastAPI server."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import os
from pathlib import Path

from .handlers import APIHandlers
from .repositories import FileRepository
from .routes import setup_routes
from .services import PlatformService


class Brain4AllApplication:
    """Builds clean layers around the original Hermes runtime objects."""

    def __init__(self, agents, config, router, runtime=None):
        root_profile = Path(os.getenv("HERMES_ROOT_PROFILE") or os.getenv("HERMES_HOME") or Path.home() / ".hermes")
        profiles_root = Path(os.getenv("HERMES_PROFILES_ROOT") or root_profile / "profiles")
        data_dir = Path(os.getenv("DATA_DIR") or root_profile / "brain4all")
        self.repository = FileRepository(data_dir, profiles_root)
        if runtime is None:
            from .integrations import LocalRuntimeManager
            runtime = LocalRuntimeManager(data_dir=data_dir)
        self.service = PlatformService(self.repository, agents, config, router, runtime)
        from .integrations.hermes_tools import bind_platform_service
        bind_platform_service(self.service)
        self.handlers = APIHandlers(self.service)

    def register(self, app) -> None:
        setup_routes(app, self.handlers)
        upstream_lifespan = app.router.lifespan_context

        @asynccontextmanager
        async def lifespan(application):
            async with upstream_lifespan(application):
                await self.service.ensure_default_agent()
                dispatcher = None
                try:
                    from .integrations.kanban import dispatcher_loop
                    dispatcher = asyncio.create_task(dispatcher_loop(), name="brain4all-kanban-dispatcher")
                except Exception:
                    dispatcher = None
                try:
                    yield
                finally:
                    with suppress(Exception):
                        await self.service.team_runs.shutdown()
                    if dispatcher is not None:
                        dispatcher.cancel()
                        with suppress(asyncio.CancelledError):
                            await dispatcher

        app.router.lifespan_context = lifespan
