"""Plugin package formats accepted by the per-agent installer.

Three layouts are recognised:

- ``native``: a Hermes Python plugin (``plugin.yaml`` + ``__init__.py``).
- ``portable``: an Agent Plugins v1 package (``plugin.json``, ``skills/``,
  ``mcp.json``) shared by Hermes, ChatGPT and Codex. It never runs Python.
- ``claude``: a Claude Code plugin (``.claude-plugin/plugin.json``). Only its
  skills and remote HTTPS MCP servers are kept; it is converted to the portable
  layout before scanning, and every dropped component is reported.

Functions here only read and rewrite files in a staging directory. They never
import plugin code.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from .extension_policy import PLUGIN_ID, fail

PLUGIN_SCHEMA_V1 = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA_V1 = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
NATIVE = "native"
PORTABLE = "portable"
CLAUDE = "claude"

# Files that may run as code. A portable package may carry them only as skill
# resources under ``skills/``; anywhere else they would be an unreviewed
# execution surface the portable loader never needs.
CODE_SUFFIXES = frozenset(
    {".py", ".sh", ".bash", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl", ".php", ".jl", ".r"}
)
# Binary images a portable package may ship under ``assets/`` for its listing.
ASSET_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico"})
_PORTABLE_KEEP = frozenset({"plugin.json", "mcp.json", "skills", "assets"})
_LICENSE_FILES = frozenset({"LICENSE", "LICENSE.md", "LICENSE.txt", "NOTICE"})
_MANIFEST_FIELDS = ("version", "description", "homepage", "license")


def detect_plugin_format(path: Path) -> str:
    """Return the package format of a staged plugin directory."""
    if (path / "plugin.yaml").is_file() or (path / "plugin.yml").is_file():
        return NATIVE
    if (path / "plugin.json").is_file():
        return PORTABLE
    if (path / ".claude-plugin" / "plugin.json").is_file():
        return CLAUDE
    fail(
        "No plugin manifest found (plugin.yaml, plugin.json or .claude-plugin/plugin.json)",
        "plugin_manifest_invalid",
    )


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        fail("Plugin manifest is unavailable", "plugin_manifest_invalid")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        fail("Plugin manifest is not valid JSON", "plugin_manifest_invalid")
    if not isinstance(data, dict):
        fail("Plugin manifest must be a JSON object", "plugin_manifest_invalid")
    return data


def plugin_identity(path: Path) -> tuple[str, str]:
    """Return ``(name, format)`` for a staged or installed plugin."""
    package_format = detect_plugin_format(path)
    if package_format == NATIVE:
        manifest_path = path / "plugin.yaml"
        if not manifest_path.is_file():
            manifest_path = path / "plugin.yml"
        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except (OSError, UnicodeError, yaml.YAMLError):
            fail("Plugin manifest is invalid", "plugin_manifest_invalid")
        if not isinstance(manifest, dict):
            fail("Plugin manifest is invalid", "plugin_manifest_invalid")
        name = str(manifest.get("key") or manifest.get("name") or "")
    elif package_format == PORTABLE:
        name = str(_read_json(path / "plugin.json").get("name") or "")
    else:
        name = str(_read_json(path / ".claude-plugin" / "plugin.json").get("name") or "")
    return name, package_format


def read_manifest(path: Path) -> dict[str, Any]:
    """Return display metadata for an installed plugin of any format."""
    package_format = detect_plugin_format(path)
    if package_format == NATIVE:
        manifest_path = path / "plugin.yaml"
        if not manifest_path.is_file():
            manifest_path = path / "plugin.yml"
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        if not isinstance(manifest, dict):
            raise ValueError("plugin manifest is not a mapping")
        return {**manifest, "format": NATIVE}
    manifest = _read_json(path / "plugin.json")
    return {**manifest, "format": PORTABLE}


def _remote_mcp_issue(name: str, server: Any) -> str | None:
    """Return why a declared MCP server cannot run, or ``None`` when allowed."""
    if not isinstance(server, dict):
        return f"MCP server {name!r} is not an object"
    if server.get("type") == "stdio" or "command" in server:
        return f"MCP server {name!r} runs a local command, which is blocked"
    if server.get("type") not in {"streamable-http", "http"}:
        return f"MCP server {name!r} uses an unsupported transport"
    url = server.get("url")
    parsed = urlsplit(url) if isinstance(url, str) else None
    if parsed is None or parsed.scheme != "https" or not parsed.hostname or parsed.query:
        return f"MCP server {name!r} must use a public HTTPS URL without a query string"
    headers = server.get("headers") or {}
    if not isinstance(headers, dict):
        return f"MCP server {name!r} headers must be an object"
    if any("${" in str(value) for value in headers.values()):
        # Plugin servers never receive profile secrets through placeholders.
        return f"MCP server {name!r} headers may not reference secrets"
    return None


def convert_claude_plugin(path: Path) -> list[str]:
    """Rewrite a staged Claude Code plugin into the portable layout in place.

    Keeps ``skills/`` and remote HTTP servers from ``.mcp.json``. Returns the
    names of dropped components so the user can see what was not installed.
    """
    source = _read_json(path / ".claude-plugin" / "plugin.json")
    name = source.get("name")
    if not isinstance(name, str) or not PLUGIN_ID.fullmatch(name):
        fail("Claude plugin name is invalid", "plugin_manifest_invalid")
    manifest: dict[str, Any] = {"$schema": PLUGIN_SCHEMA_V1, "name": name}
    for field in _MANIFEST_FIELDS:
        if isinstance(source.get(field), str):
            manifest[field] = source[field]
    repository = source.get("repository")
    if isinstance(repository, dict):
        repository = repository.get("url")
    if isinstance(repository, str):
        manifest["repository"] = repository
    author = source.get("author")
    if isinstance(author, str):
        manifest["author"] = {"name": author}
    elif isinstance(author, dict):
        kept = {k: v for k, v in author.items() if k in {"name", "email", "url"}}
        if kept and all(isinstance(v, str) for v in kept.values()):
            manifest["author"] = kept
    keywords = source.get("keywords")
    if isinstance(keywords, list) and all(isinstance(k, str) for k in keywords):
        manifest["keywords"] = keywords

    dropped: list[str] = []
    servers: dict[str, Any] = {}
    mcp_source = path / ".mcp.json"
    if mcp_source.exists() or mcp_source.is_symlink():
        declared = _read_json(mcp_source).get("mcpServers") or {}
        if not isinstance(declared, dict):
            fail("Claude plugin .mcp.json is invalid", "plugin_manifest_invalid")
        for server_name, server in declared.items():
            if not isinstance(server, dict):
                dropped.append(f"mcp:{server_name}")
                continue
            kind = server.get("type") or ("stdio" if "command" in server else "http")
            if kind != "http" or not isinstance(server.get("url"), str):
                dropped.append(f"mcp:{server_name} ({kind})")
                continue
            if "${" in json.dumps(server, default=str):
                # Plugin servers never receive profile secrets; keep the rest.
                dropped.append(f"mcp:{server_name} (secret)")
                continue
            converted = {"type": "streamable-http", "url": server["url"]}
            if isinstance(server.get("headers"), dict) and server["headers"]:
                converted["headers"] = server["headers"]
            servers[str(server_name)] = converted

    for item in sorted(path.iterdir()):
        if item.name in {"skills", "assets"} and item.is_dir() and not item.is_symlink():
            continue
        if item.name in _LICENSE_FILES and item.is_file():
            continue
        if item.name not in {".claude-plugin", ".mcp.json"}:
            dropped.append(item.name + ("/" if item.is_dir() else ""))
        if item.is_dir() and not item.is_symlink():
            shutil.rmtree(item)
        else:
            item.unlink()

    (path / "plugin.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    if servers:
        (path / "mcp.json").write_text(
            json.dumps({"$schema": MCP_SCHEMA_V1, "mcpServers": servers}, indent=2) + "\n",
            encoding="utf-8",
        )
    return dropped


def portable_policy_findings(path: Path) -> list[dict[str, Any]]:
    """Return XNOBrain policy findings for a portable package.

    Any finding blocks installation: portable packages carry no runnable code
    outside skills, and their MCP servers must be public HTTPS.
    """
    findings: list[dict[str, Any]] = []

    def add(code: str, file: str, message: str) -> None:
        findings.append(
            {
                "severity": "medium",
                "code": code,
                "category": "policy",
                "file": file,
                "line": 0,
                "message": message,
            }
        )

    for item in sorted(path.iterdir()):
        if item.name not in _PORTABLE_KEEP and item.name not in _LICENSE_FILES:
            add("plugin_unexpected_entry", item.name, "Entry is not part of a portable package")
    for file in sorted(path.rglob("*")):
        relative = file.relative_to(path)
        if not file.is_file() or relative.parts[0] == "skills":
            continue
        if file.suffix.lower() in CODE_SUFFIXES or file.name == "__init__.py":
            add("plugin_code_blocked", relative.as_posix(), "Code outside skills/ is not allowed")
    mcp_path = path / "mcp.json"
    if mcp_path.exists() or mcp_path.is_symlink():
        servers = _read_json(mcp_path).get("mcpServers")
        if not isinstance(servers, dict):
            add("plugin_mcp_invalid", "mcp.json", "mcpServers must be an object")
        else:
            for name, server in servers.items():
                issue = _remote_mcp_issue(str(name), server)
                if issue:
                    code = (
                        "plugin_stdio_mcp_blocked"
                        if "local command" in issue
                        else "plugin_mcp_unsupported"
                    )
                    add(code, "mcp.json", issue)
    return findings


def is_asset_image(relative: Path) -> bool:
    """Return whether a file is a listing image a portable package may ship."""
    return relative.parts[0] == "assets" and relative.suffix.lower() in ASSET_IMAGE_SUFFIXES


def portable_summary(path: Path) -> dict[str, Any]:
    """Return the skills and MCP servers a portable package declares."""
    skills_root = path / "skills"
    skills = (
        sorted(
            child.name
            for child in skills_root.iterdir()
            if child.is_dir() and not child.is_symlink() and (child / "SKILL.md").is_file()
        )
        if skills_root.is_dir() and not skills_root.is_symlink()
        else []
    )
    servers: list[dict[str, str]] = []
    mcp_path = path / "mcp.json"
    if mcp_path.is_file() and not mcp_path.is_symlink():
        declared = _read_json(mcp_path).get("mcpServers") or {}
        if isinstance(declared, dict):
            for name, server in sorted(declared.items()):
                url = server.get("url") if isinstance(server, dict) else None
                host = urlsplit(url).hostname if isinstance(url, str) else None
                servers.append({"name": str(name), "host": host or ""})
    return {"skills": skills, "mcp_servers": servers}
