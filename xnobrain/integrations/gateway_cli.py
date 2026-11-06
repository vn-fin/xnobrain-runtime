"""Private launcher for the Hermes messaging gateway.

Runs the pinned gateway unchanged, but first installs the same workspace
credential and attribution shims Runtime turns use. Hermes multiplexes profiles
by switching ``HERMES_HOME`` per turn, so the launcher must make the scoped
Router key visible to every profile scope and stamp each agent turn with the
active profile's identity.
"""

import os
import sys
import uuid

from .accounting_context import (
    AccountingUnavailable,
    accounting_binding,
    accounting_enabled,
    inference_accounting,
)


def _current_profile():
    from hermes_constants import get_hermes_home

    return get_hermes_home()


def _binding_for_profile(profile):
    """Build the same managed binding the worker uses, or ``None`` if absent."""
    if not accounting_enabled():
        return None
    try:
        return accounting_binding(profile.name)
    except AccountingUnavailable:
        # An unprovisioned workspace still needs to start the gateway; the
        # native provider path will report its own authorization failure.
        return None


def _apply_headers(agent, binding, conversation_id, run_id):
    headers = {
        "X-GoRouter-Agent-Id": binding["agent_id"],
        "X-GoRouter-Conversation-Id": conversation_id,
        "X-GoRouter-Run-Id": run_id,
    }
    for client in (getattr(agent, "client", None), getattr(agent, "async_client", None)):
        if client is not None and hasattr(client, "_custom_headers"):
            client._custom_headers = {**(client._custom_headers or {}), **headers}


def install_gateway_turns():
    """Attach profile identity and accounting to every native agent turn."""
    from run_agent import AIAgent

    if getattr(AIAgent, "_xnobrain_gateway_attribution", False):
        return

    original_init = AIAgent.__init__
    original_run = AIAgent.run_conversation

    def initialize(agent, *args, **kwargs):
        profile = _current_profile()
        binding = _binding_for_profile(profile)
        router_url = os.environ.get("RUNTIME_LLM_ROUTER_URL", "").strip()
        if binding is not None and router_url:
            kwargs.update(
                api_key=binding["user_key"],
                base_url=router_url,
                provider="custom:xnobrain",
            )
        original_init(agent, *args, **kwargs)
        if binding is None:
            return

        # Match Runtime chat and one-shot workers: image rendering and the
        # provider request guard are installed for every agent they construct.
        from .conversation_runner import ConversationRunnerMixin
        from .image_tools import install_image_tools

        install_image_tools(agent, profile / "workspace", [profile / "cache"])
        ConversationRunnerMixin._install_provider_runtime_request_guard(agent)
        agent._xnobrain_gateway_binding = binding
        agent._xnobrain_gateway_conversation = str(agent.session_id or "")
        agent._xnobrain_gateway_run_id = ""

        build = agent._build_api_kwargs

        def attributed_request(messages):
            with inference_accounting(
                binding,
                agent._xnobrain_gateway_conversation,
                agent._xnobrain_gateway_run_id,
            ):
                return build(messages)

        agent._build_api_kwargs = attributed_request

    def run(agent, *args, **kwargs):
        binding = getattr(agent, "_xnobrain_gateway_binding", None)
        if binding is None:
            return original_run(agent, *args, **kwargs)
        run_id = uuid.uuid4().hex
        conversation_id = str(agent.session_id or run_id)
        agent._xnobrain_gateway_run_id = run_id
        agent._xnobrain_gateway_conversation = conversation_id
        _apply_headers(agent, binding, conversation_id, run_id)
        with inference_accounting(binding, conversation_id, run_id):
            return original_run(agent, *args, **kwargs)

    AIAgent.__init__ = initialize
    AIAgent.run_conversation = run
    AIAgent._xnobrain_gateway_attribution = True


def main():
    from .conversation_credentials import install_workspace_router_key_scope

    try:
        # Applied before config load so the gateway's own startup scope and
        # every multiplexed per-turn scope see the workspace Router key.
        install_workspace_router_key_scope()
        install_gateway_turns()
        from hermes_cli.main import main as native_main

        # Run exactly the foreground `hermes gateway run` path.
        sys.argv = ["hermes", "gateway", "run"]
        native_main()
    except SystemExit:
        # Preserve the gateway's restart exit code (for example 75).
        raise
    except Exception as error:
        # Native/provider exceptions may contain request data. Do not echo them.
        print("Messaging gateway failed: " + type(error).__name__, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
