"""Run-scoped effort enforcement at the provider request boundary."""

from __future__ import annotations

import asyncio


def install_reasoning_guard(agent, router, loop, preference, emit):
    """Validate after routing and before serialization; never mutate profile files."""
    from hermes_constants import parse_reasoning_effort

    install_router_reasoning_transport(agent)
    original = agent._build_api_kwargs

    def build(*args, **kwargs):
        async def resolve():
            return await router.reasoning_for_model(str(agent.model))

        future = asyncio.run_coroutine_threadsafe(resolve(), loop)
        try:
            metadata = future.result(timeout=30)
        except Exception:
            future.cancel()
            raise ValueError("reasoning_metadata_unavailable") from None
        effective = preference
        if preference == "auto":
            effective = metadata.get("default_reasoning") or "auto"
            agent.reasoning_config = (
                parse_reasoning_effort(effective) if effective != "auto" else None
            )
        else:
            if preference not in metadata.get("reasoning", []):
                raise ValueError("unsupported_reasoning_effort")
            agent.reasoning_config = parse_reasoning_effort(preference)
        request = original(*args, **kwargs)
        emit(effective)
        return request

    agent._build_api_kwargs = build


def install_router_reasoning_transport(agent):
    """Supply a per-agent profile to Hermes, before native serialization.

    No global provider registration or upstream patching: other agents and
    non-chat transports retain their native behavior.
    """
    from agent.transports.chat_completions import ChatCompletionsTransport
    from providers.base import ProviderProfile

    if getattr(agent, "_xnobrain_reasoning_transport", False):
        return
    get_transport = getattr(agent, "_get_transport", None)
    if not callable(get_transport):
        return

    class RouterProfile(ProviderProfile):
        def build_api_kwargs_extras(self, *, reasoning_config=None, **context):
            if not reasoning_config:
                return {}, {}
            effort = (
                "none"
                if reasoning_config.get("enabled") is False
                else reasoning_config.get("effort")
            )
            if not effort:
                return {}, {}
            # GoRouter's Codex adapter reads the Responses-style object. Use
            # extra_body so the OpenAI chat SDK merges it into the JSON body
            # without receiving an unsupported top-level Python argument.
            if "cx" in str(context.get("model") or "").split("/")[:-1]:
                return {"reasoning": {"effort": effort, "summary": "auto"}}, {}
            return {}, {"reasoning_effort": effort}

    profile = RouterProfile(name="xnobrain-router")

    class RouterTransport:
        def __init__(self, transport):
            self.transport = transport

        def __getattr__(self, name):
            return getattr(self.transport, name)

        def build_kwargs(self, *args, **kwargs):
            kwargs["provider_profile"] = profile
            # Conversation preference owns reasoning, not generic overrides.
            # Remove conflicts before Hermes maps and serializes the config.
            overrides = dict(kwargs.get("request_overrides") or {})
            overrides.pop("reasoning", None)
            overrides.pop("reasoning_effort", None)
            if isinstance(overrides.get("extra_body"), dict):
                extra = dict(overrides["extra_body"])
                extra.pop("reasoning", None)
                extra.pop("reasoning_effort", None)
                overrides["extra_body"] = extra
            kwargs["request_overrides"] = overrides
            return self.transport.build_kwargs(*args, **kwargs)

    def transport_for_request():
        transport = get_transport()
        if isinstance(transport, ChatCompletionsTransport):
            return RouterTransport(transport)
        return transport

    agent._get_transport = transport_for_request
    agent._xnobrain_reasoning_transport = True
