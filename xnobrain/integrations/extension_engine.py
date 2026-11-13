"""Adapters for the pinned engine's extension APIs and public Git artifacts."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
import zlib
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
    prune_plugin_artifact,
    read_config,
    safe_child,
    tree_digest,
    trusted_required,
    validate_profile_extensions,
)
from .hermes_support import AgentAPIError
from .plugin_formats import (
    PORTABLE,
    detect_plugin_format,
    is_asset_image,
    portable_policy_findings,
    portable_summary,
    read_manifest,
)

_PLUGIN_REFRESH_LOCK = threading.RLock()
_PLUGIN_GENERATIONS: dict[str, str] = {}
_LOGGER = logging.getLogger(__name__)


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
            manifest = read_manifest(path)
            package_format = manifest["format"]
            summary = portable_summary(path) if package_format == PORTABLE else {}
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
                    "provides_skills": summary.get("skills")
                    or manifest.get("provides_skills")
                    or [],
                    "format": record.get("format") or package_format,
                    "mcp_servers": summary.get("mcp_servers") or [],
                    "dropped_components": record.get("dropped_components") or [],
                }
            )
        except (OSError, ValueError, yaml.YAMLError):
            _LOGGER.warning("Skipping plugin %s with unreadable metadata", path.name)
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
    result = _query(path.parent, "scan", {"artifact": str(path)}, timeout=60)
    # The scan child has no reason to do DNS; check plugin MCP hosts here.
    # Chat re-checks with DNS pinning on every connect; one-shot runs do not yet.
    private = _private_mcp_findings(path)
    if private:
        scan = result["scan"]
        scan["findings"] = (scan.get("findings", []) + private)[:200]
        scan["status"], scan["coverage"] = "BLOCKED", "incomplete"
        if scan.get("verdict") == "safe":
            scan["verdict"] = "caution"
    return result


def _private_mcp_findings(path: Path) -> list[dict[str, Any]]:
    if detect_plugin_format(path) != PORTABLE:
        return []
    findings = []
    for server in portable_summary(path)["mcp_servers"]:
        try:
            public_https(f"https://{server['host']}/")
        except AgentAPIError:
            findings.append(
                {
                    "severity": "medium",
                    "code": "plugin_mcp_unsupported",
                    "category": "policy",
                    "file": "mcp.json",
                    "line": 0,
                    "message": f"MCP server {server['name']!r} must resolve to public addresses",
                }
            )
    return findings


def _scan_plugin(path: Path) -> dict[str, Any]:
    from tools import skills_guard

    digest = tree_digest(path)
    package_format = detect_plugin_format(path)
    findings = []
    complete = True
    files = 0
    for file in sorted(path.rglob("*")):
        relative = file.relative_to(path)
        if ".git" in relative.parts or not file.is_file():
            continue
        files += 1
        if package_format == PORTABLE and is_asset_image(relative):
            continue  # Listing images are never loaded or executed.
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
    if package_format == PORTABLE:
        complete = _check_portable_package(path, findings) and complete
    else:
        complete = _check_native_package(path) and complete
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


def _check_portable_package(path: Path, findings: list[dict[str, Any]]) -> bool:
    """Validate an Agent Plugins v1 package; return whether coverage is complete."""
    from hermes_cli.agent_plugins import AgentPluginError, load_agent_plugin

    complete = True
    policy = portable_policy_findings(path)
    if policy:
        findings.extend(policy)
        complete = False
    try:
        with tempfile.TemporaryDirectory() as data_root:
            package = load_agent_plugin(path, Path(data_root))
    except (AgentPluginError, OSError, ValueError) as error:
        fail(f"Portable plugin is invalid: {str(error)[:160]}", "plugin_manifest_invalid")
    if not PLUGIN_ID.fullmatch(package.name):
        fail("Portable plugin name is not a valid plugin identifier", "plugin_manifest_invalid")
    for diagnostic in package.diagnostics:
        findings.append(
            {
                "severity": "medium",
                "code": "plugin_manifest_diagnostic",
                "category": "policy",
                "file": diagnostic.scope,
                "line": 0,
                "message": str(diagnostic.message)[:300],
            }
        )
        complete = False
    return complete


_UNSUPPORTED_NATIVE_KINDS = {
    "backend": "Backend plugins require packaged engine support",
    "exclusive": "Exclusive plugins require packaged engine support",
    "model-provider": "Model provider plugins require managed provider support",
    "platform": "Platform plugins require packaged channel support",
}
_NATIVE_PLUGIN_KINDS = frozenset({"standalone", *_UNSUPPORTED_NATIVE_KINDS})


def _check_native_package(path: Path) -> bool:
    """Validate a Hermes Python plugin; return whether coverage is complete."""
    complete = True
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
        raw_kind = manifest.get("kind")
        if raw_kind is not None and (
            not isinstance(raw_kind, str) or raw_kind.strip().lower() not in _NATIVE_PLUGIN_KINDS
        ):
            fail("Plugin manifest is invalid", "plugin_manifest_invalid")
        from hermes_cli.plugins import PluginManager

        parsed = PluginManager()._parse_manifest(path / "plugin.yaml", path, "user", "")
        if parsed is None:
            fail("Plugin manifest is invalid", "plugin_manifest_invalid")
        kind = getattr(parsed, "kind", None)
        if kind == "standalone":
            pass
        elif isinstance(kind, str) and kind in _UNSUPPORTED_NATIVE_KINDS:
            fail(
                f"Plugin kind '{kind}' cannot be installed here; "
                f"{_UNSUPPORTED_NATIVE_KINDS[kind]}. Only standalone plugins are supported",
                "plugin_kind_unsupported",
            )
        else:
            fail("Plugin manifest is invalid", "plugin_manifest_invalid")
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
    return complete


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
    # Pin IPv4 first: container networks often have no IPv6 route, and a plain
    # string sort would put "2600:..." before "32.x.x.x".
    return host, min(ips, key=lambda ip: (ipaddress.ip_address(ip).version, ip))


_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_SUBDIRECTORY_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def plugin_subdirectory(path: str | None) -> list[str]:
    """Split a repository subdirectory into safe parts; reject traversal."""
    if not path:
        return []
    parts = path.rstrip("/").split("/")
    if (
        path.startswith("/")
        or len(path) > 512
        or len(parts) > 8
        or any(not _SUBDIRECTORY_PART.fullmatch(part) or part in {".", ".."} for part in parts)
    ):
        fail("Plugin subdirectory is invalid", "invalid_source", 422)
    return parts


def clone_plugin(
    source: str,
    ref: str | None,
    stage: Path,
    cancelled=lambda: False,
    path: str | None = None,
    *,
    artifact: bool = True,
) -> str:
    """Fetch one public Git revision into ``stage`` and return its commit.

    ``ref`` may be a branch, a tag, or an exact 40-character commit SHA.
    ``path`` keeps only that repository subdirectory as the plugin artifact.
    """
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", source):
        source = "https://github.com/" + source + ".git"
    host, address = public_https(source)
    parsed = urlsplit(source)
    if parsed.query or not parsed.path.strip("/"):
        fail("Git URL is invalid", "invalid_source", 422)
    if ref and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", ref):
        fail("Git reference is invalid", "invalid_source", 422)
    subdirectory = plugin_subdirectory(path)
    stage.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    resolve = f"{host}:443:{'[' + address + ']' if ':' in address else address}"
    hardened = [
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
    ]
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(stage.parent),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_LFS_SKIP_SMUDGE": "1",
    }
    started = time.monotonic()
    if ref and _COMMIT_SHA.fullmatch(ref):
        # A branch clone cannot name a commit; fetch exactly that one object.
        _run_git(
            [*hardened, "init", "-q", "--", str(stage)], environment, stage, cancelled, started
        )
        _run_git(
            [
                *hardened,
                "-C",
                str(stage),
                "fetch",
                "-q",
                "--depth=1",
                "--no-recurse-submodules",
                "--",
                source,
                ref,
            ],
            environment,
            stage,
            cancelled,
            started,
            failure=("Commit was not found in the public Git source", "invalid_git_ref"),
        )
        _run_git(
            [*hardened, "-C", str(stage), "checkout", "-q", "--detach", "FETCH_HEAD"],
            environment,
            stage,
            cancelled,
            started,
        )
    else:
        command = [*hardened, "clone", "--depth=1", "--no-recurse-submodules"]
        if ref:
            command += ["--branch", ref]
        command += ["--", source, str(stage)]
        _run_git(command, environment, stage, cancelled, started)
    result = subprocess.run(
        ["git", "-C", str(stage), "rev-parse", "HEAD"],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    ).stdout.strip()
    if ref and _COMMIT_SHA.fullmatch(ref) and result != ref:
        fail("Fetched commit does not match the pinned SHA", "invalid_git_ref", 422)
    # Git metadata, CI, tests and docs are not part of the installed artifact.
    shutil.rmtree(stage / ".git")
    if subdirectory:
        selected = safe_child(stage, *subdirectory)
        if not selected.is_dir():
            fail("Plugin subdirectory was not found", "invalid_source", 422)
        keep = stage.parent / f".{stage.name}.subdirectory"
        os.replace(selected, keep)
        shutil.rmtree(stage)
        os.replace(keep, stage)
    if artifact:
        prune_plugin_artifact(stage)
        tree_digest(stage)
    return result


def extract_plugin_archive(archive_path: Path, stage: Path, cancelled=lambda: False) -> str:
    """Safely extract a bounded plugin ZIP and return its artifact digest."""

    def invalid() -> None:
        fail("Plugin archive is invalid", "invalid_plugin_archive", 422)

    try:
        with zipfile.ZipFile(archive_path) as archive:
            entries = archive.infolist()
            if len(entries) > 2000:
                invalid()
            names: set[str] = set()
            parsed: list[tuple[zipfile.ZipInfo, tuple[str, ...], bool]] = []
            roots: set[str] = set()
            total_header = 0
            for info in entries:
                name = info.filename
                if (
                    not name
                    or "\\" in name
                    or name.startswith("/")
                    or re.match(r"^[A-Za-z]:", name)
                ):
                    invalid()
                parts = tuple(part for part in name.rstrip("/").split("/") if part)
                if (
                    not parts
                    or any(part in {".", ".."} for part in parts)
                    or any(re.match(r"^[A-Za-z]:", part) for part in parts)
                ):
                    invalid()
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    invalid()
                if name in names:
                    invalid()
                names.add(name)
                total_header += info.file_size
                if total_header > 32 * 1024 * 1024:
                    invalid()
                if (
                    info.file_size > 1024 * 1024
                    and info.file_size / max(info.compress_size, 1) > 100
                ):
                    invalid()
                skip = parts[0] == "__MACOSX" or parts[-1] == ".DS_Store"
                if not skip:
                    roots.add(parts[0])
                parsed.append((info, parts, skip))
            strip_root = len(roots) == 1 and all(
                len(parts) > 1 or info.is_dir() for info, parts, skip in parsed if not skip
            )
            stage.mkdir(parents=True, exist_ok=False, mode=0o700)
            actual_total = 0
            for info, parts, skip in parsed:
                if cancelled():
                    fail("Operation cancelled", "operation_cancelled")
                if skip:
                    count = 0
                    with archive.open(info) as source:
                        while chunk := source.read(64 * 1024):
                            count += len(chunk)
                            actual_total += len(chunk)
                            if actual_total > 32 * 1024 * 1024 or (
                                count > 1024 * 1024 and count / max(info.compress_size, 1) > 100
                            ):
                                invalid()
                    continue
                output_parts = parts[1:] if strip_root else parts
                if not output_parts:
                    continue
                target = safe_child(stage, *output_parts)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True, mode=0o700)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                count = 0
                with archive.open(info) as source, target.open("xb") as destination:
                    while chunk := source.read(64 * 1024):
                        count += len(chunk)
                        actual_total += len(chunk)
                        if (
                            count > 32 * 1024 * 1024
                            or actual_total > 32 * 1024 * 1024
                            or (count > 1024 * 1024 and count / max(info.compress_size, 1) > 100)
                        ):
                            invalid()
                        destination.write(chunk)
            prune_plugin_artifact(stage)
            return tree_digest(stage)
    except AgentAPIError:
        raise
    except (
        zipfile.BadZipFile,
        EOFError,
        NotImplementedError,
        OSError,
        RuntimeError,
        ValueError,
        zlib.error,
    ):
        invalid()


def _run_git(
    command: list[str],
    environment: dict[str, str],
    stage: Path,
    cancelled,
    started: float,
    *,
    failure: tuple[str, str] = ("Public Git source could not be cloned", "plugin_install_failed"),
) -> None:
    """Run one bounded Git step; enforce cancellation, time and size limits."""
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(
            command, env=environment, stdout=log, stderr=log, start_new_session=True
        )
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
                fail(failure[0], failure[1], 422 if failure[1] == "invalid_git_ref" else 409)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()


# Expected validation errors a worker may report. Anything else (including
# output printed by plugin code the worker imported) stays a generic 503.
_CHILD_ERRORS = {
    "plugin_manifest_invalid": ("Plugin manifest is invalid", 409),
    "plugin_kind_unsupported": (
        "This plugin kind is unsupported; only standalone plugins can be installed",
        409,
    ),
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
    matcher = _hook_matcher(matcher)
    config = read_config(profile)
    hooks = config.setdefault("hooks", {})
    entries = hooks.setdefault(event, [])
    entries[:] = [
        entry
        for entry in entries
        if not (
            isinstance(entry, dict)
            and entry.get("command") == command
            and _hook_matcher(entry.get("matcher")) == matcher
        )
    ]
    entries.append(
        {"command": command, "matcher": matcher, "timeout": min(300, int(timeout or 60))}
    )
    _write_config(profile, config)


def _hook_matcher(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _other_enabled_hook(
    profile: Path, event: str, command: str, exclude_hook_id: str | None
) -> bool:
    """Return whether another enabled and approved hook still needs this command."""
    for hook_id, record in (load_state(profile).get("hooks") or {}).items():
        if hook_id == exclude_hook_id or not isinstance(record, dict):
            continue
        if record.get("event") != event or record.get("command") != command:
            continue
        approval = record.get("approval") or {}
        if (
            record.get("enabled")
            and approval.get("approved") is True
            and approval.get("digest") == record.get("command_digest")
        ):
            return True
    return False


def _revoke_allowlist_pair(shell_hooks: Any, event: str, command: str) -> None:
    """Remove only this ``(event, command)`` consent, leaving other events intact."""
    with shell_hooks._locked_update_approvals() as data:
        data["approvals"] = [
            entry
            for entry in data.get("approvals", [])
            if not (
                isinstance(entry, dict)
                and entry.get("event") == event
                and entry.get("command") == command
            )
        ]


def hook_revoke(
    profile: Path,
    event: str,
    command: str,
    matcher=None,
    *,
    exclude_hook_id: str | None = None,
) -> None:
    from agent import shell_hooks

    config = read_config(profile)
    hooks = config.get("hooks")
    entries = hooks.get(event) if isinstance(hooks, dict) else None
    if isinstance(entries, list):
        matcher_key = _hook_matcher(matcher)
        kept = [
            entry
            for entry in entries
            if not (
                isinstance(entry, dict)
                and entry.get("command") == command
                and _hook_matcher(entry.get("matcher")) == matcher_key
            )
        ]
        if len(kept) != len(entries):
            if kept:
                hooks[event] = kept
            else:
                hooks.pop(event, None)
            if not hooks:
                config.pop("hooks", None)
            _write_config(profile, config)
    if _other_enabled_hook(profile, event, command, exclude_hook_id):
        return
    with profile_context(profile):
        _revoke_allowlist_pair(shell_hooks, event, command)


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
        status = live.get(name, {})
        rows.append(
            {
                "catalog_id": catalog_id,
                "server_name": name,
                "enabled": isinstance(server, dict),
                "configured": isinstance(server, dict),
                "auth_configured": bool(record.get("auth_configured")),
                "revision": record.get("revision", 1),
                "connected": status.get("connected", False),
                "status": status.get("status", "configured"),
            }
        )
    return rows


def _custom_mcp_servers(config: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Return the profile's own ``mcp_servers``, minus legacy catalog entries."""
    servers = config.get("mcp_servers")
    if not isinstance(servers, dict):
        return {}
    catalog_names = {
        record.get("server_name")
        for record in (state.get("mcp") or {}).values()
        if isinstance(record, dict)
    }
    return {
        name: server
        for name, server in servers.items()
        if name not in catalog_names
        and not (isinstance(server, dict) and server.get("xnobrain_plugin_shadow"))
    }


def portable_plugin_mcp_names(profile: Path, plugin_id: str) -> list[str]:
    """Return Hermes engine names for one installed portable plugin's MCP entries."""
    from hermes_cli.plugins import _portable_skill_namespace

    from .plugin_formats import PORTABLE, read_manifest

    plugin = Path(profile) / "plugins" / plugin_id
    if (
        not plugin.is_dir()
        or plugin.is_symlink()
        or read_manifest(plugin).get("format") != PORTABLE
    ):
        return []
    path = plugin / "mcp.json"
    if not path.is_file() or path.is_symlink():
        return []
    import json

    servers = json.loads(path.read_text(encoding="utf-8")).get("mcpServers") or {}
    if not isinstance(servers, dict):
        return []
    namespace = _portable_skill_namespace(plugin_id)
    return [f"{namespace}__{name}" for name in servers if isinstance(name, str)]


def _portable_plugin_mcp_servers(profile: Path) -> dict[str, Any]:
    """Return MCP servers declared by this profile's enabled portable plugins.

    ``refresh_profile_plugins`` runs first, so the engine's plugin manager has
    loaded only approved, enabled packages for this profile.
    """
    try:
        from hermes_cli.plugins import get_plugin_manager

        with profile_context(profile):
            servers = get_plugin_manager().get_portable_mcp_servers()
            result = dict(servers) if isinstance(servers, dict) else {}
            state = load_state(profile)
            from hermes_cli.plugins import _portable_skill_namespace

            for plugin_id, record in (state.get("plugins") or {}).items():
                if record.get("format") not in {"portable", "claude"}:
                    continue
                path = Path(profile) / "plugins" / plugin_id / "mcp.json"
                if not path.is_file() or path.is_symlink():
                    continue
                declarations = json.loads(path.read_text(encoding="utf-8")).get("mcpServers") or {}
                prefix = f"{_portable_skill_namespace(plugin_id)}__"
                for name, declaration in declarations.items():
                    engine_name = f"{prefix}{name}"
                    if engine_name in result and isinstance(declaration, dict):
                        result[engine_name] = {**result[engine_name], **declaration}
            return result
    except Exception:
        return {}


def _canonical_connection_id(value: Any) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as error:
        raise ValueError("Control connection ID is invalid") from error


def _valid_connection_id(value: Any) -> bool:
    try:
        _canonical_connection_id(value)
        return True
    except ValueError:
        return False


def refresh_profile_mcp(profile: Path) -> None:
    """Reconcile this profile's MCP connections before constructing an agent.

    Catalog servers, the profile's own ``mcp_servers`` and MCP servers of
    enabled portable plugins all run through the profile-scoped adapter, so
    native turns never share MCP registrations across profiles.
    """
    config = read_config(profile)
    servers = config.get(MANAGED_MCP_KEY) or {}
    state = load_state(profile)
    custom = _custom_mcp_servers(config, state)
    custom = {
        name: server
        for name, server in custom.items()
        if not (
            isinstance(server, dict)
            and server.get("xnobrain_connection_id")
            and not _valid_connection_id(server.get("xnobrain_connection_id"))
        )
    }
    plugin_servers = _portable_plugin_mcp_servers(profile)
    plugin_state = state.get("plugins") or {}
    mappings = state.get("plugin_mcp") or {}
    if not isinstance(plugin_servers, dict):
        plugin_servers = {}
    from hermes_cli.plugins import _portable_skill_namespace

    namespace_ids = {
        f"{_portable_skill_namespace(plugin_id)}__": plugin_id for plugin_id in plugin_state
    }
    for engine_name, server in list(plugin_servers.items()):
        if not isinstance(server, dict):
            continue
        # The engine names servers <plugin namespace>__<server name>.
        plugin_id = next(
            (key for prefix, key in namespace_ids.items() if engine_name.startswith(prefix)), None
        )
        server_name = engine_name.split("__", 1)[-1]
        record = plugin_state.get(plugin_id, {}) if plugin_id else {}
        revision = str(record.get("commit") or "")
        mapping = mappings.get(f"{plugin_id}/{server_name}") if plugin_id else None
        if isinstance(mapping, dict) and mapping.get("plugin_revision") == revision:
            control_url = os.environ.get("RUNTIME_CONTROL_URL", "").rstrip("/")
            try:
                connection_id = _canonical_connection_id(mapping.get("connection_id"))
            except ValueError:
                plugin_servers.pop(engine_name, None)
                continue
            server.clear()
            server.update(
                {
                    "url": f"{control_url}/xnobrain/api/control/internal/v1/mcp/{connection_id}",
                    "xnobrain_connection_id": connection_id,
                }
            )
        elif any(
            value == "" or "${" in str(value) for value in (server.get("headers") or {}).values()
        ):
            plugin_servers.pop(engine_name, None)
    generation = str(state.get("profile_generation") or "")
    if not servers and not custom and not plugin_servers and not generation:
        return
    if not isinstance(servers, dict):
        fail("Managed connections need configuration recovery", "mcp_recovery_failed", 503)
    from .managed_mcp import ensure_profile_generation, sync_profile_mcp

    if not _GENERATION.fullmatch(generation):
        if servers:
            fail("Managed connections need configuration recovery", "mcp_recovery_failed", 503)
        generation = ensure_profile_generation(profile)

    with profile_context(profile), _profile_secret_context(profile):
        sync_profile_mcp(profile, generation, servers, custom, plugin_servers)


def mcp_profile_isolation_supported() -> bool:
    """Return whether managed catalog MCP can stay profile scoped.

    Native ``mcp_servers`` discovery registers globally, so native turns run
    both catalog servers (stored under a separate key) and the profile's own
    ``mcp_servers`` through ``managed_mcp.ManagedMCPServerTask``. That adapter needs the engine's
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
        server = (read_config(profile).get(MANAGED_MCP_KEY) or {}).get(server_name)
    except Exception:
        return {"connected": False, "status": "unavailable", "tools_count": 0}
    if isinstance(server, dict) and server.get("xnobrain_connection_id"):
        # Control-proxied servers use a private http:// URL that is rebuilt
        # from the in-memory grant; public HTTPS validation is meaningless here.
        # The live managed task in this process is the authoritative probe.
        from .managed_mcp import profile_mcp_status

        generation = str(load_state(profile).get("profile_generation") or "")
        if _GENERATION.fullmatch(generation):
            try:
                status = profile_mcp_status(profile, generation).get(server_name)
            except (ValueError, RuntimeError):
                status = None
            if isinstance(status, dict):
                return status
        return {"connected": False, "status": "unavailable", "tools_count": 0}
    try:
        return _query(profile, "mcp", {"server_name": server_name})
    except Exception:
        return {"connected": False, "status": "unavailable", "tools_count": 0}


def mcp_disconnect(profile: Path, server_name: str | None = None) -> None:
    from .managed_mcp import disconnect_profile_mcp

    generation = str(load_state(profile).get("profile_generation") or "")
    if not _GENERATION.fullmatch(generation):
        return
    disconnect_profile_mcp(profile, generation, None if server_name is None else [server_name])


async def _probe_mcp(profile: Path, server_name: str) -> dict[str, Any]:
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    server = (read_config(profile).get(MANAGED_MCP_KEY) or {}).get(server_name)
    if not isinstance(server, dict) or not server.get("url"):
        return {"connected": False, "status": "unavailable", "tools_count": 0}
    connection_id = server.get("xnobrain_connection_id")
    control_url = os.environ.get("RUNTIME_CONTROL_URL", "").rstrip("/")
    # Only the exact Control proxy URL may skip public HTTPS pinning.
    control_proxy = (
        isinstance(connection_id, str)
        and bool(control_url)
        and server["url"] == f"{control_url}/xnobrain/api/control/internal/v1/mcp/{connection_id}"
    )
    if not control_proxy:
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
        transport = None if control_proxy else PinnedPublicTransport()
        return httpx.AsyncClient(
            headers=headers,
            timeout=timeout,
            auth=auth,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
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
