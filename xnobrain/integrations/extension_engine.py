"""Adapters for the pinned engine's extension APIs and public Git artifacts."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from .extension_policy import (
    MANAGED_MCP_KEY,
    PLUGIN_ID,
    REQUIRED_PLUGINS,
    atomic_json,
    fail,
    load_state,
    read_config,
    safe_child,
    tree_digest,
    trusted_required,
    validate_profile_extensions,
)

_PLUGIN_REFRESH_LOCK = threading.RLock()
_PLUGIN_GENERATIONS: dict[str, str] = {}


def refresh_profile_plugins(profile: Path) -> None:
    """Apply changed plugin enablement before constructing the next native agent.

    Lifecycle writers exclude active turns; the local lock coalesces concurrent
    first readers. The engine keeps registration ledgers scoped by Hermes home.
    """
    from hermes_cli.plugins import get_plugin_manager

    validate_profile_extensions(profile)
    material = {
        "plugins": read_config(profile).get("plugins", {}),
        "revision": load_state(profile).get("revision", 1),
    }
    generation = hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()
    home = str(profile.resolve())
    with _PLUGIN_REFRESH_LOCK, profile_context(profile):
        manager = get_plugin_manager()
        if _PLUGIN_GENERATIONS.get(home) != generation:
            manager.discover_and_load(force=True)
            _PLUGIN_GENERATIONS[home] = generation
        _sync_profile_shell_hooks(manager, read_config(profile))


_SHELL_HOOK_MARKER = "_xnobrain_profile_shell_hook"


def _is_shell_hook_callback(callback: Any) -> bool:
    return getattr(callback, _SHELL_HOOK_MARKER, False) or str(
        getattr(callback, "__name__", "")
    ).startswith("shell_hook[")


def _sync_profile_shell_hooks(manager: Any, config: dict[str, Any]) -> None:
    """Wire this profile's approved shell hooks into its own plugin manager.

    Hermes registers shell hooks once at CLI or gateway startup, so native
    runs never saw hooks created later. Each profile has its own manager, so
    hooks stay profile scoped. Callers hold the profile context.
    """
    from agent import shell_hooks

    # Drop every config shell hook, including ones Hermes re-registers after a
    # forced plugin reload, so removed or disabled hooks stop firing.
    for event, callbacks in list(manager._hooks.items()):
        manager._hooks[event] = [
            callback for callback in callbacks if not _is_shell_hook_callback(callback)
        ]
    for spec in shell_hooks._parse_hooks_block(config.get("hooks")):
        # Approval is recorded only through the reviewed API flow.
        if not shell_hooks.allowlist_entry_for(spec.event, spec.command):
            continue
        callback = shell_hooks._make_callback(spec)
        setattr(callback, _SHELL_HOOK_MARKER, True)
        manager._hooks.setdefault(spec.event, []).append(callback)


@contextmanager
def profile_context(profile: Path):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    sys.dont_write_bytecode = True
    token = set_hermes_home_override(str(profile))
    try:
        yield
    finally:
        reset_hermes_home_override(token)


@contextmanager
def _profile_secret_context(profile: Path):
    from agent.secret_scope import (
        build_profile_secret_scope,
        reset_secret_scope,
        set_secret_scope,
    )

    token = set_secret_scope(build_profile_secret_scope(profile))
    try:
        yield
    finally:
        reset_secret_scope(token)


def _write_config(profile: Path, config: dict[str, Any]) -> None:
    from .llm_router_support import normalize_llm_router_config

    normalize_llm_router_config(config)
    path = safe_child(profile, "config.yaml")
    fd, temporary = tempfile.mkstemp(prefix=".extension-config-", dir=profile)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            yaml.safe_dump(config, stream, sort_keys=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(profile, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def plugin_inventory(profile: Path) -> list[dict[str, Any]]:
    state = load_state(profile)
    config = read_config(profile)
    settings = config.get("plugins") or {}
    enabled = set(settings.get("enabled") or [])
    disabled = set(settings.get("disabled") or [])
    root = safe_child(profile, "plugins")
    rows = []
    for path in sorted(root.iterdir()) if root.is_dir() else []:
        if not path.is_dir() or path.is_symlink() or not PLUGIN_ID.fullmatch(path.name):
            continue
        try:
            manifest = yaml.safe_load(safe_child(path, "plugin.yaml").read_text()) or {}
            if not isinstance(manifest, dict):
                continue
            record = state["plugins"].get(path.name, {})
            system = trusted_required(profile, path.name)
            scan = record.get("scan") or {"status": "REQUIRED", "findings": []}
            rows.append(
                {
                    "id": path.name,
                    "name": str(manifest.get("name") or path.name),
                    "version": str(manifest.get("version") or ""),
                    "description": str(manifest.get("description") or "")[:500],
                    "source": "system" if system else record.get("source", "user"),
                    "artifact_digest": tree_digest(path),
                    "enabled": path.name in enabled and path.name not in disabled,
                    "hidden": bool(record.get("hidden")),
                    "required": path.name in REQUIRED_PLUGINS,
                    "system_managed": path.name in REQUIRED_PLUGINS,
                    "revision": record.get("revision", 1),
                    "scan": {"status": "COMPLETED", "verdict": "system", "findings": []}
                    if system
                    else scan,
                    "approval": {
                        "required": not system,
                        "approved": system,
                        **(record.get("approval") or {}),
                    },
                    "status": "protected" if system else scan.get("status", "REQUIRED"),
                    "provides_hooks": manifest.get("provides_hooks") or [],
                    "provides_skills": manifest.get("provides_skills") or [],
                }
            )
        except (OSError, ValueError, yaml.YAMLError):
            fail("Installed plugin metadata is invalid", "plugin_manifest_invalid")
    return rows


def plugin_set_enabled(profile: Path, name: str, enabled: bool) -> dict[str, Any]:
    if not PLUGIN_ID.fullmatch(name):
        fail("Invalid plugin identifier", "invalid_request", 422)
    config = read_config(profile)
    settings = config.setdefault("plugins", {})
    active = set(settings.get("enabled") or [])
    disabled = set(settings.get("disabled") or [])
    (active.add if enabled else active.discard)(name)
    (disabled.discard if enabled else disabled.add)(name)
    settings.update(enabled=sorted(active), disabled=sorted(disabled))
    _write_config(profile, config)
    return {"id": name, "enabled": enabled}


def plugin_remove(profile: Path, name: str) -> dict[str, Any]:
    if name in REQUIRED_PLUGINS:
        fail("System plugin is protected", "plugin_protected")
    plugin_set_enabled(profile, name, False)
    root = safe_child(profile, "plugins", name)
    if root.is_dir():
        shutil.rmtree(root)
    return {"id": name, "removed": True}


def scan_plugin(path: Path) -> dict[str, Any]:
    """Bound native regex scanning in a separate, credential-free interpreter."""
    tree_digest(path)
    return _query(path.parent, "scan", {"artifact": str(path)}, timeout=60)


def _scan_plugin(path: Path) -> dict[str, Any]:
    from tools import skills_guard

    digest = tree_digest(path)
    findings = []
    complete = True
    files = 0
    for file in sorted(path.rglob("*")):
        relative = file.relative_to(path)
        if ".git" in relative.parts or not file.is_file():
            continue
        files += 1
        # Never honor a plugin-provided .skillignore: it could hide its code.
        suffix = file.suffix.lower()
        try:
            file.read_text(encoding="utf-8")
        except (UnicodeError, OSError):
            complete = False
        if suffix not in skills_guard.SCANNABLE_EXTENSIONS:
            # Documentation/license dotfiles have no executable semantics.
            if file.name not in {
                "LICENSE",
                "LICENSE.md",
                "NOTICE",
                ".gitignore",
                ".skillignore",
                ".clawhubignore",
            }:
                complete = False
        if suffix in skills_guard.SCANNABLE_EXTENSIONS or file.name in {
            "LICENSE",
            "LICENSE.md",
            "NOTICE",
            ".gitignore",
            ".skillignore",
            ".clawhubignore",
        }:
            if suffix in skills_guard.SCANNABLE_EXTENSIONS:
                native_findings = skills_guard.scan_file(file, relative.as_posix())
            else:
                # The native scanner dispatches by extension. Normalize only
                # whitelisted plain-text metadata to scan its full bytes too.
                with tempfile.TemporaryDirectory() as scratch:
                    text_file = Path(scratch) / "metadata.txt"
                    text_file.write_bytes(file.read_bytes())
                    native_findings = skills_guard.scan_file(text_file, relative.as_posix())
            for finding in native_findings:
                findings.append(
                    {
                        "severity": finding.severity,
                        "code": finding.pattern_id,
                        "category": finding.category,
                        "file": relative.as_posix(),
                        "line": finding.line,
                        "message": finding.description,
                    }
                )
    try:
        manifest = yaml.safe_load((path / "plugin.yaml").read_text())
        if not isinstance(manifest, dict) or not (path / "__init__.py").is_file():
            fail("Plugin manifest or entry point is missing", "plugin_manifest_invalid")
        name = manifest.get("name")
        if (
            not isinstance(name, str)
            or not PLUGIN_ID.fullmatch(name)
            or manifest.get("key", name) != name
        ):
            fail("Plugin identity does not match the native loader", "plugin_manifest_invalid")
        from hermes_cli.plugins import PluginManager

        parsed = PluginManager()._parse_manifest(path / "plugin.yaml", path, "user", "")
        if parsed is None or parsed.kind != "standalone":
            fail("Plugin manifest is unsupported or invalid", "plugin_manifest_invalid")
        if (
            manifest.get("dependencies")
            or manifest.get("requires_packages")
            or parsed.python_dependencies
            or parsed.requires_plugins
        ):
            complete = False  # Dependencies require a separate reviewed installation.
        if manifest.get("allow_tool_override") or "tools.override" in (
            manifest.get("capabilities") or []
        ):
            complete = False
    except (OSError, yaml.YAMLError):
        fail("Plugin manifest is invalid", "plugin_manifest_invalid")
    dangerous = any(item["severity"] in {"critical", "high"} for item in findings)
    verdict = "dangerous" if dangerous else "caution" if findings else "safe"
    return {
        "artifact_digest": digest,
        "scan": {
            "status": "COMPLETED" if complete and not dangerous else "BLOCKED",
            "verdict": verdict,
            "coverage": "complete_text" if complete else "incomplete",
            "files": files,
            "findings": findings[:200],
            "scanner_version": skills_guard.SCANNER_VERSION,
        },
    }


def public_https(url: str, *, hosts: set[str] | None = None) -> tuple[str, str]:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        fail("HTTPS endpoint is malformed", "invalid_source", 422)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or port not in {None, 443}
    ):
        fail("A public HTTPS endpoint is required", "invalid_source", 422)
    host = parsed.hostname.lower()
    if hosts is not None and host not in hosts:
        fail("Endpoint host is not supported", "invalid_source", 422)
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        ips = {row[4][0] for row in addresses}
    except OSError:
        fail("Endpoint host could not be resolved", "dependency_unavailable", 503)
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        fail("Endpoint must resolve to public addresses", "invalid_source", 422)
    return host, sorted(ips)[0]


def clone_plugin(source: str, ref: str | None, stage: Path, cancelled=lambda: False) -> str:
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", source):
        source = "https://github.com/" + source + ".git"
    host, address = public_https(source)
    parsed = urlsplit(source)
    if parsed.query or not parsed.path.strip("/"):
        fail("Git URL is invalid", "invalid_source", 422)
    if ref and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", ref):
        fail("Git reference is invalid", "invalid_source", 422)
    stage.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    resolve = f"{host}:443:{'[' + address + ']' if ':' in address else address}"
    command = [
        "git",
        "-c",
        "http.followRedirects=false",
        "-c",
        "http.proxy=",
        "-c",
        f"http.curloptResolve={resolve}",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "protocol.file.allow=never",
        "-c",
        "protocol.ext.allow=never",
        "clone",
        "--depth=1",
        "--no-recurse-submodules",
    ]
    if ref:
        command += ["--branch", ref]
    command += ["--", source, str(stage)]
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(stage.parent),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_LFS_SKIP_SMUDGE": "1",
    }
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(
            command, env=environment, stdout=log, stderr=log, start_new_session=True
        )
        started = time.monotonic()
        try:
            while process.poll() is None:
                if cancelled():
                    fail("Operation cancelled", "operation_cancelled")
                if time.monotonic() - started > 240:
                    fail("Git operation timed out", "operation_timeout", 504)
                size = (
                    sum(p.lstat().st_size for p in stage.rglob("*") if p.is_file())
                    if stage.exists()
                    else 0
                )
                if size > 32 * 1024 * 1024 or log.tell() > 1024 * 1024:
                    fail("Git artifact exceeds limits", "plugin_scan_incomplete")
                time.sleep(0.2)
            if process.returncode:
                fail("Public Git source could not be cloned", "plugin_install_failed")
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
    result = subprocess.run(
        ["git", "-C", str(stage), "rev-parse", "HEAD"],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    ).stdout.strip()
    # Git metadata is not part of the executable installed artifact.
    shutil.rmtree(stage / ".git")
    tree_digest(stage)
    return result


# Expected validation errors a worker may report. Anything else (including
# output printed by plugin code the worker imported) stays a generic 503.
_CHILD_ERRORS = {
    "plugin_manifest_invalid": ("Plugin manifest is invalid", 409),
    "invalid_path": ("Unsafe extension storage", 409),
}


def _raise_child_error(output: str) -> None:
    marker = "XNOBRAIN_EXTENSION_ERROR="
    if marker not in output:
        return
    try:
        code = json.loads(output.rsplit(marker, 1)[1].splitlines()[0]).get("code")
    except (ValueError, AttributeError, IndexError):
        return
    if code in _CHILD_ERRORS:
        message, status = _CHILD_ERRORS[code]
        fail(message, code, status)


def _query(
    profile: Path, action: str, payload: dict[str, Any] | None = None, *, timeout: int = 20
) -> Any:
    import importlib.util

    spec = importlib.util.find_spec("hermes_cli")
    engine_root = (
        str(Path(next(iter(spec.submodule_search_locations))).parent)
        if spec and spec.submodule_search_locations
        else ""
    )
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(profile),
        "PYTHONPATH": os.pathsep.join(
            filter(None, [str(Path(__file__).resolve().parents[2]), engine_root])
        ),
        "HERMES_HOME": str(profile),
        "HERMES_ENABLE_PROJECT_PLUGINS": "0",
        "HERMES_ACCEPT_HOOKS": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "xnobrain.integrations.extension_engine", action, str(profile)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        start_new_session=True,
    )
    try:
        output, _stderr = process.communicate(json.dumps(payload or {}), timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        fail("Extension operation timed out", "operation_timeout", 504)
    if process.returncode or "XNOBRAIN_EXTENSION_JSON=" not in output:
        _raise_child_error(output)
        fail("Extension engine is unavailable", "plugins_unavailable", 503)
    return json.loads(output.rsplit("XNOBRAIN_EXTENSION_JSON=", 1)[1])


def toolset_inventory(profile: Path) -> list[dict[str, Any]]:
    validate_profile_extensions(profile)
    return _query(profile, "tools")


def toolset_set_enabled(profile: Path, name: str, enabled: bool) -> None:
    from hermes_cli.tools_config import _get_platform_tools, _save_platform_tools

    config = read_config(profile)
    disabled = set((config.get("agent") or {}).get("disabled_toolsets") or [])
    (disabled.discard if enabled else disabled.add)(name)
    config.setdefault("agent", {})["disabled_toolsets"] = sorted(disabled)
    # Resolve presets with the same helper the native API uses. Removing a
    # denylist entry alone cannot enable a tool omitted by a positive preset.
    with profile_context(profile):
        for platform in ("api_server", "cli"):
            selected = _get_platform_tools(config, platform)
            (selected.add if enabled else selected.discard)(name)
            _save_platform_tools(config, platform, selected)
    _write_config(profile, config)


def hook_events() -> dict[str, list[str]]:
    from hermes_cli.plugins import SHELL_UNSUPPORTED_HOOKS, VALID_HOOKS

    return {
        "valid_events": sorted(VALID_HOOKS - SHELL_UNSUPPORTED_HOOKS),
        "unsupported": sorted(SHELL_UNSUPPORTED_HOOKS),
    }


def hook_list(profile: Path) -> list[dict[str, Any]]:
    from agent import shell_hooks

    with profile_context(profile):
        return [
            {
                "event": spec.event,
                "command_label": "hook-" + hashlib.sha256(spec.command.encode()).hexdigest()[:12],
                "matcher": spec.matcher,
                "timeout": spec.timeout,
                "approved": bool(shell_hooks.allowlist_entry_for(spec.event, spec.command)),
            }
            for spec in shell_hooks.iter_configured_hooks(read_config(profile))
        ]


def hook_record_approval(profile: Path, event: str, command: str) -> None:
    from agent import shell_hooks

    with profile_context(profile):
        shell_hooks._record_approval(event, command)


def hook_register(profile: Path, event: str, command: str, matcher=None, timeout=60) -> None:
    if event not in hook_events()["valid_events"]:
        fail("Hook event is unsupported", "invalid_hook_event", 422)
    config = read_config(profile)
    hooks = config.setdefault("hooks", {})
    entries = hooks.setdefault(event, [])
    entries[:] = [
        entry for entry in entries if not isinstance(entry, dict) or entry.get("command") != command
    ]
    entries.append(
        {"command": command, "matcher": matcher, "timeout": min(300, int(timeout or 60))}
    )
    _write_config(profile, config)


def hook_revoke(profile: Path, event: str, command: str) -> None:
    from agent import shell_hooks

    with profile_context(profile):
        shell_hooks.revoke(command)
    config = read_config(profile)
    entries = (config.get("hooks") or {}).get(event)
    if isinstance(entries, list):
        config["hooks"][event] = [
            entry
            for entry in entries
            if not isinstance(entry, dict) or entry.get("command") != command
        ]
        _write_config(profile, config)


_GENERATION = re.compile(r"[a-f0-9]{32}")


def mcp_status(profile: Path) -> list[dict[str, Any]]:
    from .managed_mcp import profile_mcp_status

    servers = read_config(profile).get(MANAGED_MCP_KEY) or {}
    generation = str(load_state(profile).get("profile_generation") or "")
    # A profile that never installed a catalog server has no generation yet.
    live = profile_mcp_status(profile, generation) if _GENERATION.fullmatch(generation) else {}
    rows = []
    for catalog_id, record in load_state(profile)["mcp"].items():
        name = record.get("server_name")
        server = servers.get(name)
        rows.append(
            {
                "catalog_id": catalog_id,
                "server_name": name,
                "enabled": isinstance(server, dict),
                "configured": isinstance(server, dict),
                "auth_configured": bool(record.get("auth_configured")),
                "revision": record.get("revision", 1),
                "connected": live.get(name, {}).get("connected", False),
                "status": live.get(name, {}).get("status", "configured"),
            }
        )
    return rows


def refresh_profile_mcp(profile: Path) -> None:
    """Reconcile private catalog connections before constructing an agent."""
    servers = read_config(profile).get(MANAGED_MCP_KEY) or {}
    state = load_state(profile)
    generation = state.get("profile_generation", "")
    if not servers and not generation:
        return
    if not isinstance(servers, dict) or not re.fullmatch(r"[a-f0-9]{32}", str(generation)):
        fail("Managed connections need configuration recovery", "mcp_recovery_failed", 503)
    from .managed_mcp import sync_profile_mcp

    with profile_context(profile), _profile_secret_context(profile):
        sync_profile_mcp(profile, generation, servers)


def mcp_profile_isolation_supported() -> bool:
    """Return whether managed catalog MCP can stay profile scoped.

    Native ``mcp_servers`` discovery still registers globally, so catalog
    servers are stored under a separate key and run through
    ``managed_mcp.ManagedMCPServerTask``. That adapter needs the engine's
    scoped registry API and the task hooks it overrides; an engine without
    them fails closed. ``test_managed_mcp`` is the conformance suite.
    """
    try:
        from tools.mcp_tool import MCPServerTask
        from tools.registry import registry
    except Exception:
        return False
    registry_api = ("current_scope_key", "snapshot_registration", "restore_registration")
    task_hooks = ("_register_discovered_tools_if_needed", "_refresh_tools", "_deregister_tools")
    return all(callable(getattr(registry, name, None)) for name in registry_api) and all(
        callable(getattr(MCPServerTask, name, None)) for name in task_hooks
    )


def mcp_probe(profile: Path, server_name: str) -> dict[str, Any]:
    try:
        return _query(profile, "mcp", {"server_name": server_name})
    except Exception:
        return {"connected": False, "status": "unavailable", "tools_count": 0}


def mcp_disconnect(profile: Path, server_name: str) -> None:
    from .managed_mcp import disconnect_profile_mcp

    generation = str(load_state(profile).get("profile_generation") or "")
    if not _GENERATION.fullmatch(generation):
        return
    disconnect_profile_mcp(profile, generation, [server_name])


async def _probe_mcp(profile: Path, server_name: str) -> dict[str, Any]:
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    server = (read_config(profile).get(MANAGED_MCP_KEY) or {}).get(server_name)
    if not isinstance(server, dict) or not server.get("url"):
        return {"connected": False, "status": "unavailable", "tools_count": 0}
    public_https(server["url"])

    class PinnedPublicTransport(httpx.AsyncBaseTransport):
        def __init__(self):
            self.transport = httpx.AsyncHTTPTransport()

        async def handle_async_request(self, request):
            host, address = public_https(str(request.url))
            headers = request.headers.copy()
            headers["Host"] = host
            extensions = dict(request.extensions)
            extensions["sni_hostname"] = host
            pinned = httpx.Request(
                request.method,
                request.url.copy_with(host=address),
                headers=headers,
                stream=request.stream,
                extensions=extensions,
            )
            return await self.transport.handle_async_request(pinned)

        async def aclose(self):
            await self.transport.aclose()

    def factory(headers=None, timeout=None, auth=None):
        return httpx.AsyncClient(
            headers=headers,
            timeout=timeout,
            auth=auth,
            follow_redirects=False,
            transport=PinnedPublicTransport(),
        )

    async with streamablehttp_client(
        server["url"],
        headers=server.get("headers"),
        timeout=10,
        sse_read_timeout=10,
        httpx_client_factory=factory,
    ) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            tools = await session.list_tools()
            return {"connected": True, "status": "connected", "tools_count": len(tools.tools)}


def _main() -> None:
    from .hermes_support import AgentAPIError

    try:
        result = _run_action(sys.argv[1], Path(sys.argv[2]), json.load(sys.stdin))
    except AgentAPIError as error:
        # Report only the code; the parent maps it through an allowlist.
        print("XNOBRAIN_EXTENSION_ERROR=" + json.dumps({"code": error.code}))
        raise SystemExit(2) from None
    print("XNOBRAIN_EXTENSION_JSON=" + json.dumps(result))


def _run_action(action: str, profile: Path, payload: dict[str, Any]) -> Any:
    with profile_context(profile), _profile_secret_context(profile):
        if action == "tools":
            import model_tools
            from hermes_cli.tools_config import _get_platform_tools
            from tools.registry import registry

            from ..services.constants import SAFE_TOOLSETS

            config = read_config(profile)
            # Loads native registrations and approved profile-local extensions.
            model_tools.get_tool_definitions(quiet_mode=True, skip_tool_search_assembly=True)
            available = registry.get_available_toolsets()
            disabled = set((config.get("agent") or {}).get("disabled_toolsets") or [])
            selection = _get_platform_tools(config, "api_server")
            rows = []
            from hermes_cli.plugins import get_plugin_toolset_keys_nowait

            plugin_sets = get_plugin_toolset_keys_nowait()
            for name in sorted(set(SAFE_TOOLSETS) | plugin_sets):
                meta = available.get(name, {})
                rows.append(
                    {
                        "id": name,
                        "name": name,
                        "enabled": name not in disabled and name in selection,
                        "available": name in available,
                        "ready": bool(meta.get("available")),
                        "requires": meta.get("requirements", []),
                        "mutable": True,
                        "requires_approval": False,
                        "revision": load_state(profile).get("revision", 1),
                        "source": "native" if name in SAFE_TOOLSETS else "plugin",
                    }
                )
            result = rows
        elif action == "scan":
            result = _scan_plugin(Path(payload["artifact"]))
        elif action == "mcp":
            result = asyncio.run(
                asyncio.wait_for(_probe_mcp(profile, payload["server_name"]), timeout=15)
            )
        else:
            raise ValueError("unknown action")
    return result


def hook_observability(profile: Path) -> dict[str, list[dict[str, Any]]]:
    """Read declarations without importing plugin or gateway handler code."""
    plugins = []
    for row in plugin_inventory(profile):
        for event in row.get("provides_hooks") or []:
            if isinstance(event, str):
                plugins.append(
                    {
                        "plugin_id": row["id"],
                        "event": event,
                        "enabled": row["enabled"],
                        "state": "declared",
                    }
                )
    gateway = []
    root = safe_child(profile, "hooks")
    for folder in sorted(root.iterdir()) if root.is_dir() else []:
        if not folder.is_dir() or folder.is_symlink() or not PLUGIN_ID.fullmatch(folder.name):
            continue
        manifest = safe_child(folder, "HOOK.yaml")
        if not manifest.is_file() or manifest.stat().st_size > 64 * 1024:
            continue
        try:
            info = yaml.safe_load(manifest.read_text()) or {}
        except (OSError, yaml.YAMLError):
            continue
        events = info.get("events") if isinstance(info, dict) else []
        gateway.append(
            {
                "name": folder.name,
                "events": [event for event in events or [] if isinstance(event, str)],
                "state": "declared",
            }
        )
    return {"plugin_hooks": plugins, "gateway_hooks": gateway}


if __name__ == "__main__":
    _main()
