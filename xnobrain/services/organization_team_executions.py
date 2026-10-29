"""BigBrother on the initiator workspace; members execute via Control runners."""

import asyncio
import hashlib
import hmac
import os

from ..defaults import BIG_BROTHER_AGENT_ID
from ..integrations.organization_team_executions import OrganizationTeamExecutionClient
from ..repositories import StoreError
from ..repositories.organization_team_executions import OrganizationTeamExecutionRepository
from .base import ServiceError, iso
from .team_runs import MAX_ACTIVE_TEAM_RUNS, TeamRunService
from .teams import TeamsServiceMixin


class _ExecutionAdmission:
    """Use the workspace queue while keeping organization records isolated."""

    def __init__(self, capacity, repository, organization_id):
        self.capacity = capacity
        self.store = capacity.store
        self.repository = repository
        self.organization_id = organization_id

    def enqueue_team(self, record, team, workflow):
        return self.capacity.enqueue_team(
            record,
            team,
            workflow,
            repository=self.repository,
            organization_id=self.organization_id,
        )


class _ExecutionPlatform(TeamsServiceMixin):
    distributed_workers = True

    def __init__(self, platform, repository, organization_id):
        self.repository = repository
        self.runtime_updates = platform.runtime_updates
        self.agents = platform.agents
        capacity = getattr(platform, "run_admission", None)
        self.run_admission = (
            _ExecutionAdmission(capacity, repository, organization_id) if capacity else None
        )

    def _enabled_team_skills(self, agent_id):
        if agent_id == BIG_BROTHER_AGENT_ID:
            return super()._enabled_team_skills(agent_id)
        return []


class _ExecutionAgents:
    def __init__(self, local, client, members):
        self.local = local
        self.client = client
        self.members = members

    def list_skills(self, agent_id):
        return self.local.list_skills(agent_id) if agent_id == BIG_BROTHER_AGENT_ID else {}

    async def chat(self, agent_id, request):
        if agent_id == BIG_BROTHER_AGENT_ID:
            return await self.local.chat(agent_id, request)
        if agent_id not in self.members:
            raise ServiceError("worker outside organization team", status=403)
        return await self.client.chat(agent_id, request)


class _ExecutionRun(TeamRunService):
    def __init__(self, repository, agents, platform, run_id, analytics):
        super().__init__(repository, agents, platform, analytics)
        self.run_id = run_id

    def _new_record(self, *args, **kwargs):
        record = super()._new_record(*args, **kwargs)
        record["id"] = self.run_id
        return record

    async def _require_execution_budgets(self, team, workflow):
        if self.analytics is not None:
            await self.analytics.require_execution_budget(BIG_BROTHER_AGENT_ID)

    async def _persist(self, record):
        await super()._persist(record)
        # Local execution state remains durable during a Control outage; GET
        # reconciles it. Remote dispatch itself fails closed without Control.
        try:
            await self.agents.client.record(record)
        except ServiceError as error:
            if error.code == "organization_execution_revoked":
                raise asyncio.CancelledError from error


class OrganizationTeamExecutions:
    def _repository(self, organization_id):
        return OrganizationTeamExecutionRepository(self.platform.repository, organization_id)

    def _execution_platform(self, repo, organization_id):
        return _ExecutionPlatform(self.platform, repo, organization_id)

    def _prepare_team(self, team):
        if team["orchestrator_id"] != BIG_BROTHER_AGENT_ID:
            raise ServiceError("invalid organization coordinator", status=400)
        team["synthesis_agent_id"] = BIG_BROTHER_AGENT_ID

    def _client(self, endpoint, organization_id, run_id, capability):
        return OrganizationTeamExecutionClient(endpoint, organization_id, run_id, capability)

    def _agents(self, client, team):
        return _ExecutionAgents(
            self.platform.agents,
            client,
            {member["agent_id"] for member in team["members"] if member.get("enabled", True)},
        )

    def _engine(self, repo, agents, platform, run_id):
        return _ExecutionRun(repo, agents, platform, run_id, self.platform.analytics)

    def __init__(self, platform):
        self.platform = platform
        self.executions = {}
        self.lock = asyncio.Lock()

    async def handle(self, request, body):
        secret = os.getenv("RUNTIME_INTERNAL_SERVICE_TOKEN", "")
        raw = await request.body()
        message = "\n".join((request.method, request.url.path, hashlib.sha256(raw).hexdigest()))
        expected = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
        supplied = request.headers.get("x-xnobrain-team-signature", "")
        if not secret or not hmac.compare_digest(expected, supplied):
            raise ServiceError("private execution authorization required", status=403)
        params = request.path_params
        repo = self._repository(params["organization_id"])
        team_id = repo._id(params["team_id"])
        run_id = repo._id(params["run_id"])
        key = (params["organization_id"], team_id, run_id)
        if request.method == "POST":
            async with self.lock:
                return await self._start(key, repo, body)
        engine = self.executions.get(key)
        if engine is None:
            engine = TeamRunService(
                repo,
                self.platform.agents,
                self._execution_platform(repo, params["organization_id"]),
            )
        if request.method == "DELETE":
            return await engine.cancel_run(team_id, run_id)
        return engine.get_run(team_id, run_id)

    async def _start(self, key, repo, body):
        organization_id, team_id, run_id = key
        if key in self.executions:
            return self.executions[key].get_run(team_id, run_id)
        try:
            previous = repo.get_team_run(team_id, run_id)
        except StoreError as error:
            if error.status != 404:
                raise
        else:
            engine = TeamRunService(
                repo,
                self.platform.agents,
                self._execution_platform(repo, organization_id),
            )
            return engine._heal_if_stale(previous)
        active = sum(bool(engine._active) for engine in self.executions.values())
        if active + len(self.platform.team_runs._active) >= MAX_ACTIVE_TEAM_RUNS:
            raise ServiceError("too many active team runs", status=409)
        team = {**body["team"], "id": team_id}
        self._prepare_team(team)
        repo.put_team(team)
        endpoint = os.getenv("RUNTIME_CONTROL_URL", "")
        if not endpoint:
            raise ServiceError("Control is unavailable", status=503)
        client = self._client(endpoint, organization_id, run_id, body["capability"])
        agents = self._agents(client, team)
        engine = self._engine(repo, agents, self._execution_platform(repo, organization_id), run_id)
        self.executions[key] = engine
        try:
            record = await engine.start_run(team_id, body["input"])
        except (ServiceError, StoreError) as error:
            record = {
                "id": run_id,
                "team_id": team_id,
                "status": "failed",
                "error": error.code,
                "revision": 1,
                "mode": "async",
                "task": body["input"].get("task", ""),
                "steps": [],
                "orchestrator_id": team["orchestrator_id"],
                "orchestrator_summary": "",
                "created_at": iso(),
                "updated_at": iso(),
                "ended_at": iso(),
            }
            repo.put_team_run(record)
            self.executions.pop(key, None)
            return record
        entry = engine.registry_entry(run_id)
        if entry is not None and entry.task is not None:
            entry.task.add_done_callback(lambda _task: self.executions.pop(key, None))
        return record

    async def reconcile_admission(self, row):
        organization_id = row["payload"]["organization_id"]
        key = (organization_id, row["conversation"], row["id"])
        engine = self.executions.get(key)
        if engine is None:
            # The callback capability lives only in memory. After a restart,
            # release the durable admission without replaying remote side effects
            # or persisting credentials. Control reconciles the terminal record
            # through the existing signed GET endpoint.
            repo = self._repository(organization_id)
            engine = TeamRunService(
                repo,
                self.platform.agents,
                self._execution_platform(repo, organization_id),
            )
            try:
                record = repo.get_team_run(row["conversation"], row["id"])
            except StoreError as error:
                if error.status != 404:
                    raise
                record = row["payload"]["_record"]
                repo.put_team_run(record)
            if record["status"] in {"pending", "running"}:
                # Another process may still own the executor fence.
                if self.platform.run_admission.store.team_active(row["id"]):
                    return
                await engine._finalize(record, "failed", error="interrupted_by_restart")
        try:
            await self.platform.run_admission._tick_team(row, teams=engine)
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise
            # A revoked callback cancels this run, not the workspace queue loop.
            # Persist locally without invoking the revoked callback again.
            record = engine.repository.get_team_run(row["conversation"], row["id"])
            record["status"] = "cancelled"
            record["error"] = "organization_execution_revoked"
            record["ended_at"] = iso()
            record["completed_at"] = None
            engine._cancel_open_steps(record)
            await TeamRunService._persist(engine, record)
            await self.platform.run_admission._tick_team(row, teams=engine)
        if not self.platform.run_admission.store.contains(row["id"]):
            self.executions.pop(key, None)

    async def shutdown(self):
        await asyncio.gather(*(engine.shutdown() for engine in self.executions.values()))
