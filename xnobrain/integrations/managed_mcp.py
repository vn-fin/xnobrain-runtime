"""Profile-scoped MCP sessions for managed catalog integrations.

Legacy MCP configuration continues through Hermes native discovery. Managed
catalog servers use an owned ``MCPServerTask`` subclass so native transport
lifecycle and tool handlers are reused while registrations remain profile
scoped.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from tools.mcp_tool import MCPServerTask

from .extension_engine import profile_context, public_https
from .extension_policy import load_state

_GENERATION = re.compile(r"^[a-f0-9]{32}$")
_MANAGED_TASKS: dict[tuple[str, str, str], "ManagedMCPServerTask"] = {}
_PROFILE_LOCKS: dict[str, threading.RLock] = {}
_MANAGER_LOCK = threading.RLock()


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


def _resolved_config(profile: Path, config: dict[str, Any]) -> dict[str, Any]:
    from tools import mcp_tool

    from .extension_engine import _profile_secret_context

    if not isinstance(config, dict) or not isinstance(config.get("url"), str):
        raise ValueError("Managed MCP integrations require an HTTPS endpoint")
    if "command" in config or (config.get("auth") or "").lower() == "oauth":
        raise ValueError("Managed MCP integrations support remote header authentication only")
    public_https(config["url"])
    parsed = urlsplit(config["url"])
    if parsed.query:
        raise ValueError("Managed MCP endpoint query strings are unsupported")

    # Resolve placeholders only from this profile's native secret scope. Never
    # fall back to the Runtime process environment, which may belong to another
    # routed workspace.
    with profile_context(profile), _profile_secret_context(profile):
        resolved = mcp_tool._interpolate_env_vars(config)
    if not isinstance(resolved, dict):
        raise ValueError("Managed MCP configuration is invalid")
    resolved["skip_preflight"] = True
    resolved.pop("transport", None)
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
        headers = dict(config.get("headers") or {})
        headers.setdefault("mcp-protocol-version", mcp_tool.LATEST_PROTOCOL_VERSION)
        timeout = float(config.get("connect_timeout", mcp_tool._DEFAULT_CONNECT_TIMEOUT))

        class PinnedPublicTransport(httpx.AsyncBaseTransport):
            def __init__(self):
                self.transport = httpx.AsyncHTTPTransport(verify=True)

            async def handle_async_request(self, request):
                host, address = public_https(str(request.url))
                pinned_headers = request.headers.copy()
                pinned_headers["Host"] = host
                extensions = dict(request.extensions)
                extensions["sni_hostname"] = host
                pinned = httpx.Request(
                    request.method,
                    request.url.copy_with(host=address),
                    headers=pinned_headers,
                    stream=request.stream,
                    extensions=extensions,
                )
                return await self.transport.handle_async_request(pinned)

            async def aclose(self):
                await self.transport.aclose()

        sampling_kwargs = self._sampling.session_kwargs() if self._sampling else {}
        if self._elicitation:
            sampling_kwargs.update(self._elicitation.session_kwargs())
        if mcp_tool._MCP_NOTIFICATION_TYPES and mcp_tool._MCP_MESSAGE_HANDLER_SUPPORTED:
            sampling_kwargs["message_handler"] = self._make_message_handler()
        if mcp_tool._MCP_LOGGING_CALLBACK_SUPPORTED:
            sampling_kwargs["logging_callback"] = self._make_logging_callback()

        def factory(client_headers=None, client_timeout=None, auth=None):
            if auth is not None:
                raise ValueError("Managed MCP OAuth is unsupported")
            timeout_value = client_timeout
            if timeout_value is None:
                timeout_value = httpx.Timeout(timeout, read=300.0)
            return httpx.AsyncClient(
                headers=client_headers or headers,
                timeout=timeout_value,
                follow_redirects=False,
                transport=PinnedPublicTransport(),
            )

        try:
            async with streamablehttp_client(
                url,
                headers=headers or None,
                timeout=timeout,
                sse_read_timeout=300.0,
                httpx_client_factory=factory,
            ) as (read_stream, write_stream, _session_id):
                async with ClientSession(
                    read_stream,
                    write_stream,
                    **sampling_kwargs,
                ) as session:
                    self.initialize_result = await asyncio.wait_for(
                        session.initialize(), timeout=timeout
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
) -> dict[str, Any]:
    """Connect/update only managed catalog servers for this profile generation."""
    profile = Path(profile)
    profile_identity = _require_generation(profile, generation)
    scope = _profile_scope(profile)
    lock = _profile_lock(scope)
    if not isinstance(managed_servers, dict):
        raise ValueError("Managed MCP server configuration is invalid")

    from tools import mcp_tool

    if not mcp_tool._ensure_mcp_sdk():
        if managed_servers:
            raise RuntimeError("MCP support is unavailable")
        return {"connected": []}

    with lock:
        desired: dict[tuple[str, str, str], tuple[str, dict[str, Any]]] = {}
        for logical_name, raw_config in managed_servers.items():
            if not isinstance(logical_name, str) or not logical_name:
                continue
            server_id = _server_id(profile, generation, logical_name)
            resolved = _resolved_config(profile, raw_config)
            filtered = mcp_tool._filter_suspicious_mcp_servers({server_id: resolved})
            if server_id not in filtered:
                raise ValueError("Managed MCP server configuration was rejected")
            resolved = filtered[server_id]
            material = json.dumps(resolved, sort_keys=True, separators=(",", ":"))
            desired[_task_key(scope, generation, logical_name)] = (
                hashlib.sha256(material.encode()).hexdigest(),
                resolved,
            )

        with _MANAGER_LOCK:
            existing_items = [
                (key, task) for key, task in _MANAGED_TASKS.items() if key[0] == scope
            ]
            for key, task in existing_items:
                target = desired.get(key)
                if target is None or task._config_digest != target[0]:
                    _MANAGED_TASKS.pop(key, None)
                    _stop_task(task)

            connected = []
            for key, (config_digest, config) in desired.items():
                task = _MANAGED_TASKS.get(key)
                if task is None:
                    server_id = _server_id(profile, generation, key[2])
                    task = ManagedMCPServerTask(server_id, scope, generation, key[2])
                    task._config_digest = config_digest
                    _register_task(task)
                    _MANAGED_TASKS[key] = task
                    with profile_context(profile):
                        _start_task(task, config)
                    try:
                        _require_generation(profile, generation)
                    except ValueError:
                        _MANAGED_TASKS.pop(key, None)
                        _stop_task(task)
                        raise
                if task.session is not None:
                    connected.append(key[2])
        return {"connected": connected}


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
        return {
            logical_name: {
                "connected": task.session is not None and task._ready.is_set(),
                "status": "connected"
                if task.session is not None and task._ready.is_set()
                else "unavailable",
                "tools_count": len(task._registered_tool_names),
            }
            for logical_name, task in tasks.items()
        }


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
    """Expose this profile's connected catalog MCP tools to a native agent.

    Hermes selects MCP toolsets only from native ``mcp_servers``. Managed
    servers live under a separate key, so their profile-scoped toolsets are
    added to the agent after construction, like other XNOBrain run tools.
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
