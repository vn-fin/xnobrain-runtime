"""Composition root for the unified XNOBrain and Hermes FastAPI server."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import logging
import os
from pathlib import Path

from .handlers import APIHandlers
from .repositories import FileRepository
from .routes import setup_routes
from .services import PlatformService


class XNOBrainApplication:
    """Builds clean layers around the original Hermes runtime objects."""

    def __init__(self, agents, config, router, runtime=None):
        root_profile = Path(os.getenv("HERMES_ROOT_PROFILE") or os.getenv("HERMES_HOME") or Path.home() / ".hermes")
        profiles_root = Path(os.getenv("HERMES_PROFILES_ROOT") or root_profile / "profiles")
        data_dir = Path(os.getenv("DATA_DIR") or root_profile / "xnobrain")
        self.repository = FileRepository(data_dir, profiles_root)
        if runtime is None:
            from .integrations import LocalRuntimeManager
            runtime = LocalRuntimeManager(data_dir=data_dir)
        self.service = PlatformService(self.repository, agents, config, router, runtime)
        self.handlers = APIHandlers(self.service)

    def register(self, app) -> None:
        setup_routes(app, self.handlers)
        upstream_lifespan = app.router.lifespan_context

        @asynccontextmanager
        async def lifespan(application):
            async with upstream_lifespan(application):
                await self.service.ensure_default_agent()
                from .integrations.runtime_gateway import start_runtime_gateway
                grpc_server = await start_runtime_gateway()
                dispatcher = None
                try:
                    from .integrations.kanban import dispatcher_loop
                    dispatcher = asyncio.create_task(
                        dispatcher_loop(on_tick=self.service.cron.reconcile_deliveries),
                        name="xnobrain-kanban-dispatcher",
                    )
                except Exception:
                    dispatcher = None
                cron_tasks: dict[tuple[str, str], asyncio.Task] = {}

                def finish_cron_task(key: tuple[str, str], task: asyncio.Task) -> None:
                    cron_tasks.pop(key, None)
                    if not task.cancelled():
                        try:
                            task.result()
                        except Exception:
                            logging.getLogger(__name__).exception("Profile cron execution failed")

                async def profile_cron_loop() -> None:
                    while True:
                        due = await asyncio.to_thread(self.service.cron.due_jobs)
                        for profile, job_id in due:
                            key = (profile, job_id)
                            if key in cron_tasks:
                                continue
                            task = asyncio.create_task(
                                asyncio.to_thread(self.service.cron.fire_due, profile, job_id),
                                name=f"xnobrain-cron-{job_id}",
                            )
                            task.add_done_callback(lambda completed, key=key: finish_cron_task(key, completed))
                            cron_tasks[key] = task
                        await asyncio.sleep(1)

                cron_dispatcher = asyncio.create_task(profile_cron_loop(), name="xnobrain-profile-cron-dispatcher")
                try:
                    yield
                finally:
                    if grpc_server is not None:
                        await grpc_server.stop(grace=5)
                    cron_dispatcher.cancel()
                    with suppress(asyncio.CancelledError):
                        await cron_dispatcher
                    for task in tuple(cron_tasks.values()):
                        task.cancel()
                    with suppress(Exception):
                        await self.service.conversation_runs.shutdown()
                    with suppress(Exception):
                        await self.service.team_runs.shutdown()
                    if dispatcher is not None:
                        dispatcher.cancel()
                        with suppress(asyncio.CancelledError):
                            await dispatcher

        app.router.lifespan_context = lifespan
