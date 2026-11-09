"""Private launcher for the Hermes messaging gateway.

Runs the pinned gateway unchanged, but first installs the same workspace
credential and attribution shims Runtime turns use. Hermes multiplexes profiles
by switching ``HERMES_HOME`` per turn, so the launcher must make the scoped
Router key visible to every profile scope and stamp each agent turn with the
active profile's identity.
"""

import asyncio
import logging
import os
import sys
import time
import uuid
from contextlib import suppress

from .accounting_context import (
    AccountingUnavailable,
    accounting_binding,
    accounting_enabled,
    inference_accounting,
)

_LOGGER = logging.getLogger(__name__)
_DELEGATED_PLATFORMS = {"zalo", "zalo_bot"}
_DELEGATION_TIMEOUT_SECONDS = 900.0
_TYPING_INTERVAL_SECONDS = 4.0


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


def _env_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _delegation_endpoint() -> str:
    base = os.environ.get("RUNTIME_INTERNAL_URL", "").strip().rstrip("/")
    if not base:
        port = os.environ.get("API_SERVER_PORT", "3000").strip() or "3000"
        base = f"http://127.0.0.1:{port}"
    return base + "/xnobrain/api/runtime/v1/internal/channels/turn"


async def _typing_keepalive(runner, source) -> None:
    try:
        adapter = runner._adapter_for_source(source)
    except Exception:
        return
    send_typing = getattr(adapter, "send_typing", None)
    if not callable(send_typing):
        return
    chat_id = str(getattr(source, "chat_id", "") or "")
    if not chat_id:
        return
    metadata = None
    try:
        metadata = runner._thread_metadata_for_source(source)
    except Exception:
        metadata = None
    while True:
        try:
            await send_typing(chat_id, metadata=metadata)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(_TYPING_INTERVAL_SECONDS)


def _delegation_payload(message, history, source, event_message_id):
    from hermes_constants import get_hermes_home

    platform = str(getattr(getattr(source, "platform", None), "value", "") or "")
    thread_id = str(getattr(source, "chat_id", "") or "")
    sender_id = str(getattr(source, "user_id", "") or thread_id)
    message_id = str(event_message_id or getattr(source, "message_id", "") or "")
    text = str(message or "")
    agent_id = get_hermes_home().name
    if (
        platform not in _DELEGATED_PLATFORMS
        or not agent_id
        or not thread_id
        or not sender_id
        or not message_id
        or not text.strip()
    ):
        return None
    chat_type = str(getattr(source, "chat_type", "") or "").strip().lower()
    return {
        "platform": platform,
        "agent_id": agent_id,
        "thread_id": thread_id,
        "thread_type": "group" if chat_type in {"group", "channel", "thread"} else "dm",
        "sender_id": sender_id,
        "sender_name": str(
            getattr(source, "user_name", "") or getattr(source, "chat_name", "") or ""
        ),
        "text": text,
        "message_id": message_id,
        "ts": time.time(),
    }


async def _run_delegated_turn(runner, message, history, source, session_id, event_message_id):
    import httpx

    token = os.environ.get("RUNTIME_INTERNAL_SERVICE_TOKEN", "").strip()
    payload = _delegation_payload(message, history, source, event_message_id)
    if not token or payload is None:
        return None
    typing_task = asyncio.create_task(_typing_keepalive(runner, source))
    try:
        timeout = httpx.Timeout(_DELEGATION_TIMEOUT_SECONDS, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                _delegation_endpoint(),
                json=payload,
                headers={"x-xnobrain-internal-token": token},
            )
        if response.status_code != 200:
            return None
        try:
            envelope = response.json()
        except ValueError:
            return None
        if not isinstance(envelope, dict) or envelope.get("success") is not True:
            return None
        data = envelope.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("reply"), str):
            return None
    finally:
        typing_task.cancel()
        with suppress(asyncio.CancelledError):
            await typing_task
    reply = data["reply"]
    return {
        "final_response": reply,
        "messages": [
            {"role": "user", "content": str(message or "")},
            {"role": "assistant", "content": reply},
        ],
        "api_calls": 1,
        "tools": [],
        "history_offset": len(history or []),
        "session_id": session_id,
        "response_previewed": False,
    }


def install_runtime_channel_delegation() -> None:
    """Route authorized Zalo gateway turns through the main Runtime process."""
    if not _env_enabled("RUNTIME_GATEWAY_CHANNEL_DELEGATION"):
        return
    try:
        from gateway.run import GatewayRunner
    except Exception:
        _LOGGER.warning("channel delegation unavailable: gateway import failed")
        return
    if getattr(GatewayRunner, "_xnobrain_channel_delegation", False):
        return
    original = GatewayRunner._run_agent_inner

    async def delegated(runner, *args, **kwargs):
        message = kwargs.get("message", args[0] if len(args) > 0 else "")
        history = kwargs.get("history", args[2] if len(args) > 2 else [])
        source = kwargs.get("source", args[3] if len(args) > 3 else None)
        session_id = kwargs.get("session_id", args[4] if len(args) > 4 else "")
        event_message_id = kwargs.get("event_message_id")
        platform = str(getattr(getattr(source, "platform", None), "value", "") or "")
        if platform not in _DELEGATED_PLATFORMS:
            return await original(runner, *args, **kwargs)
        try:
            result = await _run_delegated_turn(
                runner,
                message,
                history,
                source,
                session_id,
                event_message_id,
            )
        except Exception as error:
            _LOGGER.warning(
                "channel delegation failed (%s); using native gateway agent",
                type(error).__name__,
            )
            return await original(runner, *args, **kwargs)
        if result is None:
            _LOGGER.warning("channel delegation was unavailable; using native gateway agent")
            return await original(runner, *args, **kwargs)
        return result

    GatewayRunner._run_agent_inner = delegated
    GatewayRunner._xnobrain_channel_delegation = True


def main():
    from .conversation_credentials import install_workspace_router_key_scope

    try:
        # Applied before config load so the gateway's own startup scope and
        # every multiplexed per-turn scope see the workspace Router key.
        install_workspace_router_key_scope()
        install_gateway_turns()
        install_runtime_channel_delegation()
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
