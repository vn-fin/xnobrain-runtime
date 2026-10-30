"""Personal DAG coordinator with exact-grant workers owned by other members."""

from ..feature_flags import FEATURE_PERSONAL_ORG_WORKSPACE_EXPERIENCE, enabled
from ..integrations.organization_team_executions import OrganizationTeamExecutionClient
from ..repositories import StoreError
from ..repositories.organization_team_executions import OrganizationTeamExecutionRepository
from .base import ServiceError
from .organization_team_executions import (
    OrganizationTeamExecutions,
    _ExecutionPlatform,
    _ExecutionRun,
)
from .personal_resource_definitions import require_private_definition_request


class _PersonalAdmission:
    def __init__(self, capacity, repository):
        self.capacity = capacity
        self.store = capacity.store
        self.repository = repository

    def enqueue_team(self, record, team, workflow):
        return self.capacity.enqueue_team(
            record, team, workflow, repository=self.repository, personal_execution=True
        )


class _PersonalPlatform(_ExecutionPlatform):
    def __init__(self, platform, repository):
        super().__init__(platform, repository, None)
        self.run_admission = (
            _PersonalAdmission(platform.run_admission, repository)
            if getattr(platform, "run_admission", None)
            else None
        )

    def _enabled_team_skills(self, agent_id):
        # External skills are restricted by the exact grant at worker admission.
        return []


class _PersonalAgents:
    def __init__(self, local, client, team):
        self.local = local
        self.client = client
        self.external = {
            m["agent_id"]
            for m in team["members"]
            if m.get("external_agent") and m.get("enabled", True)
        }
        self.local_ids = {
            team["orchestrator_id"],
            team.get("synthesis_agent_id") or team["orchestrator_id"],
        }
        self.local_ids.update(
            m["agent_id"]
            for m in team["members"]
            if not m.get("external_agent") and m.get("enabled", True)
        )
        if self.external & self.local_ids:
            raise ServiceError("external aliases must not shadow local agents")

    def list_skills(self, agent_id):
        return self.local.list_skills(agent_id) if agent_id in self.local_ids else {}

    async def chat(self, agent_id, request):
        if agent_id in self.external:
            return await self.client.chat(agent_id, request)
        if agent_id in self.local_ids:
            return await self.local.chat(agent_id, request)
        raise ServiceError("worker outside personal composition", status=403)


class _PersonalRun(_ExecutionRun):
    async def _require_execution_budgets(self, team, workflow):
        if self.analytics is not None:
            for agent_id in sorted(self.agents.local_ids):
                await self.analytics.require_execution_budget(agent_id)


class PersonalDelegatedExecutions(OrganizationTeamExecutions):
    def _repository(self, _namespace):
        return OrganizationTeamExecutionRepository(
            self.platform.repository, "personal", namespace="personal-delegated-executions"
        )

    def _execution_platform(self, repo, _namespace):
        return _PersonalPlatform(self.platform, repo)

    def _prepare_team(self, team):
        if team.get("shared_workspace"):
            raise ServiceError("delegated teams cannot share private workspace files")
        for agent_id in {
            team["orchestrator_id"],
            team.get("synthesis_agent_id") or team["orchestrator_id"],
        }:
            self.platform.agents.describe_agent(agent_id, include_memory=False)

    def _client(self, endpoint, _namespace, run_id, capability):
        client = OrganizationTeamExecutionClient(endpoint, "", run_id, capability)
        client.url = (
            endpoint.rstrip("/")
            + f"/xnobrain/api/control/v1/personal/delegated-execution-callbacks/{run_id}"
        )
        return client

    def _agents(self, client, team):
        return _PersonalAgents(self.platform.agents, client, team)

    def _engine(self, repo, agents, platform, run_id):
        return _PersonalRun(repo, agents, platform, run_id, self.platform.analytics)

    async def handle(self, request, body):
        await require_private_definition_request(request, allow_disabled=True)
        if request.method == "POST" and not enabled(FEATURE_PERSONAL_ORG_WORKSPACE_EXPERIENCE):
            repo = self._repository("personal")
            try:
                repo.get_team_run(request.path_params["team_id"], request.path_params["run_id"])
            except StoreError as error:
                if error.status == 404:
                    raise ServiceError(
                        "personal organization experience is unavailable", status=404
                    ) from error
                raise
        # This is a local storage namespace, never an organization identity.
        request.path_params["organization_id"] = "personal"
        return await super().handle(request, body)

    async def reconcile_admission(self, row):
        row = {**row, "payload": {**row["payload"], "organization_id": "personal"}}
        await super().reconcile_admission(row)
