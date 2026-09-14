"""Explicit stored principal binding; legacy absence is never inferred authority.

Control resolves the workspace and signs the principal. These helpers only check
its existing immutable session binding and do not migrate/backfill ownership.
"""

from collections.abc import Mapping

from ..models.conversations import ConversationOwnershipContext
from .base import ServiceError


def principal_matches(record, trusted) -> bool:
    return (
        isinstance(record, Mapping)
        and bool(getattr(trusted, "subject", ""))
        and record.get("actor_user_id") == trusted.subject
        and "actor_tenant_id" in record
        and isinstance(record["actor_tenant_id"], str)
        and record["actor_tenant_id"] == trusted.tenant_id
    )


def require_binding(files, agent, session, trusted, *, active=False, personal=False, expected=None):
    stored = files.get_conversation_context(files.live_profile_path(agent), session)
    if (
        not principal_matches(stored, trusted)
        or stored.get("agent_id") != agent
        or stored.get("legacy_backfill") is True
    ):
        raise ServiceError(
            "Conversation identity is unavailable; use a newly verified conversation",
            status=403,
            code="conversation_owner_forbidden",
        )
    if active and stored.get("state") != "active":
        raise ServiceError(
            "Conversation does not permit new work",
            status=403,
            code="conversation_context_inactive",
        )
    if personal and (
        stored.get("owner_kind") != "personal"
        or stored.get("payer_kind") != "personal"
        or stored.get("organization_id")
        or stored.get("sponsor_grant_id")
    ):
        raise ServiceError(
            "Personal conversation required",
            status=403,
            code="custom_page_personal_conversation_required",
        )
    if expected is not None and any(
        stored.get(key) != expected.get(key) for key in ConversationOwnershipContext.model_fields
    ):
        raise ServiceError(
            "Conversation binding changed", status=403, code="conversation_context_conflict"
        )
    return stored


def require_run(files, agent, session, record, trusted, *, personal=False):
    if (
        not principal_matches(record, trusted)
        or record.get("agent_id") != agent
        or record.get("conversation_id") != session
    ):
        raise ServiceError(
            "Run identity is unavailable", status=403, code="conversation_owner_forbidden"
        )
    return require_binding(
        files,
        agent,
        session,
        trusted,
        active=True,
        personal=personal,
        expected=record.get("ownership_context") or {},
    )


def public_run(record):
    if not isinstance(record, Mapping):
        return record
    return {
        key: value
        for key, value in record.items()
        if key not in {"actor_user_id", "actor_tenant_id"}
    }
