"""Workspace project authorization and profile lifecycle coordination."""

from contextlib import contextmanager

from pydantic import ValidationError

from ..defaults import BIG_BROTHER_AGENT_ID
from ..models.agent_projects import ProjectAssignment, ProjectRevision, ProjectWrite
from ..repositories.agent_projects import AgentProjectsRepository, fail
from ..repositories.base import StoreError
from ..repositories.custom_page_locks import acquire, execution_name, release
from ..repositories.runtime_update_gate import WorkspaceActivity


@contextmanager
def profile_read_gate(root, agent_id):
    """Allow ongoing executions, but serialize against profile removal."""
    with WorkspaceActivity(root):
        descriptor = acquire(root, execution_name(agent_id), shared=True, timeout=2)
        try:
            yield
        finally:
            release(descriptor)


class AgentProjectsService:
    def __init__(self, platform):
        self.platform = platform
        self.repository = AgentProjectsRepository(platform.repository)

    @staticmethod
    def scope(trusted):
        if not trusted or not trusted.subject or not trusted.tenant_id:
            fail("trusted_subject_required", 401)
        context = trusted.ownership_context or {}
        if trusted.organization_id:
            if trusted.tenant_id != f"organization-{trusted.organization_id}":
                fail("agent_projects_workspace_mismatch", 403)
        elif context.get("owner_kind") == "organization":
            fail("agent_projects_workspace_mismatch", 403)
        if context.get("state", "active") != "active":
            fail("agent_projects_authority_revoked", 403)
        return trusted.tenant_id, trusted.subject

    def read(self, trusted):
        scope = self.scope(trusted)
        result = self.repository.read(scope)
        # A missing profile can result from native CLI deletion. Hidden import
        # candidates retain membership until their lifecycle completes.
        hidden = set()
        for agent_id in list(result["memberships"]):
            try:
                profile = self.platform.repository.live_profile_path(agent_id)
            except StoreError as error:
                if error.status != 404:
                    raise
                hidden.add(agent_id)
                continue
            if not profile.is_dir():
                with profile_read_gate(self.platform.repository.data_dir, agent_id):
                    if profile.is_dir():
                        continue
                    self.repository.forget_agent(agent_id)
        result = self.repository.read(scope)
        result["memberships"] = {
            key: value for key, value in result["memberships"].items() if key not in hidden
        }
        return result

    def mutate(self, operation, body, trusted, *, project_id=None, agent_id=None):
        scope = self.scope(trusted)
        model = (
            ProjectAssignment
            if operation == "assign"
            else (ProjectRevision if operation == "delete" else ProjectWrite)
        )
        try:
            payload = model.model_validate(body).model_dump()
        except ValidationError:
            fail("agent_projects_invalid_input", 422)
        updates = getattr(self.platform, "runtime_updates", None)
        if updates is not None:
            updates.require_dispatch()
        if operation == "assign":
            if agent_id == BIG_BROTHER_AGENT_ID:
                fail("protected_agent", 409)
            with profile_read_gate(self.platform.repository.data_dir, agent_id):
                profile = self.platform.repository.live_profile_path(agent_id)
                if not profile.is_dir():
                    fail("agent_not_found", 404)
                return self.repository.mutate(
                    scope,
                    payload["expected_revision"],
                    operation,
                    agent_id=agent_id,
                    project_id=payload["project_id"],
                )
        return self.repository.mutate(
            scope,
            payload["expected_revision"],
            operation,
            project_id=project_id,
            name=payload.get("name"),
        )
