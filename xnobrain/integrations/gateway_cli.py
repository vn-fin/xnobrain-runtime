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

from ..runtime_limits import session_timeout_seconds
from .accounting_context import (
    AccountingUnavailable,
    accounting_binding,
    accounting_enabled,
    inference_accounting,
)

_LOGGER = logging.getLogger(__name__)
_DELEGATED_PLATFORMS = {"zalo", "zalo_bot"}
_TYPING_INTERVAL_SECONDS = 4.0
# The HTTP call must outlive the run's own bounded session timeout. Once the
# Runtime may have accepted a turn, the gateway never starts a native second
# turn; a retry with the same provider message ID replays the accepted run.
_DELEGATION_TIMEOUT_GRACE_SECONDS = 60.0
_DELEGATION_FAILURE_TEXT = "This message could not be completed right now. Please retry shortly."

# Pinned upstream pairing replies (gateway/run.py) start with these prefixes.
PAIRING_REQUEST_TEXT_PREFIX = "Hi~ I don't recognize you yet!"
PAIRING_REQUEST_REPLY = (
    "Xin chào! Bot chưa nhận ra bạn.\n"
    "Yêu cầu trò chuyện của bạn đã được gửi tới chủ bot. "
    "Bạn sẽ nhắn được ngay khi chủ bot duyệt.\n\n"
    "Hello! Your request to chat has been sent to the bot owner. "
    "You can chat as soon as it is approved."
)
PAIRING_LIMIT_TEXT_PREFIX = "Too many pairing requests right now"
PAIRING_LIMIT_REPLY = (
    "Hiện có quá nhiều yêu cầu đang chờ duyệt. Vui lòng thử lại sau.\n\n"
    "Too many requests are waiting for approval. Please try again later."
)

# Pinned upstream home-channel setup notice (gateway/run.py) starts with this.
HOME_CHANNEL_NOTICE_PREFIX = "📬 No home channel is set for"


class _DelegationAttempted(Exception):
    """The request may have reached Runtime; native execution is unsafe."""


_REDACTED_INGRESS_PREFIXES = (
    "inbound message:",
    "telegram topic recovery:",
    "Unauthorized user:",
    "Forwarded update prompt to",
)


class _GatewayIngressRedaction(logging.Filter):
    """Remove user payload/display data from pinned gateway INFO ingress logs."""

    def filter(self, record):
        try:
            message = record.getMessage()
        except Exception:
            record.msg = "gateway ingress event redacted"
            record.args = ()
            return True
        for prefix in _REDACTED_INGRESS_PREFIXES:
            if message.startswith(prefix):
                record.msg = "gateway ingress event redacted"
                record.args = ()
                break
        return True


def install_gateway_log_redaction() -> None:
    """Apply one launcher-owned filter without modifying pinned upstream."""
    logger = logging.getLogger("gateway.run")
    if getattr(logger, "_xnobrain_ingress_redaction", False):
        return
    logger.addFilter(_GatewayIngressRedaction())
    logger._xnobrain_ingress_redaction = True


def _rewrite_pairing_message(text):
    """Replace the pinned gateway's pairing replies, leaving other text alone."""
    if not isinstance(text, str):
        return text
    if text.startswith(PAIRING_REQUEST_TEXT_PREFIX):
        return PAIRING_REQUEST_REPLY
    if text.startswith(PAIRING_LIMIT_TEXT_PREFIX):
        return PAIRING_LIMIT_REPLY
    return text


def _install_pairing_message_rewrite(adapter):
    """Wrap one adapter instance's ``send`` so plugin adapters are covered."""
    if adapter is None or getattr(adapter, "_xnobrain_pairing_send_rewrite", False):
        return adapter
    original_send = getattr(adapter, "send", None)
    if not callable(original_send):
        return adapter

    async def rewritten_send(*args, **kwargs):
        if "text" in kwargs:
            kwargs["text"] = _rewrite_pairing_message(kwargs["text"])
        elif "content" in kwargs:
            kwargs["content"] = _rewrite_pairing_message(kwargs["content"])
        elif len(args) >= 2:
            args = (args[0], _rewrite_pairing_message(args[1]), *args[2:])
        return await original_send(*args, **kwargs)

    try:
        adapter.send = rewritten_send
        adapter._xnobrain_pairing_send_rewrite = True
    except Exception:
        return adapter
    return adapter


def install_gateway_pairing_messages() -> None:
    """Rewrite pinned gateway pairing replies without modifying upstream.

    The pairing flow resolves its adapter through ``_adapter_for_source`` and
    calls ``adapter.send``. Wrapping the resolved adapter instance covers
    platform subclasses that override ``send`` (for example ``zalo_bot``),
    which a ``BasePlatformAdapter.send`` patch would miss.
    """
    try:
        from gateway.run import GatewayRunner
    except Exception:
        _LOGGER.warning("gateway pairing message rewrite unavailable: gateway import failed")
        return
    if getattr(GatewayRunner, "_xnobrain_pairing_messages", False):
        return
    original = GatewayRunner._adapter_for_source

    def adapter_for_source(runner, source):
        return _install_pairing_message_rewrite(original(runner, source))

    GatewayRunner._adapter_for_source = adapter_for_source
    GatewayRunner._xnobrain_pairing_messages = True


def _is_home_channel_notice(text) -> bool:
    return isinstance(text, str) and text.startswith(HOME_CHANNEL_NOTICE_PREFIX)


def install_gateway_operator_notice_filter() -> None:
    """Drop the pinned gateway's operator-only home-channel setup notice.

    The notice is operator UX for configuring Hermes; channel bot users are
    end users, so it must not reach them. Every other notice is delivered
    unchanged with its original arguments.
    """
    try:
        from gateway.run import GatewayRunner
    except Exception:
        _LOGGER.warning("gateway operator notice filter unavailable: gateway import failed")
        return
    if getattr(GatewayRunner, "_xnobrain_operator_notice_filter", False):
        return
    original = GatewayRunner._deliver_platform_notice

    async def deliver_platform_notice(runner, *args, **kwargs):
        text = kwargs.get("content")
        if text is None and len(args) >= 2:
            text = args[1]
        if _is_home_channel_notice(text):
            return None
        return await original(runner, *args, **kwargs)

    GatewayRunner._deliver_platform_notice = deliver_platform_notice
    GatewayRunner._xnobrain_operator_notice_filter = True


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


def _failed_turn_result(message, history, session_id):
    """Return one sanitized reply without starting a second agent turn."""
    return {
        "final_response": _DELEGATION_FAILURE_TEXT,
        "messages": [
            {"role": "user", "content": str(message or "")},
            {"role": "assistant", "content": _DELEGATION_FAILURE_TEXT},
        ],
        "api_calls": 0,
        "tools": [],
        "history_offset": len(history or []),
        "session_id": session_id,
        "response_previewed": False,
    }


async def _run_delegated_turn(runner, message, history, source, session_id, event_message_id):
    import httpx

    token = os.environ.get("RUNTIME_INTERNAL_SERVICE_TOKEN", "").strip()
    payload = _delegation_payload(message, history, source, event_message_id)
    if not token or payload is None:
        # No request was constructed, so native execution is still safe.
        return None
    typing_task = asyncio.create_task(_typing_keepalive(runner, source))
    attempted = False
    try:
        timeout = httpx.Timeout(
            float(session_timeout_seconds()) + _DELEGATION_TIMEOUT_GRACE_SECONDS,
            connect=10.0,
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            attempted = True
            response = await client.post(
                _delegation_endpoint(),
                json=payload,
                headers={"x-xnobrain-internal-token": token},
            )
        if response.status_code != 200:
            raise _DelegationAttempted(f"runtime returned HTTP {response.status_code}")
        try:
            envelope = response.json()
        except ValueError as error:
            raise _DelegationAttempted("runtime returned invalid JSON") from error
        if not isinstance(envelope, dict) or envelope.get("success") is not True:
            raise _DelegationAttempted("runtime returned an error envelope")
        data = envelope.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("reply"), str):
            raise _DelegationAttempted("runtime returned an invalid reply")
    except _DelegationAttempted:
        raise
    except Exception as error:
        if attempted:
            raise _DelegationAttempted(type(error).__name__) from error
        return None
    finally:
        typing_task.cancel()
        # Cleanup must never turn an accepted request into a native retry.
        with suppress(asyncio.CancelledError, Exception):
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
        message_identity = str(event_message_id or getattr(source, "message_id", "") or "").strip()
        if not message_identity:
            # Without a stable provider ID a retry cannot replay one accepted
            # turn, so native execution is unsafe for a delegated platform.
            _LOGGER.warning(
                "channel delegation skipped: provider message identity unavailable; "
                "not retrying natively"
            )
            return _failed_turn_result(message, history, session_id)
        try:
            result = await _run_delegated_turn(
                runner,
                message,
                history,
                source,
                session_id,
                event_message_id,
            )
        except _DelegationAttempted as error:
            # Runtime may already have accepted (and persisted) this turn.
            # Running the native agent now would execute a second turn and could
            # repeat external side effects. Return one sanitized failure; a
            # gateway retry with the same message ID replays the accepted run.
            _LOGGER.warning(
                "channel delegation failed after a request may have been accepted (%s); "
                "not retrying natively",
                type(error).__name__,
            )
            return _failed_turn_result(message, history, session_id)
        except Exception as error:
            _LOGGER.warning(
                "channel delegation setup failed (%s); using native gateway agent",
                type(error).__name__,
            )
            return await original(runner, *args, **kwargs)
        if result is None:
            _LOGGER.warning("channel delegation was not attempted; using native gateway agent")
            return await original(runner, *args, **kwargs)
        return result

    GatewayRunner._run_agent_inner = delegated
    GatewayRunner._xnobrain_channel_delegation = True


def main():
    from .conversation_credentials import install_workspace_router_key_scope

    try:
        # Importing the pinned main applies any -p/--profile/sticky selector
        # before the gateway creates a plugin manager.
        from hermes_cli.main import main as native_main
        from hermes_cli.plugins import get_plugin_manager

        from .plugin_registration_policy import bootstrap, enforce

        # Install policy before any gateway/import path can discover plugins.
        manager = get_plugin_manager()
        bootstrap(manager)

        # Applied before config load so the gateway's own startup scope and
        # every multiplexed per-turn scope see the workspace Router key.
        install_workspace_router_key_scope()
        install_gateway_log_redaction()
        install_gateway_pairing_messages()
        install_gateway_operator_notice_filter()
        from .hermes_pairings import install_gateway_pairing_locking

        install_gateway_pairing_locking()
        install_gateway_turns()
        install_runtime_channel_delegation()
        enforce(manager)

        # Run exactly the foreground `hermes gateway run` path.
        sys.argv = ["hermes", "gateway", "run"]
        native_main()
    except SystemExit:
        # Preserve the gateway's restart exit code (for example 75).
        raise
    except Exception as error:
        if getattr(error, "code", "") == "plugin_capability_forbidden":
            print("plugin_capability_forbidden", file=sys.stderr)
        else:
            # Native/provider exceptions may contain request data. Do not echo them.
            print("Messaging gateway failed: " + type(error).__name__, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
