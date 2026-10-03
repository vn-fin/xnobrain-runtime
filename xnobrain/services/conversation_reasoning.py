"""Conversation-only effort preferences and server-owned admission snapshots."""

from __future__ import annotations

from ..feature_flags import enabled
from ..repositories.conversation_reasoning import ConversationReasoningRepository
from .base import ServiceError

FEATURE = "CONVERSATION_REASONING_EFFORT"


def store(service, agent_id, conversation_id):
    return ConversationReasoningRepository(
        service.repository, service.repository.live_profile_path(agent_id), conversation_id
    )


def snapshot(service, agent_id, conversation_id):
    repository = store(service, agent_id, conversation_id)
    with repository.locked():
        preference = repository.read()
        config = service.agents.describe_agent(agent_id)["config"]
        inherited = str(config.get("effort") or config.get("reasoning_effort") or "medium")
        return {
            **preference,
            "effective_preference": preference["reasoning_effort"] or inherited,
            "source": "conversation" if preference["reasoning_effort"] is not None else "agent",
        }


async def validate(router, model, effort):
    if effort in {None, "auto"}:
        return "available"
    if effort == "ultra":
        raise ServiceError(
            "ultra reasoning is unavailable",
            status=422,
            code="unsupported_reasoning_effort",
        )
    try:
        metadata = await router.reasoning_for_model(model)
    except Exception as error:
        raise ServiceError(
            "reasoning metadata is unavailable", status=503, code="reasoning_metadata_unavailable"
        ) from error
    if effort not in metadata.get("reasoning", []):
        raise ServiceError(
            "selected model does not support this reasoning effort",
            status=422,
            code="unsupported_reasoning_effort",
        )
    return "available"


class ConversationReasoningServiceMixin:
    def _require_reasoning(self, agent_id, conversation_id):
        if not enabled(FEATURE):
            raise ServiceError("not found", status=404, code="not_found")
        self.agents.get_conversation(agent_id, conversation_id)

    async def get_conversation_reasoning(self, agent_id, conversation_id):
        self._require_reasoning(agent_id, conversation_id)
        value = snapshot(self, agent_id, conversation_id)
        config = self.agents.describe_agent(agent_id)["config"]
        try:
            status = await validate(
                self.router, config.get("model", "auto"), value["effective_preference"]
            )
        except ServiceError as error:
            status = "unavailable" if error.status == 503 else "unsupported"
        return {**value, "capability_status": status}

    async def update_conversation_reasoning(
        self, agent_id, conversation_id, body, trusted_context=None
    ):
        from ..models.conversations import ConversationReasoningUpdate

        self._require_reasoning(agent_id, conversation_id)
        update = ConversationReasoningUpdate.model_validate(body)
        config = self.agents.describe_agent(agent_id)["config"]
        await validate(self.router, config.get("model", "auto"), update.reasoning_effort)
        from ..repositories.conversation_creation import ConversationCreationRepository

        repository = store(self, agent_id, conversation_id)
        lifecycle = ConversationCreationRepository(
            self.repository, self.repository.live_profile_path(agent_id), agent_id, conversation_id
        )
        with lifecycle.locked():
            self.authorize_conversation(agent_id, conversation_id, trusted_context, active=True)
            self.agents.get_conversation(agent_id, conversation_id)
            repository.update(update.reasoning_effort, update.expected_revision)
        return await self.get_conversation_reasoning(agent_id, conversation_id)
