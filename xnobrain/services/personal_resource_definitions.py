"""Private projections of owner-local configuration; never profile exports."""

import hashlib
import hmac
import json
import os
import uuid

from ..feature_flags import FEATURE_PERSONAL_ORG_WORKSPACE_EXPERIENCE, enabled
from ..models.teams import TeamCreate
from .base import ServiceError, iso


def definition_revision(configuration):
    encoded = json.dumps(
        configuration, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def team_definition(source_id, team):
    configuration = TeamCreate.model_validate(team).model_dump(exclude_none=True)
    references = [{"agent_id": configuration["orchestrator_id"]}]
    synthesis = configuration.get("synthesis_agent_id")
    if synthesis and synthesis != configuration["orchestrator_id"]:
        references.append({"agent_id": synthesis})
    for member in configuration["members"]:
        ref = {"agent_id": member["agent_id"]}
        if member.get("external_agent"):
            ref["external_agent"] = member["external_agent"]
        references.append(ref)
    bound = {
        "version": 1,
        "kind": "agent_team",
        "source_id": source_id,
        "configuration": configuration,
        "agent_references": references,
    }
    return {**bound, "source_revision": definition_revision(bound)}


async def require_private_definition_request(request, *, allow_disabled=False):
    secret = os.getenv("RUNTIME_INTERNAL_SERVICE_TOKEN", "")
    raw = await request.body()
    material = "\n".join((request.method, request.url.path, hashlib.sha256(raw).hexdigest()))
    expected = hmac.new(secret.encode(), material.encode(), hashlib.sha256).hexdigest()
    supplied = request.headers.get("x-xnobrain-team-signature", "")
    if not secret or request.url.query or not hmac.compare_digest(expected, supplied):
        raise ServiceError("private definition authorization required", status=403)
    if not allow_disabled and not enabled(FEATURE_PERSONAL_ORG_WORKSPACE_EXPERIENCE):
        raise ServiceError("personal organization experience is unavailable", status=404)


class PersonalResourceDefinitions:
    def __init__(self, platform):
        self.platform = platform

    async def handle(self, request, body):
        await require_private_definition_request(request)
        return self.load(request.path_params["kind"], request.path_params["source_id"], body)

    async def save_team(self, request, body):
        await require_private_definition_request(request)
        source_id = request.path_params["team_id"]
        created = iso()
        if source_id == "new":
            source_id = uuid.uuid4().hex
        else:
            current = self.platform.repository.get_team(source_id)
            created = current.get("created_at", created)
        return self.platform._put_team(source_id, body, created_at=created, allow_external=True)

    def load(self, kind, source_id, body):
        source_id = self.platform.repository._id(source_id)
        references = []
        if kind == "agent":
            agent = self.platform.agents.describe_agent(source_id, include_memory=False)
            metadata = agent.get("metadata") or {}
            # No config dictionary, environment, SOUL, memory, skill contents,
            # paths or credentials cross this boundary. Publication binds the
            # existing owner agent rather than cloning its filesystem.
            configuration = {
                "agent_id": source_id,
                "name": str(metadata.get("display_name") or source_id),
                "description": str(metadata.get("description") or ""),
            }
            references = [{"agent_id": source_id}]
        elif kind == "agent_team":
            team = self.platform.repository.get_team(source_id)
            snapshot = team_definition(source_id, team)
            configuration, references = snapshot["configuration"], snapshot["agent_references"]
        elif kind == "board":
            board = self.platform.kanban.get_board(source_id)
            configuration = {key: board.get(key, "") for key in ("name", "description")}
        elif kind == "task":
            board_id = self.platform.repository._id(body.get("board_id"))
            task = self.platform.kanban.get_task(board_id, source_id)
            configuration = {
                key: task.get(key)
                for key in (
                    "title",
                    "description",
                    "assignee",
                    "team_id",
                    "skills",
                    "model_override",
                    "provider_override",
                    "schedule",
                )
            }
            configuration["board_id"] = board_id
            configuration["team_id"] = task.get("team_id") or (task.get("team") or {}).get("id")
            if configuration.get("team_id"):
                team = self.load("agent_team", configuration["team_id"], {})
                configuration["team"] = team["configuration"]
                references = team["agent_references"]
            elif configuration.get("assignee"):
                references = [{"agent_id": configuration["assignee"]}]
        else:
            raise ServiceError("unsupported resource kind")
        bound = {
            "version": 1,
            "kind": kind,
            "source_id": source_id,
            "configuration": configuration,
            "agent_references": references,
        }
        revision = definition_revision(bound)
        if body.get("source_revision") and body["source_revision"] != revision:
            raise ServiceError("source configuration changed", status=409, code="revision_conflict")
        if len(json.dumps(bound).encode()) > 1_000_000:
            raise ServiceError("source definition is too large", status=413)
        return {**bound, "source_revision": revision}
