"""Composition root for the unified XNOBrain and Hermes FastAPI server."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from .handlers import APIHandlers
from .repositories import FileRepository
from .routes import setup_routes
from .services import PlatformService
from .services.runtime_shutdown import shutdown_stage
from .services.runtime_update_startup import finish_startup


class XNOBrainApplication:
    """Builds clean layers around the original Hermes runtime objects."""

    def __init__(self, agents, config, router, runtime=None):
        root_profile = Path(
            os.getenv("HERMES_ROOT_PROFILE") or os.getenv("HERMES_HOME") or Path.home() / ".hermes"
        )
        profiles_root = Path(os.getenv("HERMES_PROFILES_ROOT") or root_profile / "profiles")
        data_dir = Path(os.getenv("DATA_DIR") or root_profile / "xnobrain")
        self.repository = FileRepository(data_dir, profiles_root, root_profile=root_profile)
        if runtime is None:
            from .integrations import LocalRuntimeManager

            runtime = LocalRuntimeManager(data_dir=data_dir)
        self.service = PlatformService(self.repository, agents, config, router, runtime)
        self.handlers = APIHandlers(self.service)

    def register(self, app) -> None:
        from .integrations.cron_maintenance import install_ticker_maintenance
        from .integrations.rebalance_admission import WorkspaceAdmissionMiddleware

        install_ticker_maintenance(self.repository.data_dir)
        app.add_middleware(WorkspaceAdmissionMiddleware, root=self.repository.data_dir)
        setup_routes(app, self.handlers)
        upstream_lifespan = app.router.lifespan_context

        @asynccontextmanager
        async def lifespan(application):
            self.service.workspace_memory.start()
            async with upstream_lifespan(application):
                startup_task = None
                if self.service.runtime_updates.dispatch_paused:
                    startup_task = asyncio.create_task(
                        finish_startup(self.service, deferred=True),
                        name="xnobrain-post-update-startup",
                    )

                    def report_startup_failure(task):
                        if not task.cancelled() and (error := task.exception()) is not None:
                            logging.getLogger(__name__).error(
                                "Deferred Runtime startup failed",
                                extra={"error_type": type(error).__name__},
                            )

                    startup_task.add_done_callback(report_startup_failure)
                else:
                    await finish_startup(self.service)
                await self.service.organization_connector.start()
                if self.service.run_admission is not None:
                    await self.service.run_admission.start()
                from .integrations.runtime_gateway import RuntimeGatewaySupervisor

                grpc_supervisor = RuntimeGatewaySupervisor(
                    rebalances=self.service.runtime_rebalances,
                    memory=self.service.workspace_memory,
                )
                await grpc_supervisor.start()
                grpc_supervisor_task = asyncio.create_task(
                    grpc_supervisor.run(), name="xnobrain-grpc-supervisor"
                )
                dispatcher = None
                await self.service.kanban_delegated_tasks.start()
                try:
                    from .integrations.kanban import dispatcher_loop
                    from .integrations.kanban_workers import KanbanWorkerSpawner

                    dispatcher = asyncio.create_task(
                        dispatcher_loop(
                            spawn_fn=KanbanWorkerSpawner(
                                self.service.agents,
                                self.service.analytics,
                                asyncio.get_running_loop(),
                            ),
                            on_tick=self.service.kanban_delegated_tasks.tick,
                            dispatch_allowed=lambda: (
                                not self.service.runtime_updates.dispatch_paused
                            ),
                            activity_root=self.repository.data_dir,
                        ),
                        name="xnobrain-kanban-dispatcher",
                    )
                except Exception:
                    dispatcher = None
                self.service.custom_page.schedules.loop = asyncio.get_running_loop()
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
                            task.add_done_callback(
                                lambda completed, key=key: finish_cron_task(key, completed)
                            )
                            cron_tasks[key] = task
                        await asyncio.sleep(1)

                cron_dispatcher = asyncio.create_task(
                    profile_cron_loop(), name="xnobrain-profile-cron-dispatcher"
                )
                await self.service.portability_tasks.worker.start()
                try:
                    yield
                finally:
                    if startup_task is not None:
                        startup_task.cancel()
                        with suppress(asyncio.CancelledError, Exception):
                            await startup_task
                    if dispatcher is not None:
                        dispatcher.cancel()
                        with suppress(asyncio.CancelledError):
                            await dispatcher
                    async with shutdown_stage("personal_task_reconciliation"):
                        await self.service.kanban_delegated_tasks.close()
                    if self.service.run_admission is not None:
                        async with shutdown_stage("run_admission"):
                            await self.service.run_admission.close()
                    async with shutdown_stage("portability"):
                        await self.service.portability_tasks.worker.shutdown()
                    async with shutdown_stage("organization_connector"):
                        await self.service.organization_connector.stop()
                    async with shutdown_stage("grpc"):
                        await grpc_supervisor.stop()
                    grpc_supervisor_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await grpc_supervisor_task
                    cron_dispatcher.cancel()
                    with suppress(asyncio.CancelledError):
                        await cron_dispatcher
                    for task in tuple(cron_tasks.values()):
                        task.cancel()
                    with suppress(Exception):
                        async with shutdown_stage("conversation_runs"):
                            await self.service.conversation_runs.shutdown()
                    with suppress(Exception):
                        async with shutdown_stage("team_runs"):
                            await self.service.organization_team_executions.shutdown()
                            await self.service.personal_delegated_executions.shutdown()
                            await self.service.team_runs.shutdown()
                    with suppress(Exception):
                        async with shutdown_stage("accounting"):
                            await self.service.analytics.accounting.close()

        app.router.lifespan_context = lifespan
