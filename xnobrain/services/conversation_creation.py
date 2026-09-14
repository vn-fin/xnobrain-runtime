"""Recovery of one Control-bound session, never a guessed recent conversation."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping

from ..integrations import AgentAPIError
from ..repositories.conversation_creation import ConversationCreationRepository
from .base import ServiceError


def signed_creation(body: Mapping, trusted) -> str | None:
    intent = body.get("creation_intent")
    if not intent:
        return None
    verified = getattr(trusted, "ownership_context", None)
    if not isinstance(verified, Mapping) or verified.get("creation_intent") != intent:
        raise ServiceError(
            "creation intent was not verified", status=403, code="conversation_context_not_verified"
        )
    conversation = verified.get("conversation_id")
    if not isinstance(conversation, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", conversation
    ):
        raise ServiceError(
            "creation binding is unsupported", status=409, code="conversation_creation_unsupported"
        )
    return conversation


def create_bound(service, agent: str, conversation: str, body: Mapping, context: dict, trusted):
    profile = service._conversation_profile(agent)
    repository = ConversationCreationRepository(service.repository, profile, agent, conversation)
    profile_identity = profile.stat()
    actor = str(getattr(trusted, "subject", "") or "")
    tenant = str(getattr(trusted, "tenant_id", "") or "")
    if not actor:
        raise ServiceError(
            "verified identity required", status=401, code="trusted_context_required"
        )
    material = {
        "subject": actor,
        "profile_identity": [profile_identity.st_dev, profile_identity.st_ino],
        "tenant": tenant,
        "agent": agent,
        "conversation": conversation,
        "intent": body["creation_intent"],
        "context": context,
        "title": body.get("title") or "New Session",
    }
    fingerprint = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with repository.locked():
        receipt = repository.read()
        if receipt is not None:
            if receipt.get("fingerprint") != fingerprint:
                raise ServiceError(
                    "creation input conflicts", status=409, code="conversation_creation_conflict"
                )
            if receipt["state"] == "deleted":
                raise ServiceError(
                    "created conversation was deleted",
                    status=410,
                    code="conversation_creation_deleted",
                )
        else:
            try:
                service.agents.get_conversation(agent, conversation)
            except AgentAPIError as error:
                if error.status != 404:
                    raise
            else:
                raise ServiceError(
                    "session exists without creation receipt",
                    status=409,
                    code="conversation_creation_conflict",
                )
            receipt = {"fingerprint": fingerprint, "state": "pending"}
            repository.save(receipt, new=True)
        stored = service.repository.get_conversation_context(profile, conversation)
        if stored is not None:
            if (
                service._public_context(stored) != service._public_context(context)
                or stored.get("actor_user_id") != actor
                or stored.get("actor_tenant_id", "") != tenant
            ):
                raise ServiceError(
                    "creation binding conflicts", status=409, code="conversation_creation_conflict"
                )
        elif receipt["state"] == "complete":
            raise ServiceError(
                "created conversation is unavailable",
                status=410,
                code="conversation_creation_deleted",
            )
        else:
            # Persist immutable context before inserting the native session.
            service.repository.create_conversation_context(
                profile,
                {
                    **context,
                    "agent_id": agent,
                    "conversation_id": conversation,
                    "actor_user_id": actor,
                    "actor_tenant_id": tenant,
                    "legacy_backfill": False,
                },
            )
        try:
            payload = service.agents.get_conversation(agent, conversation)
        except AgentAPIError as error:
            if error.status != 404:
                raise
            if receipt["state"] == "complete":
                raise ServiceError(
                    "created conversation was deleted",
                    status=410,
                    code="conversation_creation_deleted",
                ) from error
            payload = service.agents.create_conversation(
                agent, {"id": conversation, "title": material["title"]}
            )
        receipt["state"] = "complete"
        repository.save(receipt)
        return service._conversation_dto(agent, payload["conversation"], ownership_context=context)
