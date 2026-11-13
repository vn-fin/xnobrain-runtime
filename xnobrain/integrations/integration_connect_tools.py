"""Read-only Big Brother tool that requests an integration connect card."""

import copy
import ipaddress
import json
import re
import threading
import uuid
from contextlib import contextmanager
from difflib import get_close_matches
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..defaults import BIG_BROTHER_AGENT_ID
from ..services.base import ServiceError
from ..trusted_context import TrustedRequestContext

_LOCK = threading.RLock()
_BINDINGS = {}
_SCHEMAS = {}

TOOL_NAME = "xnobrain_integration_connect"
TOOLSET = "xnobrain_integration_connect"
CONNECT_KINDS = {"catalog_mcp", "plugin_mcp", "custom_mcp", "manage_mcp"}
AUTH_KINDS = {"oauth", "api_key", "none"}
SERVER_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
MAX_CATALOG_CANDIDATES = 5
CONNECT_GUIDANCE = (
    "When the user asks to add, connect or sign in to an MCP server, integration, "
    "connector or app (for example Notion or Todoist), call xnobrain_integration_connect "
    "first. Try kind='catalog_mcp' with catalog_id set to the lowercase app name "
    "first (for example 'composio'); the XNOBrain catalog is not the Hermes plugin "
    "index, so do not use `hermes plugins search` to decide. If the result is "
    "catalog_unknown, retry with one of its candidates; use kind='custom_mcp' only "
    "when no candidate fits, and it accepts an optional server_name. To list or "
    "disconnect an agent's MCP servers, use kind='manage_mcp', but only when the "
    "user asks for that; never add it to an add or connect request. It shows a connect "
    "card where the user enters credentials or signs in. Do not use `agent mcp add`, "
    "the terminal or config edits for this, and never ask for API keys or tokens in "
    "chat."
)
INSTRUCTION = (
    "A connect card is shown in the chat. Ask the user to finish there. "
    "Never ask for credentials in chat."
)


class IntegrationConnectArguments(BaseModel):
    """Arguments accepted by the connect tool; never contains a secret field."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["catalog_mcp", "plugin_mcp", "custom_mcp", "manage_mcp"]
    agent_id: str | None = Field(default=BIG_BROTHER_AGENT_ID)
    catalog_id: str | None = None
    plugin_id: str | None = None
    server_name: str | None = None
    url: str | None = None
    reason: str | None = Field(default=None, max_length=200)


class _ConnectError(Exception):
    def __init__(self, code: str, candidates: list[str] | None = None):
        super().__init__(code)
        self.code = code
        self.candidates = candidates


def _error(code: str, candidates: list[str] | None = None) -> str:
    error: dict[str, object] = {"code": code}
    if candidates is not None:
        error["candidates"] = candidates
    return json.dumps({"success": False, "error": error})


def call(name, args, **kwargs):
    session = str(kwargs.get("session_id") or "")
    with _LOCK:
        binding = _BINDINGS.get(session)
        if binding is None:
            return _error("integration_connect_authority_required")
        service, trusted = binding
    try:
        request = IntegrationConnectArguments.model_validate(args or {})
        payload = _connect_request(service, request, trusted)
    except ValidationError:
        return _error("invalid_integration_connect_request")
    except _ConnectError as error:
        return _error(error.code, error.candidates)
    except ServiceError as error:
        return _error(error.code or "integration_connect_failed")
    except Exception:
        # Never expose exception text or argument values through the tool result.
        return _error("integration_connect_failed")
    return json.dumps(
        {"success": True, "connect_request": payload, "instruction": INSTRUCTION},
        ensure_ascii=False,
    )


def _connect_request(service, request: IntegrationConnectArguments, trusted) -> dict:
    target = str(request.agent_id or BIG_BROTHER_AGENT_ID).strip()
    if not target:
        raise _ConnectError("agent_not_found")
    try:
        service.connect_target(target, trusted)
    except ServiceError as error:
        if error.code in {"not_found", "agent_not_found"}:
            raise _ConnectError("agent_not_found") from error
        raise

    if request.kind == "catalog_mcp":
        payload = _catalog_request(service, request, target)
    elif request.kind == "plugin_mcp":
        payload = _plugin_request(service, request, target, trusted)
    elif request.kind == "manage_mcp":
        payload = _manage_request(target)
    else:
        payload = _custom_request(request, target)

    reason = str(request.reason or "").strip()[:200]
    if reason:
        payload["reason"] = reason
    return payload


def _catalog_request(service, request: IntegrationConnectArguments, target: str) -> dict:
    catalog = service.catalog()
    entries = catalog.get("entries")
    if not isinstance(entries, list):
        raise _ConnectError("catalog_unknown")
    catalog_id = str(request.catalog_id or "").strip()
    entry = next(
        (item for item in entries if isinstance(item, dict) and item.get("id") == catalog_id),
        None,
    )
    if entry is None:
        raise _ConnectError("catalog_unknown", _catalog_candidates(entries, catalog_id))
    if not _catalog_installable(entry, catalog):
        raise _ConnectError("catalog_unsupported")

    mcp = entry.get("mcp") if isinstance(entry.get("mcp"), dict) else {}
    auth = mcp.get("auth") if isinstance(mcp.get("auth"), dict) else {}
    auth_kind = str(auth.get("kind") or "none")
    if auth_kind not in AUTH_KINDS:
        raise _ConnectError("catalog_unsupported")
    recipe_revision = entry.get("recipe_revision")
    payload = {
        "id": uuid.uuid4().hex,
        "kind": "catalog_mcp",
        "agent_id": target,
        "display_name": str(entry.get("name") or entry.get("id"))[:200],
        "auth_kind": auth_kind,
        "catalog_id": str(entry.get("id"))[:200],
    }
    if isinstance(recipe_revision, int) and not isinstance(recipe_revision, bool):
        payload["recipe_revision"] = recipe_revision
    return payload


def _plugin_request(service, request: IntegrationConnectArguments, target: str, trusted) -> dict:
    plugin_id = str(request.plugin_id or "").strip()
    server_name = str(request.server_name or "").strip()
    if not plugin_id or not server_name:
        raise _ConnectError("plugin_mcp_unknown")
    inventory = service.list_plugins(target, trusted, recover_publication=False)
    plugins = inventory.get("plugins") if isinstance(inventory, dict) else None
    if not isinstance(plugins, list):
        raise _ConnectError("plugin_mcp_unknown")
    plugin = next(
        (item for item in plugins if isinstance(item, dict) and item.get("id") == plugin_id),
        None,
    )
    if plugin is None:
        raise _ConnectError("plugin_mcp_unknown")
    servers = plugin.get("mcp_servers")
    server = None
    if isinstance(servers, list):
        server = next(
            (
                item
                for item in servers
                if isinstance(item, dict) and item.get("name") == server_name
            ),
            None,
        )
    if server is None:
        raise _ConnectError("plugin_mcp_unknown")

    auth_kind = str(server.get("auth_kind") or "none")
    if auth_kind not in AUTH_KINDS:
        auth_kind = "none"
    payload = {
        "id": uuid.uuid4().hex,
        "kind": "plugin_mcp",
        "agent_id": target,
        "display_name": str(plugin.get("name") or server_name)[:200],
        "auth_kind": auth_kind,
        "plugin_id": plugin_id[:200],
        "server_name": server_name[:200],
    }
    plugin_revision = server.get("plugin_revision")
    if isinstance(plugin_revision, str) and plugin_revision:
        payload["plugin_revision"] = plugin_revision[:200]
    elif isinstance(plugin_revision, int) and not isinstance(plugin_revision, bool):
        payload["plugin_revision"] = plugin_revision
    return payload


def _custom_request(request: IntegrationConnectArguments, target: str) -> dict:
    url = str(request.url or "").strip()
    if len(url) > 200:
        raise _ConnectError("invalid_url")
    host = _validated_public_https(url)
    if not host:
        raise _ConnectError("invalid_url")
    return {
        "id": uuid.uuid4().hex,
        "kind": "custom_mcp",
        "agent_id": target,
        "display_name": host[:200],
        "auth_kind": "none",
        "server_name": _custom_server_name(request.server_name, host),
        "url": url,
    }


def _manage_request(target: str) -> dict:
    return {
        "id": uuid.uuid4().hex,
        "kind": "manage_mcp",
        "agent_id": target,
        "display_name": target[:200],
    }


def _custom_server_name(raw_name: str | None, host: str) -> str:
    name = str(raw_name or "").strip()
    if name:
        if not SERVER_NAME_RE.fullmatch(name):
            raise _ConnectError("invalid_server_name")
        return name
    derived = re.sub(r"[^a-z0-9_-]", "-", host.lower()).strip("-")
    derived = derived[:64].strip("-")
    if not derived:
        raise _ConnectError("invalid_server_name")
    return derived


def _validated_public_https(url: str) -> str:
    if not url or len(url) > 2000:
        return ""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return ""
    host = parsed.hostname.rstrip(".").lower()
    if not host or host in {"localhost", "localhost.localdomain"} or "." not in host:
        return ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    return host if address.is_global else ""


def _catalog_installable(entry: dict, catalog: dict) -> bool:
    if entry.get("installability") != "installable":
        return False
    capability = entry.get("capability")
    if isinstance(capability, dict) and capability.get("installable") is False:
        return False
    engine = catalog.get("engine_capabilities")
    if isinstance(engine, dict) and engine.get("mcp_profile_isolation") is False:
        return False
    mcp = entry.get("mcp")
    return isinstance(mcp, dict) and mcp.get("transport") in {"http", "stdio"}


def _catalog_candidates(entries: list, raw_id: str) -> list[str]:
    mapping = {}
    candidates = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_id = entry.get("id")
        if not isinstance(entry_id, str) or not entry_id:
            continue
        candidates.append(entry_id)
        name = entry.get("name")
        if isinstance(name, str) and name.strip():
            key = name.strip().casefold()
            mapping.setdefault(key, entry_id)
            candidates.append(key)
    matches = get_close_matches(str(raw_id or "").casefold(), candidates, n=12, cutoff=0.4)
    result: list[str] = []
    for match in matches:
        resolved = mapping.get(match, match)
        if resolved not in result:
            result.append(resolved)
        if len(result) >= MAX_CATALOG_CANDIDATES:
            break
    return result


def _function_schema() -> dict:
    return {
        "name": TOOL_NAME,
        "description": (
            "Use this FIRST whenever the user asks to add, connect or sign in to an MCP "
            "server, integration, connector or app. Try kind='catalog_mcp' with "
            "catalog_id (lowercase app name, e.g. 'composio') first; on catalog_unknown "
            "retry with a returned candidate, and use kind='custom_mcp' only when nothing "
            "fits; custom_mcp accepts an optional server_name. To list or disconnect an "
            "agent's MCP servers, use kind='manage_mcp', only when the user asks for that "
            "and never alongside an add or connect request. The XNOBrain catalog is not the "
            "Hermes plugin index. It shows a connect card in the chat "
            "where the user enters credentials or signs in themselves, so never use "
            "`agent mcp add` or ask for keys in chat. Read-only: it never installs, "
            "never changes settings, and never accepts credentials."
        ),
        "parameters": IntegrationConnectArguments.model_json_schema(),
    }


def register_tools(profile_dir):
    """Register the tool in the profile's registry scope.

    The engine rebuilds ``agent.tools`` from the registry between turns and drops
    tools appended by hand, so the tool must live in the registry. A profile scope
    keeps it out of every other agent's tool list; unbound runs get an error result.
    """
    from hermes_constants import hermes_home_key
    from tools.registry import registry

    scope = hermes_home_key(profile_dir)
    with _LOCK:
        function = _function_schema()
        _SCHEMAS[TOOL_NAME] = {"type": "function", "function": function}
        if registry.get_entry(TOOL_NAME, scope=scope) is not None:
            return
        registry.register(
            name=TOOL_NAME,
            toolset=TOOLSET,
            schema=function,
            handler=lambda args, _name=TOOL_NAME, **kw: call(_name, args, **kw),
            description=function["description"],
            scope=scope,
        )


@contextmanager
def bind_run(service, session, trusted):
    if not isinstance(trusted, TrustedRequestContext) or not trusted.subject:
        yield False
        return
    session = str(session or "")
    with _LOCK:
        if session in _BINDINGS:
            raise ServiceError(
                "Session already bound", status=409, code="integration_connect_session_busy"
            )
        _BINDINGS[session] = (service, trusted)
    try:
        yield True
    finally:
        with _LOCK:
            _BINDINGS.pop(session, None)


def install_tools(agent, session, profile_dir):
    with _LOCK:
        if session not in _BINDINGS or int(getattr(agent, "_delegate_depth", 0) or 0):
            return
    register_tools(profile_dir)
    with _LOCK:
        schemas = copy.deepcopy(list(_SCHEMAS.values()))
    existing = {tool.get("function", {}).get("name") for tool in getattr(agent, "tools", [])}
    agent.tools = list(getattr(agent, "tools", []) or []) + [
        tool for tool in schemas if tool["function"]["name"] not in existing
    ]
    agent.valid_tool_names = set(getattr(agent, "valid_tool_names", set())) | set(_SCHEMAS)
    # Without the toolset the between-turns refresh filters the registry tool out.
    enabled = getattr(agent, "enabled_toolsets", None)
    if enabled is not None and TOOLSET not in enabled:
        agent.enabled_toolsets = sorted({*enabled, TOOLSET})
