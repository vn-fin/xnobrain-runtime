"""Explicit stored principal binding; legacy absence is never inferred authority.

Control resolves the workspace and signs the principal. These helpers only check
its existing immutable session binding and do not migrate/backfill ownership.

A channel turn has no signed ``TrustedRequestContext``. It uses
``HostAuthority`` instead: a narrow, host-owned value assembled only after the
Runtime verified a persisted channel owner binding. It never fabricates a
Control signature.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from ..models.conversations import ConversationOwnershipContext
from .base import ServiceError


@dataclass(frozen=True)
class HostAuthority:
    """Verified local owner identity for one host-owned channel turn."""

    subject: str
    tenant_id: str = ""
    organization_id: str = ""
    agent_id: str = ""
    platform: str = ""


def principal_matches(record, trusted) -> bool:
    return (
        isinstance(record, Mapping)
        and bool(getattr(trusted, "subject", ""))
        and record.get("actor_user_id") == trusted.subject
        and "actor_tenant_id" in record
        and isinstance(record["actor_tenant_id"], str)
        and record["actor_tenant_id"] == trusted.tenant_id
    )


def host_principal_matches(record, authority: HostAuthority) -> bool:
    """Require the exact stored host actor, agent, and channel platform."""
    return (
        isinstance(record, Mapping)
        and bool(authority.subject)
        and record.get("actor_user_id") == authority.subject
        and "actor_tenant_id" in record
        and isinstance(record["actor_tenant_id"], str)
        and record["actor_tenant_id"] == authority.tenant_id
        and (not authority.agent_id or record.get("agent_id") == authority.agent_id)
        and (not authority.platform or record.get("channel_platform") == authority.platform)
    )


def require_host_binding(
    files,
    agent,
    session,
    authority: HostAuthority,
    *,
    active: bool = False,
) -> dict:
    """Check a channel conversation/run against its explicit host authority."""
    stored = files.get_conversation_context(files.live_profile_path(agent), session)
    if (
        not host_principal_matches(stored, authority)
        or stored.get("agent_id") != agent
        or (authority.agent_id and authority.agent_id != agent)
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
    return stored


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
        if key not in {"actor_user_id", "actor_tenant_id", "channel_platform"}
    }
