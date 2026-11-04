"""Profile-scoped MCP sessions for native (in-process) agent turns.

Hermes native discovery registers MCP servers process-globally by name, which
is unsafe when one Runtime process serves many profiles. Both managed catalog
servers and a profile's own ``mcp_servers`` therefore run here as an owned
``MCPServerTask`` subclass, so native transport lifecycle and tool handlers are
reused while registrations remain profile scoped. Separate ``hermes`` CLI
processes still use native discovery for their single profile.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from tools.mcp_tool import MCPServerTask

from .extension_engine import profile_context, public_https
from .extension_policy import load_state, save_state

_LOGGER = logging.getLogger(__name__)
_GENERATION = re.compile(r"^[a-f0-9]{32}$")
_MANAGED_TASKS: dict[tuple[str, str, str], "ManagedMCPServerTask"] = {}
_PROFILE_LOCKS: dict[str, threading.RLock] = {}
_MANAGER_LOCK = threading.RLock()
# Profile-defined servers use a separate logical namespace from catalog
# servers, so the same name in both sources never shares a task or identity.
CUSTOM_PREFIX = "custom:"
# MCP servers declared by enabled portable plugins, keyed by the engine name.
PLUGIN_PREFIX = "plugin:"
_CUSTOM_STATUS: dict[tuple[str, str, str], dict[str, Any]] = {}
_RETRY_BASE_SECONDS = 30.0
_RETRY_MAX_SECONDS = 600.0
_MAX_CONNECT_SECONDS = 20.0


def guarded_httpx_client_factory(default_headers: dict[str, str], connect_timeout: float):
    """Build SDK HTTP clients with the pinned engine's per-connection SSRF checks."""
    import httpx
    from tools import url_safety

    def factory(headers=None, timeout=None, auth=None):
        timeout_value = timeout or httpx.Timeout(connect_timeout, read=300.0)
        return httpx.AsyncClient(
            headers=headers or default_headers,
            timeout=timeout_value,
            follow_redirects=False,
            transport=url_safety.ssrf_safe_async_http_transport(),
            auth=auth,
        )

    return factory


def ensure_profile_generation(profile: Path) -> str:
    """Return this profile's generation, creating one on first MCP use."""
    profile = Path(profile)
    with _MANAGER_LOCK:
        state = load_state(profile)
        generation = state.get("profile_generation")
        if isinstance(generation, str) and _GENERATION.fullmatch(generation):
            return generation
        generation = uuid.uuid4().hex
        state["profile_generation"] = generation
        save_state(profile, state)
        return generation


def _require_generation(profile: Path, generation: str) -> str:
    if not _GENERATION.fullmatch(generation):
        raise ValueError("Managed MCP profile generation is invalid")
    current = load_state(profile).get("profile_generation")
    if current != generation:
        raise ValueError("Managed MCP profile generation changed")
    return str(profile.resolve())


def _profile_scope(profile: Path) -> str:
    from tools.registry import registry

    with profile_context(profile):
        return registry.current_scope_key()


def _server_id(profile: Path, generation: str, logical_name: str) -> str:
    material = f"{profile.resolve()}\0{generation}".encode()
    key = hashlib.sha256(b"xnobrain-managed-mcp-v1\0" + material).digest()
    digest = hmac.new(key, logical_name.encode(), hashlib.sha256).hexdigest()[:32]
    return f"xnb-{digest}"


def _profile_lock(scope: str) -> threading.RLock:
    with _MANAGER_LOCK:
        return _PROFILE_LOCKS.setdefault(scope, threading.RLock())


def _resolved_config(
    profile: Path, config: dict[str, Any], *, interpolate: bool = True
) -> dict[str, Any]:
    from tools import mcp_tool

    from .extension_engine import _profile_secret_context

    if not isinstance(config, dict) or not isinstance(config.get("url"), str):
        raise ValueError("Managed MCP integrations require an HTTPS endpoint")
    oauth = str(config.get("auth") or "").lower() == "oauth"
    catalog_oauth = oauth and isinstance(config.get("xnobrain_catalog_id"), str)
    if "command" in config or (oauth and not catalog_oauth):
        raise ValueError("Managed MCP integrations support remote header authentication only")
    public_https(config["url"])
    parsed = urlsplit(config["url"])
    if parsed.query:
        raise ValueError("Managed MCP endpoint query strings are unsupported")

    if not interpolate:
        # Third-party plugin servers never receive profile secrets: a header
        # such as "${OPENAI_API_KEY}" would send the key to the plugin's host.
        if "${" in json.dumps(config, default=str):
            raise ValueError("Plugin MCP servers may not reference secrets")
        resolved = json.loads(json.dumps(config, default=str))
    else:
        # Resolve placeholders only from this profile's native secret scope.
        # Never fall back to the Runtime process environment, which may belong
        # to another routed workspace.
        with profile_context(profile), _profile_secret_context(profile):
            resolved = mcp_tool._interpolate_env_vars(config)
    if not isinstance(resolved, dict):
        raise ValueError("Managed MCP configuration is invalid")
    # Connecting holds the process-wide manager lock, so a server's own timeout
    # must never stall other agents' turns for long.
    try:
        timeout = float(resolved.get("connect_timeout", _MAX_CONNECT_SECONDS))
    except (TypeError, ValueError):
        timeout = _MAX_CONNECT_SECONDS
    resolved["connect_timeout"] = min(max(timeout, 1.0), _MAX_CONNECT_SECONDS)
    resolved["skip_preflight"] = True
    resolved.pop("transport", None)
    if catalog_oauth:
        resolved["_xnobrain_catalog_oauth"] = True
    return resolved


class _PinnedHTTPSMCPServerTask:
    """Mixin overriding only managed Streamable HTTP transport construction."""

    async def _run_http(self, config: dict[str, Any]):
        """Run the native session lifecycle over DNS-pinned public HTTPS."""
        import httpx
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        from tools import mcp_tool

        url = config["url"]
        public_https(url)
        default_headers = dict(config.get("headers") or {})
        default_headers.setdefault("mcp-protocol-version", mcp_tool.LATEST_PROTOCOL_VERSION)
        connect_timeout = float(config.get("connect_timeout", mcp_tool._DEFAULT_CONNECT_TIMEOUT))
        auth = config.get("_oauth_provider")
        default_headers.pop("_oauth_provider", None)

        sampling_kwargs = self._sampling.session_kwargs() if self._sampling else {}
        if self._elicitation:
            sampling_kwargs.update(self._elicitation.session_kwargs())
        if mcp_tool._MCP_NOTIFICATION_TYPES and mcp_tool._MCP_MESSAGE_HANDLER_SUPPORTED:
            sampling_kwargs["message_handler"] = self._make_message_handler()
        if mcp_tool._MCP_LOGGING_CALLBACK_SUPPORTED:
            sampling_kwargs["logging_callback"] = self._make_logging_callback()

        # Matches mcp.shared._httpx_utils.McpHttpClientFactory, which the SDK
        # calls with keyword arguments.
        factory = guarded_httpx_client_factory(default_headers, connect_timeout)

        try:
            async with streamablehttp_client(
                url,
                headers=default_headers or None,
                timeout=connect_timeout,
                sse_read_timeout=300.0,
                auth=auth,
                httpx_client_factory=factory,
            ) as (read_stream, write_stream, _session_id):
                async with ClientSession(
                    read_stream,
                    write_stream,
                    **sampling_kwargs,
                ) as session:
                    self.initialize_result = await asyncio.wait_for(
                        session.initialize(), timeout=connect_timeout
                    )
                    self.session = session
                    self._mark_lifecycle_started()
                    await self._discover_tools()
                    self._ready.set()
                    mcp_tool._reset_server_error(self.name)
                    self._session_proven = False
                    return await self._wait_for_lifecycle_event()
        except BaseExceptionGroup as error:
            return self._reconnect_or_reraise_group(error)


class ManagedMCPServerTask(_PinnedHTTPSMCPServerTask, MCPServerTask):
    """Native MCP task with profile-scoped tool registration and ownership."""

    __slots__ = (
        "profile_scope",
        "profile_generation",
        "logical_name",
        "_registration_previous",
        "_registration_current",
        "_config_digest",
    )

    def __init__(self, server_id: str, scope: str, generation: str, logical_name: str):
        super().__init__(server_id)
        self.profile_scope = scope
        self.profile_generation = generation
        self.logical_name = logical_name
        self._registration_previous: dict[str, Any] = {}
        self._registration_current: dict[str, Any] = {}

    def _register_profile_tools(self) -> list[str]:
        from tools import mcp_tool
        from tools.registry import registry

        config = self._config
        toolset = f"mcp-{self.name}"
        filter_config = config.get("tools") or {}
        include = mcp_tool._normalize_name_filter(
            filter_config.get("include"), "managed_mcp.tools.include"
        )
        exclude = mcp_tool._normalize_name_filter(
            filter_config.get("exclude"), "managed_mcp.tools.exclude"
        )
        mcp_tool._record_tool_trust_metadata(self.name, config, self._tools)
        check_fn = mcp_tool._make_check_fn(self.name)
        candidates: list[tuple[str, dict[str, Any], Any]] = []

        for tool in self._tools:
            if include and not mcp_tool.matches_name_filter(tool.name, include):
                continue
            if exclude and not include and mcp_tool.matches_name_filter(tool.name, exclude):
                continue
            schema = mcp_tool._convert_mcp_schema(self.name, tool)
            handler = mcp_tool._make_tool_handler(self.name, tool.name, self.tool_timeout)
            candidates.append((schema["name"], schema, handler))

        utility_factories = {
            "list_resources": mcp_tool._make_list_resources_handler,
            "read_resource": mcp_tool._make_read_resource_handler,
            "list_prompts": mcp_tool._make_list_prompts_handler,
            "get_prompt": mcp_tool._make_get_prompt_handler,
        }
        for item in mcp_tool._select_utility_schemas(self.name, self, config):
            schema = item["schema"]
            handler = utility_factories[item["handler_key"]](self.name, self.tool_timeout)
            candidates.append((schema["name"], schema, handler))

        registered = []
        for name, schema, handler in candidates:
            previous = self._registration_previous.get(
                name,
                registry.snapshot_registration(name, scope=self.profile_scope),
            )
            registry.register(
                name=name,
                toolset=toolset,
                schema=schema,
                handler=handler,
                check_fn=check_fn,
                is_async=False,
                description=schema.get("description") or "",
                scope=self.profile_scope,
            )
            current = registry.snapshot_registration(name, scope=self.profile_scope)
            if current is None or current.handler is not handler:
                continue
            self._registration_previous[name] = previous
            self._registration_current[name] = current
            mcp_tool._track_mcp_tool_server(name, self.name)
            registered.append(name)

        return registered

    def _release_profile_tool(self, name: str) -> None:
        from tools import mcp_tool
        from tools.registry import registry

        current = self._registration_current.pop(name, None)
        previous = self._registration_previous.pop(name, None)
        if current is not None:
            registry.restore_registration(
                name,
                current,
                previous,
                scope=self.profile_scope,
            )
        mcp_tool._forget_mcp_tool_server(name)

    def _register_discovered_tools_if_needed(self) -> None:
        if self._registered_tool_names:
            return
        self._registered_tool_names = self._register_profile_tools()

    async def _refresh_tools(self) -> None:
        from tools import mcp_tool

        if not self._advertises_tools():
            return
        async with self._refresh_lock:
            async with self._rpc_lock:
                fresh_tools = await mcp_tool._paginate_full_list(
                    self.session.list_tools,
                    "tools",
                    self.name,
                )
            old_names = set(self._registered_tool_names)
            self._tools = fresh_tools
            new_names = set(self._register_profile_tools())
            for stale_name in old_names - new_names:
                self._release_profile_tool(stale_name)
            self._registered_tool_names = sorted(new_names)

    def _deregister_tools(self) -> None:
        for name in list(self._registered_tool_names):
            self._release_profile_tool(name)
        self._registered_tool_names = []


def _task_key(scope: str, generation: str, logical_name: str):
    return scope, generation, logical_name


def _register_task(task: ManagedMCPServerTask) -> None:
    from tools import mcp_tool

    with mcp_tool._lock:
        owner = mcp_tool._servers.get(task.name)
        if owner is not None and owner is not task:
            raise RuntimeError("Managed MCP server identity collision")
        mcp_tool._server_connecting.add(task.name)
        mcp_tool._servers[task.name] = task


def _remove_task(task: ManagedMCPServerTask) -> None:
    from tools import mcp_tool

    with mcp_tool._lock:
        if mcp_tool._servers.get(task.name) is task:
            mcp_tool._servers.pop(task.name, None)
        mcp_tool._server_connecting.discard(task.name)
        mcp_tool._server_connect_errors.pop(task.name, None)
        mcp_tool._server_trust_levels.pop(task.name, None)
        mcp_tool._tool_read_only_hints.pop(task.name, None)
        mcp_tool._parallel_safe_servers.discard(task.name)


def _start_task(task: ManagedMCPServerTask, config: dict[str, Any]) -> None:
    from tools import mcp_tool

    mcp_tool._ensure_mcp_loop()
    timeout = float(config.get("connect_timeout", 30)) + 30
    try:
        mcp_tool._run_on_mcp_loop(lambda: task.start(config), timeout=timeout)
    except Exception:
        try:
            mcp_tool._run_on_mcp_loop(lambda: task.shutdown(), timeout=15)
        finally:
            _remove_task(task)
        raise
    finally:
        with mcp_tool._lock:
            mcp_tool._server_connecting.discard(task.name)


def _stop_task(task: ManagedMCPServerTask) -> None:
    from tools import mcp_tool

    try:
        if mcp_tool._mcp_loop is not None and mcp_tool._mcp_loop.is_running():
            mcp_tool._run_on_mcp_loop(lambda: task.shutdown(), timeout=15)
        else:
            task._deregister_tools()
    finally:
        _remove_task(task)


def sync_profile_mcp(
    profile: Path,
    generation: str,
    managed_servers: dict[str, dict[str, Any]],
    custom_servers: dict[str, Any] | None = None,
    plugin_servers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Connect/update this profile generation's MCP servers.

    Catalog servers in ``managed_servers`` fail closed: an invalid or
    unreachable one raises. Profile-defined ``custom_servers`` and the
    ``plugin_servers`` of enabled portable plugins are optional: each invalid
    one is skipped as ``rejected`` and each unreachable one as ``unavailable``
    with a retry cooldown, so one bad server never blocks a turn. Plugin
    servers never get secret placeholders resolved. All sources share one
    desired set because stale tasks in the profile scope are stopped.
    """
    profile = Path(profile)
    profile_identity = _require_generation(profile, generation)
    scope = _profile_scope(profile)
    lock = _profile_lock(scope)
    custom_servers = {} if custom_servers is None else custom_servers
    plugin_servers = {} if plugin_servers is None else plugin_servers
    if not all(
        isinstance(item, dict) for item in (managed_servers, custom_servers, plugin_servers)
    ):
        raise ValueError("Managed MCP server configuration is invalid")

    from tools import mcp_tool

    if not mcp_tool._ensure_mcp_sdk():
        if managed_servers:
            raise RuntimeError("MCP support is unavailable")
        return {"connected": []}

    with lock:
        desired: dict[tuple[str, str, str], tuple[str, dict[str, Any]]] = {}
        optional: set[tuple[str, str, str]] = set()
        rejected: set[tuple[str, str, str]] = set()
        sources: dict[tuple[str, str, str], str] = {}
        for logical_name, raw_config in managed_servers.items():
            if not isinstance(logical_name, str) or not logical_name:
                continue
            key = _task_key(scope, generation, logical_name)
            sources[key] = _source_digest(raw_config)
            is_oauth = (
                isinstance(raw_config, dict)
                and str(raw_config.get("auth") or "").lower() == "oauth"
            )
            try:
                resolved = _accepted_config(profile, generation, logical_name, raw_config)
            except Exception:
                if not is_oauth:
                    raise
                _CUSTOM_STATUS[key] = {"status": "needs_sign_in"}
                rejected.add(key)
                continue
            desired[key] = (_config_digest(resolved), resolved)
            if is_oauth:
                optional.add(key)
        optional_sources = (
            (CUSTOM_PREFIX, custom_servers, True),
            (PLUGIN_PREFIX, plugin_servers, False),
        )
        for prefix, servers, interpolate in optional_sources:
            for name, raw_config in servers.items():
                if not isinstance(name, str) or not name.strip():
                    continue
                if isinstance(raw_config, dict) and not mcp_tool._parse_boolish(
                    raw_config.get("enabled", True), default=True
                ):
                    continue
                logical_name = prefix + name
                key = _task_key(scope, generation, logical_name)
                sources[key] = _source_digest(raw_config)
                try:
                    resolved = _accepted_config(
                        profile, generation, logical_name, raw_config, interpolate=interpolate
                    )
                except Exception:
                    _CUSTOM_STATUS[key] = {"status": "rejected"}
                    rejected.add(key)
                    _LOGGER.warning("MCP server %r was rejected for native turns", logical_name)
                    continue
                desired[key] = (_config_digest(resolved), resolved)
                optional.add(key)

        with _MANAGER_LOCK:
            existing_items = [
                (key, task) for key, task in _MANAGED_TASKS.items() if key[0] == scope
            ]
            for key, task in existing_items:
                target = desired.get(key)
                if target is None or task._config_digest != target[0]:
                    _MANAGED_TASKS.pop(key, None)
                    _stop_task(task)
            for key in [key for key in _CUSTOM_STATUS if key[0] == scope]:
                if key not in optional and key not in rejected:
                    _CUSTOM_STATUS.pop(key, None)

            connected = []
            for key, (config_digest, config) in desired.items():
                task = _MANAGED_TASKS.get(key)
                if task is None:
                    previous = _CUSTOM_STATUS.get(key) or {}
                    if (
                        key in optional
                        and previous.get("digest") == config_digest
                        and previous.get("status") != "needs_sign_in"
                        and previous.get("retry_after", 0.0) > time.monotonic()
                    ):
                        continue
                    server_id = _server_id(profile, generation, key[2])
                    task = ManagedMCPServerTask(server_id, scope, generation, key[2])
                    task._config_digest = config_digest
                    try:
                        _register_task(task)
                        _MANAGED_TASKS[key] = task
                        with profile_context(profile):
                            _start_task(task, config)
                    except Exception:
                        _MANAGED_TASKS.pop(key, None)
                        if key not in optional:
                            raise
                        failures = (
                            int(previous.get("failures", 0))
                            if previous.get("digest") == config_digest
                            else 0
                        ) + 1
                        delay = min(_RETRY_BASE_SECONDS * 2 ** (failures - 1), _RETRY_MAX_SECONDS)
                        raw = managed_servers.get(key[2])
                        is_oauth = (
                            isinstance(raw, dict) and str(raw.get("auth") or "").lower() == "oauth"
                        )
                        _CUSTOM_STATUS[key] = {
                            "status": "needs_sign_in" if is_oauth else "unavailable",
                            "digest": config_digest,
                            "failures": failures,
                            "retry_after": time.monotonic() + delay,
                        }
                        _LOGGER.warning(
                            "MCP server %r is unavailable; retrying in %ds",
                            key[2],
                            int(delay),
                        )
                        continue
                    try:
                        _require_generation(profile, generation)
                    except ValueError:
                        _MANAGED_TASKS.pop(key, None)
                        _stop_task(task)
                        raise
                if task.session is not None:
                    connected.append(key[2])
                    if key in optional:
                        _CUSTOM_STATUS[key] = {"status": "connected", "digest": config_digest}
            # Remember which saved config each status describes, so a status
            # is never shown for a config that changed after this turn.
            for key, source in sources.items():
                entry = _CUSTOM_STATUS.get(key)
                if entry is not None:
                    entry["source"] = source
        return {"connected": connected}


def _accepted_config(
    profile: Path,
    generation: str,
    logical_name: str,
    raw_config: Any,
    *,
    interpolate: bool = True,
) -> dict[str, Any]:
    from tools import mcp_tool

    server_id = _server_id(profile, generation, logical_name)
    resolved = _resolved_config(profile, raw_config, interpolate=interpolate)
    if resolved.pop("_xnobrain_catalog_oauth", False):
        from tools.mcp_oauth_manager import get_manager

        redirect_uri = os.environ.get("RUNTIME_MCP_OAUTH_REDIRECT_URI", "").strip()
        if not redirect_uri:
            raise ValueError("Managed MCP OAuth is unavailable")
        stable_name = str(raw_config.get("xnobrain_oauth_name") or "")
        if not stable_name:
            raise ValueError("Managed MCP OAuth identity is invalid")
        with profile_context(profile):
            resolved["_oauth_provider"] = get_manager().get_or_build_provider(
                stable_name,
                str(raw_config["url"]),
                {"redirect_uri": redirect_uri, "client_name": "XNOBrain"},
            )
    filtered = mcp_tool._filter_suspicious_mcp_servers({server_id: resolved})
    if server_id not in filtered:
        raise ValueError("Managed MCP server configuration was rejected")
    return filtered[server_id]


def _config_digest(config: dict[str, Any]) -> str:
    material = json.dumps(
        {key: value for key, value in config.items() if key != "_oauth_provider"},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode()).hexdigest()


def _source_digest(raw_config: Any) -> str:
    material = json.dumps(raw_config, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(material.encode()).hexdigest()


def _unsupported_in_chat(config: dict[str, Any]) -> bool:
    """Return whether a server can never pass the native transport rules.

    Mirrors the network-free checks of ``_resolved_config``; private-address
    rejection needs DNS and is reported after a native turn instead.
    """
    oauth = str(config.get("auth") or "").lower() == "oauth"
    if "command" in config or (oauth and not isinstance(config.get("xnobrain_catalog_id"), str)):
        return True
    url = config.get("url")
    if not isinstance(url, str):
        return True
    parsed = urlsplit(url)
    return parsed.scheme != "https" or not parsed.hostname or bool(parsed.query)


def native_mcp_status(profile: Path, servers: dict[str, Any]) -> dict[str, str]:
    """Return how each profile-defined server behaves in native chat turns.

    ``connected``, ``unavailable`` and ``rejected`` come from the last native
    turn for the same saved config. ``pending`` means no native turn has
    loaded this config yet, and ``disabled`` that it is switched off.
    """
    from tools import mcp_tool

    profile = Path(profile)
    generation = str(load_state(profile).get("profile_generation") or "")
    live: dict[str, dict[str, Any]] = {}
    if _GENERATION.fullmatch(generation):
        scope = _profile_scope(profile)
        with _MANAGER_LOCK:
            live = {
                key[2][len(CUSTOM_PREFIX) :]: dict(entry)
                for key, entry in _CUSTOM_STATUS.items()
                if key[0] == scope
                and key[1] == generation
                and (key[2].startswith(CUSTOM_PREFIX) or not key[2].startswith(PLUGIN_PREFIX))
            }
    result = {}
    for name, config in servers.items():
        if not isinstance(config, dict):
            continue
        if not mcp_tool._parse_boolish(config.get("enabled", True), default=True):
            result[name] = "disabled"
        elif _unsupported_in_chat(config):
            result[name] = "rejected"
        elif (
            (entry := live.get(name))
            and entry.get("source") == _source_digest(config)
            and entry.get("status") == "needs_sign_in"
        ):
            result[name] = "needs_sign_in"
        elif (entry := live.get(name)) and entry.get("source") == _source_digest(config):
            result[name] = str(entry.get("status") or "pending")
        else:
            result[name] = "pending"
    return result


def profile_custom_mcp_status(profile: Path, generation: str) -> dict[str, str]:
    """Return native-turn status for this profile's own ``mcp_servers``."""
    profile = Path(profile)
    _require_generation(profile, generation)
    scope = _profile_scope(profile)
    with _MANAGER_LOCK:
        return {
            key[2][len(CUSTOM_PREFIX) :]: str(entry.get("status") or "unavailable")
            for key, entry in _CUSTOM_STATUS.items()
            if key[0] == scope and key[1] == generation and key[2].startswith(CUSTOM_PREFIX)
        }


def plugin_mcp_status(profile: Path, engine_names: list[str]) -> dict[str, str]:
    """Return native-turn status for portable plugin MCP servers.

    ``engine_names`` are the engine's ``<namespace>__<server>`` names. A
    server no native turn has loaded yet is ``pending``.
    """
    profile = Path(profile)
    generation = str(load_state(profile).get("profile_generation") or "")
    live: dict[str, str] = {}
    if _GENERATION.fullmatch(generation):
        scope = _profile_scope(profile)
        with _MANAGER_LOCK:
            live = {
                key[2][len(PLUGIN_PREFIX) :]: str(entry.get("status") or "pending")
                for key, entry in _CUSTOM_STATUS.items()
                if key[0] == scope and key[1] == generation and key[2].startswith(PLUGIN_PREFIX)
            }
    return {name: live.get(name, "pending") for name in engine_names}


def disconnect_profile_mcp(
    profile: Path,
    generation: str,
    logical_names: list[str] | None = None,
) -> None:
    """Stop selected managed sessions and restore their scoped registrations."""
    profile = Path(profile)
    _require_generation(profile, generation)
    scope = _profile_scope(profile)
    lock = _profile_lock(scope)
    selected = set(logical_names) if logical_names is not None else None
    with lock, _MANAGER_LOCK:
        for key, task in list(_MANAGED_TASKS.items()):
            if key[0] != scope:
                continue
            if selected is not None and key[2] not in selected:
                continue
            _MANAGED_TASKS.pop(key, None)
            _stop_task(task)


def profile_mcp_status(profile: Path, generation: str) -> dict[str, dict[str, Any]]:
    """Return safe connection metadata for this profile's managed servers."""
    profile = Path(profile)
    _require_generation(profile, generation)
    scope = _profile_scope(profile)
    with _MANAGER_LOCK:
        tasks = {
            key[2]: task
            for key, task in _MANAGED_TASKS.items()
            if key[0] == scope and key[1] == generation
        }
        result = {
            logical_name: {
                "connected": task.session is not None and task._ready.is_set(),
                "status": "connected"
                if task.session is not None and task._ready.is_set()
                else "unavailable",
                "tools_count": len(task._registered_tool_names),
            }
            for logical_name, task in tasks.items()
        }
        result.update(
            {
                key[2]: {
                    "connected": False,
                    "status": str(entry.get("status") or "unavailable"),
                    "tools_count": 0,
                }
                for key, entry in _CUSTOM_STATUS.items()
                if key[0] == scope
                and key[1] == generation
                and not key[2].startswith((CUSTOM_PREFIX, PLUGIN_PREFIX))
                and key[2] not in tasks
            }
        )
        return result


def profile_mcp_toolsets(profile: Path, generation: str) -> set[str]:
    """Return active native toolset keys for this profile's managed servers."""
    profile = Path(profile)
    _require_generation(profile, generation)
    scope = _profile_scope(profile)
    with _MANAGER_LOCK:
        return {
            f"mcp-{task.name}"
            for key, task in _MANAGED_TASKS.items()
            if key[0] == scope and key[1] == generation and task._registered_tool_names
        }


def install_tools(agent: Any, profile: Path) -> list[str]:
    """Expose this profile's connected MCP tools to a native agent.

    Native turns never run Hermes MCP discovery, so the profile-scoped
    toolsets of catalog and profile-defined servers are added to the agent
    after construction, like other XNOBrain run tools.
    """
    profile = Path(profile)
    generation = str(load_state(profile).get("profile_generation") or "")
    if not _GENERATION.fullmatch(generation):
        return []
    try:
        toolsets = profile_mcp_toolsets(profile, generation)
    except ValueError:
        return []
    if not toolsets:
        return []

    from model_tools import get_tool_definitions

    with profile_context(profile):
        schemas = get_tool_definitions(
            enabled_toolsets=sorted(toolsets),
            quiet_mode=True,
            skip_tool_search_assembly=True,
        )
    tools = list(getattr(agent, "tools", None) or [])
    existing = {tool.get("function", {}).get("name") for tool in tools}
    added = []
    for schema in schemas:
        name = schema.get("function", {}).get("name")
        if not name or name in existing:
            continue
        tools.append(json.loads(json.dumps(schema)))
        existing.add(name)
        added.append(name)
    agent.tools = tools
    agent.valid_tool_names = set(getattr(agent, "valid_tool_names", None) or set()) | set(added)
    enabled = getattr(agent, "enabled_toolsets", None)
    if enabled is not None:
        agent.enabled_toolsets = sorted(set(enabled) | toolsets)
    return added
