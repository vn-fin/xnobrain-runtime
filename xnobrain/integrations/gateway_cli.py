"""Private launcher for the Hermes messaging gateway.

Runs the pinned gateway unchanged, but first installs the same workspace
credential and attribution shims Runtime turns use. Hermes multiplexes profiles
by switching ``HERMES_HOME`` per turn, so the launcher must make the scoped
Router key visible to every profile scope and stamp each agent turn with the
active profile's identity.
"""

import asyncio
import json
import logging
import os
import sys
import threading
import time
import uuid
from contextlib import suppress
from pathlib import Path

from ..runtime_limits import session_timeout_seconds
from .accounting_context import (
    AccountingUnavailable,
    accounting_binding,
    accounting_enabled,
    inference_accounting,
)
from .channel_platforms import group_platform_ids, platform_ids

_LOGGER = logging.getLogger(__name__)
_DELEGATED_PLATFORMS = set(platform_ids())
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

# Zalo personal and Zalo Bot group traffic is owner-approved per group and
# never falls back to the native gateway agent. DM-only slash commands are
# stripped by the adapter.
_GROUP_PLATFORMS = set(group_platform_ids())
_GROUP_CHAT_TYPES = {"group", "forum", "channel", "thread"}
_GROUP_LIMIT_TEXT = (
    "Bạn đã dùng hết {limit} lượt hỏi bot hôm nay. "
    "Hãy quay lại vào ngày mai nhé.\n"
    "You have used all {limit} bot turns for today. "
    "Please come back tomorrow."
)
_GROUP_DEFAULT_LIMIT = 20
_GROUP_MAX_LIMIT = 1000
_GROUP_SEEN_REPORT_SECONDS = 600.0
_GROUP_SEEN_TIMEOUT_SECONDS = 5.0
# Exact pinned Hermes silence marker: the gateway suppresses delivery but keeps
# normal user/assistant transcript alternation.
_SILENT_REPLY = "NO_REPLY"

_GROUP_CACHE_LOCK = threading.RLock()
_GROUP_APPROVED_CACHE: dict[str, tuple[int, int, frozenset[str], int]] = {}
_GROUP_SEEN_LOCK = threading.RLock()
_GROUP_SEEN_STATE: dict[tuple[str, str, str], float] = {}
_GROUP_LIMIT_NOTICE_LOCK = threading.RLock()
_GROUP_LIMIT_NOTICE_STATE: dict[tuple[str, str, str, str], float] = {}
# Repeat the limit notice on every call, but at most once per sender per window
# so a burst of mentions cannot make the bot flood the group.
_GROUP_LIMIT_NOTICE_SECONDS = 30.0


class _DelegationAttempted(Exception):
    """The request may have reached Runtime; native execution is unsafe."""


class _DelegationRefused(Exception):
    """Runtime refused an accepted gateway turn with a known public code."""

    def __init__(self, status_code: int, code: str):
        super().__init__(f"runtime returned HTTP {status_code}")
        self.status_code = status_code
        self.code = code


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


def install_gateway_group_approval() -> None:
    """Authorize Zalo groups by owner approval instead of DM pairing.

    Covers personal ``zalo`` and ``zalo_bot``. Group-level approval is always
    required: a DM-paired sender still cannot chat in an unapproved group.
    Unapproved mentions are reported to Runtime for owner approval, at most
    once per profile/platform/group per interval, without blocking or changing
    the message flow.
    """
    try:
        from gateway.run import GatewayRunner
    except Exception:
        _LOGGER.warning("gateway group approval unavailable: gateway import failed")
        return
    if getattr(GatewayRunner, "_xnobrain_group_approval", False):
        return
    original = getattr(GatewayRunner, "_is_user_authorized", None)
    if not callable(original):
        _LOGGER.warning("gateway group approval unavailable: authorization hook missing")
        return

    def is_user_authorized(runner, source, *args, **kwargs):
        if _is_group_source(source):
            profile_home = _profile_home_for(runner, source)
            platform = _source_platform(source)
            group_id = _group_id(source)
            if profile_home is not None and _group_is_approved(
                profile_home, platform, group_id
            ):
                return True
            if profile_home is not None and group_id:
                _report_group_seen(runner, source, profile_home, group_id)
            return False
        return original(runner, source, *args, **kwargs)

    GatewayRunner._is_user_authorized = is_user_authorized
    GatewayRunner._xnobrain_group_approval = True


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


def _internal_endpoint(path: str) -> str:
    base = os.environ.get("RUNTIME_INTERNAL_URL", "").strip().rstrip("/")
    if not base:
        port = os.environ.get("API_SERVER_PORT", "3000").strip() or "3000"
        base = f"http://127.0.0.1:{port}"
    return base + "/xnobrain/api/runtime/v1/internal/channels/" + path.lstrip("/")


def _delegation_endpoint() -> str:
    return _internal_endpoint("turn")


def _profile_home_for(runner, source) -> Path | None:
    """Return the home of the profile Hermes resolves for this source.

    A multiplex gateway authorizes a turn without the per-profile
    ``HERMES_HOME`` override, so the ambient home can belong to another
    profile. Resolve the source's profile explicitly and fail closed
    (``None``) when its name is not a single safe path segment.
    """
    resolve = getattr(runner, "_adapter_profile_for_source", None)
    if callable(resolve):
        try:
            profile = resolve(source)
        except Exception:
            return None
    else:
        profile = getattr(source, "profile", None)
    if profile is None or str(profile).strip().casefold() == "default":
        import hermes_constants

        process_home = getattr(hermes_constants, "get_process_hermes_home", None)
        if callable(process_home):
            return Path(process_home())
        return Path(hermes_constants.get_default_hermes_root())
    name = str(profile).strip()
    if not name or name in {".", ".."} or any(char in name for char in ("/", "\\", "\0")):
        return None
    from hermes_constants import get_default_hermes_root

    profiles_root = (Path(get_default_hermes_root()) / "profiles").resolve()
    home = (profiles_root / name).resolve()
    try:
        home.relative_to(profiles_root)
    except ValueError:
        return None
    return home


def _group_state_path(profile_home: Path) -> Path:
    return Path(profile_home) / "zalo" / "groups.json"


def _adapter_dm_policy(adapter, env_key: str) -> str:
    """Read one adapter's effective dm_policy from config.extra or its env."""
    extra = getattr(getattr(adapter, "config", None), "extra", None)
    value = extra.get("dm_policy") if isinstance(extra, dict) else None
    if not value:
        value = os.environ.get(env_key, "")
    return str(value or "").strip().lower()


def _discord_wrapper_allows(adapter, *, is_dm: bool, channel_ids) -> bool:
    """Whether the Discord access wrapper may admit an adapter-rejected call.

    Only DMs under the pairing/open policy and guild calls that carry channel
    context (when no role allowlist is set) are relaxed. Voice and every other call without channel context
    keep the adapter's original denial.
    """
    if is_dm:
        return _adapter_dm_policy(adapter, "DISCORD_DM_POLICY") in {"pairing", "open"}
    # A configured role allowlist keeps filtering guild senders before approval.
    return channel_ids is not None and not getattr(adapter, "_allowed_role_ids", None)


def install_gateway_discord_access() -> None:
    """Let Discord pairing and XNOBrain channel approval see unknown senders.

    ``DiscordAdapter._is_allowed_user`` rejects an unknown user before the
    gateway pairing flow or Runtime group approval runs. The wrapper keeps every
    original allow and only turns a rejection into an admission for a DM under
    ``dm_policy: pairing/open`` or a guild call that carries ``channel_ids``
    (the on_message/slash path), so ``install_gateway_group_approval`` decides.
    """
    try:
        from plugins.platforms.discord.adapter import DiscordAdapter
    except Exception:
        _LOGGER.warning("gateway Discord access wrapper unavailable: import failed")
        return
    if getattr(DiscordAdapter, "_xnobrain_access_wrapper", False):
        return
    original = DiscordAdapter._is_allowed_user

    def is_allowed_user(
        self,
        user_id,
        author=None,
        *,
        guild=None,
        is_dm=False,
        channel_ids=None,
    ):
        allowed = original(
            self,
            user_id,
            author,
            guild=guild,
            is_dm=is_dm,
            channel_ids=channel_ids,
        )
        if allowed:
            return True
        return _discord_wrapper_allows(
            self, is_dm=bool(is_dm), channel_ids=channel_ids
        )

    DiscordAdapter._is_allowed_user = is_allowed_user
    DiscordAdapter._xnobrain_access_wrapper = True


def install_gateway_zalo_bot_open_access() -> None:
    """Let the vendored Zalo Bot adapter honor ``dm_policy: open``.

    Its ``DM_POLICIES`` predates the product open policy and coerces unknown
    values to ``disabled`` before the gateway's ``ALLOW_ALL`` grant is read, so
    its DM pre-filter would drop every open-policy DM. The wrapper only relaxes
    a rejection when the profile's configured policy is literally ``open``.
    """
    try:
        from plugins.platforms.zalo_bot.adapter import ZaloAdapter
    except Exception:
        _LOGGER.warning("gateway Zalo Bot open-access wrapper unavailable: import failed")
        return
    if getattr(ZaloAdapter, "_xnobrain_open_access_wrapper", False):
        return
    original = ZaloAdapter._passes_allowlist

    def passes_allowlist(self, user_id):
        if original(self, user_id):
            return True
        return _adapter_dm_policy(self, "ZALO_BOT_DM_POLICY") == "open"

    ZaloAdapter._passes_allowlist = passes_allowlist
    ZaloAdapter._xnobrain_open_access_wrapper = True


def _group_intake_policy(adapter) -> str:
    """Read an adapter's configured group policy, open when it exposes none."""
    policy = getattr(adapter, "_group_policy", None)
    if policy is None:
        access = getattr(adapter, "_access_policy", None)
        policy = getattr(access, "group_policy", None)
        if policy is None:
            policy = getattr(access, "_group_policy", None)
    return str(policy or "").strip().lower()


def _install_adapter_group_wrapper(
    adapter_class, method_name: str = "_is_group_allowed"
) -> None:
    """Admit groups the adapter would drop before Runtime group approval.

    The wrapper keeps ``group_policy: disabled`` exactly disabled. Every other
    policy forwards the group to ``install_gateway_group_approval``, which
    either admits an owner-approved group or reports an unapproved one and
    stays silent.
    """
    if getattr(adapter_class, "_xnobrain_group_intake_wrapper", False):
        return
    original = getattr(adapter_class, method_name, None)
    if not callable(original):
        return

    def is_group_allowed(self, group_id, *args, **kwargs):
        if original(self, group_id, *args, **kwargs):
            return True
        return _group_intake_policy(self) != "disabled"

    setattr(adapter_class, method_name, is_group_allowed)
    adapter_class._xnobrain_group_intake_wrapper = True


def _install_yuanbao_group_intake_wrapper() -> None:
    """Admit Yuanbao groups at the intake middleware only.

    ``AccessPolicy.is_group_allowed`` is also consulted when deciding whether a
    sender may claim the home channel, so the wrapper is installed on
    ``AccessGuardMiddleware.handle`` instead. That leaves DM intake and every
    later action on the original policy.
    """
    try:
        from gateway.platforms.yuanbao import AccessGuardMiddleware
    except Exception:
        _LOGGER.warning("gateway Yuanbao group intake wrapper unavailable: import failed")
        return
    if getattr(AccessGuardMiddleware, "_xnobrain_group_intake_wrapper", False):
        return
    original = AccessGuardMiddleware.handle

    async def handle(self, ctx, next_fn):
        policy = getattr(getattr(ctx, "adapter", None), "_access_policy", None)
        group_code = str(getattr(ctx, "group_code", "") or "")
        if (
            str(getattr(ctx, "chat_type", "") or "").strip().lower() == "group"
            and policy is not None
            and group_code
            and not policy.is_group_allowed(group_code)
            and _group_intake_policy(policy) != "disabled"
        ):
            return await next_fn()
        return await original(self, ctx, next_fn)

    AccessGuardMiddleware.handle = handle
    AccessGuardMiddleware._xnobrain_group_intake_wrapper = True


def install_gateway_group_intake_access() -> None:
    """Unblock WeCom, QQ Bot, and Yuanbao groups for owner approval.

    Their adapters default ``group_policy`` to ``pairing`` and drop every group
    message at intake, before the gateway authorization hook runs. Runtime's
    owner approval is the product authority for groups, so the wrapper only
    removes that intake stop and never marks a group approved itself.
    """
    try:
        from gateway.platforms.qqbot.adapter import QQAdapter
        from plugins.platforms.wecom.adapter import WeComAdapter
    except Exception:
        _LOGGER.warning("gateway group intake wrapper unavailable: import failed")
        return
    _install_adapter_group_wrapper(QQAdapter)
    _install_adapter_group_wrapper(WeComAdapter)
    _install_yuanbao_group_intake_wrapper()


def _group_limit(value) -> int:
    if isinstance(value, bool):
        return _GROUP_DEFAULT_LIMIT
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return _GROUP_DEFAULT_LIMIT
    if not 0 <= limit <= _GROUP_MAX_LIMIT:
        return _GROUP_DEFAULT_LIMIT
    return limit


def _read_group_state(profile_home: Path, platform: str) -> tuple[frozenset[str], int]:
    """Return (approved group IDs, daily limit) for one platform.

    The cache is keyed by file path and platform, so a ``zalo_bot`` entry never
    authorizes the same ID on ``zalo``. Any read or parse failure fails closed:
    no group is approved and the default limit is used.
    """
    path = _group_state_path(profile_home)
    try:
        if path.is_symlink():
            return frozenset(), _GROUP_DEFAULT_LIMIT
        stat = path.stat()
    except FileNotFoundError:
        return frozenset(), _GROUP_DEFAULT_LIMIT
    except OSError:
        return frozenset(), _GROUP_DEFAULT_LIMIT
    key = f"{path}\0{platform}"
    stamp = (int(stat.st_mtime_ns), int(stat.st_size))
    with _GROUP_CACHE_LOCK:
        cached = _GROUP_APPROVED_CACHE.get(key)
        if cached is not None and cached[0] == stamp[0] and cached[1] == stamp[1]:
            return cached[2], cached[3]
    approved: frozenset[str] = frozenset()
    limit = _GROUP_DEFAULT_LIMIT
    try:
        with path.open("r", encoding="utf-8") as file:
            value = json.load(file)
        platforms = value.get("platforms") if isinstance(value, dict) else None
        record = platforms.get(platform) if isinstance(platforms, dict) else None
        if isinstance(record, dict):
            limit = _group_limit(record.get("daily_limit"))
            groups = record.get("groups")
            if isinstance(groups, dict):
                approved = frozenset(
                    str(group_id)
                    for group_id, entry in groups.items()
                    if isinstance(entry, dict) and entry.get("status") == "approved"
                )
    except Exception:
        approved, limit = frozenset(), _GROUP_DEFAULT_LIMIT
    with _GROUP_CACHE_LOCK:
        _GROUP_APPROVED_CACHE[key] = (stamp[0], stamp[1], approved, limit)
    return approved, limit


def _group_is_approved(profile_home: Path, platform: str, group_id: str) -> bool:
    if not group_id:
        return False
    approved, _ = _read_group_state(profile_home, platform)
    return group_id in approved


def _source_chat_type(source) -> str:
    return str(getattr(source, "chat_type", "") or "").strip().lower()


def _source_platform(source) -> str:
    return str(getattr(getattr(source, "platform", None), "value", "") or "").strip().lower()


def _is_group_source(source) -> bool:
    return (
        _source_platform(source) in _GROUP_PLATFORMS
        and _source_chat_type(source) in _GROUP_CHAT_TYPES
    )


def _group_id(source) -> str:
    """Return the id a group is approved and counted under.

    Discord is approved per server, so every channel and thread (often
    auto-created when the bot is mentioned) shares the server's approval and
    daily limit. Elsewhere a thread belongs to its parent chat.
    """
    if _source_platform(source) == "discord":
        guild = str(getattr(source, "scope_id", "") or getattr(source, "guild_id", "") or "")
        if guild:
            return guild
    parent = str(getattr(source, "parent_chat_id", "") or "")
    if parent and _source_chat_type(source) == "thread":
        return parent
    return str(getattr(source, "chat_id", "") or "")


def _response_error_code(envelope) -> str:
    if not isinstance(envelope, dict):
        return ""
    error = envelope.get("error")
    if not isinstance(error, dict):
        return ""
    return str(error.get("code") or "")


async def _post_group_seen(_runner, source, profile_home: Path, group_id: str) -> None:
    """Report one unapproved group mention without blocking or leaking."""
    try:
        token = os.environ.get("RUNTIME_INTERNAL_SERVICE_TOKEN", "").strip()
        if not token:
            return
        platform = _source_platform(source)
        # Personal Zalo carries the group title on the inbound source; the Zalo
        # Bot API has no group metadata lookup, so bot groups report no name.
        group_name = ""
        if platform == "zalo":
            group_name = " ".join(str(getattr(source, "chat_name", "") or "").split())[:100]
        elif platform == "discord":
            # Discord chat names read "Server / #channel[ / thread]".
            server = str(getattr(source, "chat_name", "") or "").split(" / ", 1)[0]
            group_name = " ".join(server.split())[:100]
        payload = {
            "platform": platform,
            "agent_id": Path(profile_home).name,
            "group_id": group_id,
        }
        if group_name:
            payload["group_name"] = group_name
        sender_name = " ".join(
            str(getattr(source, "user_name", "") or "").split()
        )[:100]
        if sender_name:
            payload["sender_name"] = sender_name
        import httpx

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_GROUP_SEEN_TIMEOUT_SECONDS, connect=2.0)
        ) as client:
            await client.post(
                _internal_endpoint("groups/seen"),
                json=payload,
                headers={"x-xnobrain-internal-token": token},
            )
    except Exception as error:
        _LOGGER.debug("group seen report unavailable: %s", type(error).__name__)


def _report_group_seen(runner, source, profile_home: Path, group_id: str) -> None:
    """Schedule at most one group-seen report per profile/platform/group."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    key = (str(profile_home), _source_platform(source), group_id)
    now = time.monotonic()
    with _GROUP_SEEN_LOCK:
        previous = _GROUP_SEEN_STATE.get(key)
        if previous is not None and now - previous < _GROUP_SEEN_REPORT_SECONDS:
            return
        _GROUP_SEEN_STATE[key] = now
    loop.create_task(_post_group_seen(runner, source, profile_home, group_id))


def _group_limit_notice(
    profile_home: Path, platform: str, group_id: str, sender_id: str
) -> str:
    """Return the bilingual limit notice, at most once per sender per window."""
    key = (str(profile_home), platform, group_id, sender_id)
    now = time.monotonic()
    with _GROUP_LIMIT_NOTICE_LOCK:
        for existing, sent in list(_GROUP_LIMIT_NOTICE_STATE.items()):
            if now - sent >= _GROUP_LIMIT_NOTICE_SECONDS:
                _GROUP_LIMIT_NOTICE_STATE.pop(existing, None)
        if key in _GROUP_LIMIT_NOTICE_STATE:
            return ""
        _GROUP_LIMIT_NOTICE_STATE[key] = now
    _, limit = _read_group_state(profile_home, platform)
    return _GROUP_LIMIT_TEXT.format(limit=limit or _GROUP_DEFAULT_LIMIT)


def _silent_turn_result(message, history, session_id):
    """Return an exact Hermes silence marker so nothing is delivered."""
    return {
        "final_response": _SILENT_REPLY,
        "messages": [],
        "api_calls": 0,
        "tools": [],
        "history_offset": len(history or []),
        "session_id": session_id,
        "response_previewed": False,
    }


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
    chat_id = str(getattr(source, "chat_id", "") or "")
    thread_id = _group_id(source) if _is_group_source(source) else chat_id
    sender_id = str(getattr(source, "user_id", "") or chat_id)
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
        "thread_type": "group" if chat_type in _GROUP_CHAT_TYPES else "dm",
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
        try:
            envelope = response.json()
        except ValueError:
            envelope = None
        if response.status_code != 200:
            code = _response_error_code(envelope)
            if response.status_code == 403 and code == "channel_group_not_approved":
                raise _DelegationRefused(response.status_code, code)
            if response.status_code == 429 and code == "channel_group_limit_reached":
                raise _DelegationRefused(response.status_code, code)
            raise _DelegationAttempted(f"runtime returned HTTP {response.status_code}")
        if not isinstance(envelope, dict) or envelope.get("success") is not True:
            raise _DelegationAttempted("runtime returned an error envelope")
        data = envelope.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("reply"), str):
            raise _DelegationAttempted("runtime returned an invalid reply")
    except (_DelegationAttempted, _DelegationRefused):
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
        group_source = _is_group_source(source)
        message_identity = str(event_message_id or getattr(source, "message_id", "") or "").strip()
        if not message_identity:
            if group_source:
                # Groups must never run the native gateway agent, including an
                # empty mention whose provider identity is unavailable.
                return _silent_turn_result(message, history, session_id)
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
        except _DelegationRefused as error:
            if error.code == "channel_group_not_approved":
                return _silent_turn_result(message, history, session_id)
            if error.code == "channel_group_limit_reached":
                profile_home = _profile_home_for(runner, source)
                notice = (
                    _group_limit_notice(
                        profile_home,
                        platform,
                        _group_id(source) if _is_group_source(source) else "@dm",
                        str(getattr(source, "user_id", "") or getattr(source, "chat_id", "") or ""),
                    )
                    if profile_home is not None
                    else ""
                )
                if notice:
                    return {
                        "final_response": notice,
                        "messages": [
                            {"role": "user", "content": str(message or "")},
                            {"role": "assistant", "content": notice},
                        ],
                        "api_calls": 0,
                        "tools": [],
                        "history_offset": len(history or []),
                        "session_id": session_id,
                        "response_previewed": False,
                    }
                return _silent_turn_result(message, history, session_id)
            return _failed_turn_result(message, history, session_id)
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
            if group_source:
                # A group source never falls back to the native gateway agent.
                _LOGGER.warning(
                    "channel delegation setup failed (%s); staying silent for group",
                    type(error).__name__,
                )
                return _silent_turn_result(message, history, session_id)
            _LOGGER.warning(
                "channel delegation setup failed (%s); using native gateway agent",
                type(error).__name__,
            )
            return await original(runner, *args, **kwargs)
        if result is None:
            if group_source:
                return _silent_turn_result(message, history, session_id)
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
        install_gateway_group_approval()
        install_gateway_discord_access()
        install_gateway_zalo_bot_open_access()
        install_gateway_group_intake_access()
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
