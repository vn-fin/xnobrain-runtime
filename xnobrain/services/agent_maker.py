"""Automatic Agent Maker lifecycle scoped to a verified, active chat run."""

import asyncio
import hashlib
import json
from dataclasses import replace

from ..models.agent_maker import MakerBuild, MakerInspect, MakerPrepare
from ..models.conversations import ConversationOwnershipContext
from ..repositories.base import StoreError
from .base import ServiceError
from .conversation_authority import require_run


class AgentMakerService:
    def __init__(self, platform):
        self.platform = platform
        self.repository = platform.repository

    def authority(self, agent, session, run_id, trusted):
        run = self.repository.get_conversation_run(agent, session, run_id)
        context = require_run(self.repository, agent, session, run, trusted)
        if run["status"] not in {"queued", "running"} or (run.get("cancellation") or {}).get(
            "requested"
        ):
            raise ServiceError(
                "Chat run is no longer active", status=403, code="agent_maker_run_inactive"
            )
        # Use the immutable server-verified session context. It is never an LLM argument.
        ownership = self.platform._public_context(context)
        return replace(trusted, ownership_context=ownership)

    @staticmethod
    def _same_context(record, trusted):
        context = trusted.ownership_context
        stored = record.get("ownership_context") or {}
        if record["work_context_id"] != context["id"]:
            return False
        fields = set(ConversationOwnershipContext.model_fields) - {"owner_label"}
        if context["id"] == "personal":
            fields -= {"revocation_version"}
        return all(stored.get(key) == context.get(key) for key in fields)

    def _record(self, agent, blueprint_id, trusted):
        record = self.platform.get_agent_blueprint(agent, blueprint_id)
        if not self._same_context(record, trusted):
            raise ServiceError(
                "Blueprint belongs to another context",
                status=403,
                code="blueprint_context_not_verified",
            )
        return record

    async def execute(self, name, args, *, agent, session, run_id, trusted):
        def guard():
            return self.authority(agent, session, run_id, trusted)

        verified = guard()
        if name == "agent_maker_inspect":
            body = MakerInspect.model_validate(args)
            if body.blueprint_id:
                return self._record(agent, body.blueprint_id, verified)
            records = self.platform.list_agent_blueprints(agent)["blueprints"]
            return {
                "auto_accept": True,
                "prepare_schema": MakerPrepare.model_json_schema(),
                "build_schema": MakerBuild.model_json_schema(),
                "blueprints": [
                    {
                        key: item[key]
                        for key in (
                            "id",
                            "intent",
                            "revision",
                            "status",
                            "canonical_digest",
                            "target_profile_id",
                        )
                    }
                    for item in records
                    if self._same_context(item, verified)
                ][:50],
            }
        if name == "agent_maker_prepare":
            body = MakerPrepare.model_validate(args)
            data = body.model_dump(mode="json")
            if body.blueprint.ownership != verified.ownership_context["owner_kind"]:
                raise ServiceError(
                    "Blueprint ownership must match the chat",
                    code="blueprint_context_not_verified",
                    status=403,
                )
            if body.blueprint_id:
                current = self._record(agent, body.blueprint_id, verified)
                # A lost response to an identical edit must not increment the revision again.
                if current["revision"] == body.expected_revision + 1 and all(
                    current[key] == data[key] for key in ("intent", "blueprint")
                ):
                    return current
                return self.platform.patch_agent_blueprint(
                    agent,
                    body.blueprint_id,
                    {key: data[key] for key in ("intent", "blueprint", "expected_revision")},
                )
            material = [agent, session, trusted.subject, trusted.tenant_id, body.idempotency_key]
            identifier = "abp_" + hashlib.sha256(json.dumps(material).encode()).hexdigest()[:32]
            with self.repository._lock:
                try:
                    current = self._record(agent, identifier, verified)
                except StoreError as error:
                    if error.code != "not_found":
                        raise
                else:
                    if any(current[key] != data[key] for key in ("intent", "blueprint")):
                        raise ServiceError(
                            "Preparation key was reused",
                            status=409,
                            code="blueprint_idempotency_conflict",
                        )
                    return current
                return self.platform.create_agent_blueprint(
                    agent,
                    {
                        "intent": body.intent,
                        "blueprint": data["blueprint"],
                        "work_context_id": verified.ownership_context["id"],
                    },
                    verified,
                    record_id=identifier,
                )
        if name != "agent_maker_build":
            raise ValueError("Unknown Agent Maker operation")
        body = MakerBuild.model_validate(args)
        data = body.model_dump(mode="json", exclude={"blueprint_id"})
        current = self._record(agent, body.blueprint_id, verified)
        self.platform._check_lifecycle_request(current, data)
        if current["status"] == "blueprint_ready":
            current = self.platform.approve_agent_blueprint(
                agent,
                current["id"],
                {
                    **data,
                    "decision": "approve",
                },
                verified.subject,
                approval_mode="auto",
            )
        scaffold_request = {
            "expected_revision": body.expected_revision,
            "canonical_digest": body.canonical_digest,
            "idempotency_key": f"auto-scaffold:{current['id']}:{body.expected_revision}",
            "decision": "scaffold",
        }
        guard()
        if current["status"] in {"approved", "scaffolding"}:
            current = self.platform.scaffold_agent_blueprint(
                agent, current["id"], scaffold_request, verified.subject
            )
        if current["status"] in {"scaffolded", "certification_failed", "certifying"}:
            # Recheck revocation/Stop while child certification is awaiting inference.
            task = asyncio.create_task(
                self.platform.certify_agent_blueprint(agent, current["id"], data, verified)
            )
            try:
                while not task.done():
                    await asyncio.wait({task}, timeout=0.25)
                    guard()
                current = await task
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        guard()
        if current["status"] == "ready_to_activate":
            current = self.platform.activate_agent_blueprint(
                agent,
                current["id"],
                {
                    **scaffold_request,
                    "decision": "activate",
                    "idempotency_key": f"auto-activate:{current['id']}:{body.expected_revision}",
                    "certification_digest": current["certification"]["certification_digest"],
                },
                verified.subject,
            )
        return current
