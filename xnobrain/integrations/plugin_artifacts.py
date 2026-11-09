"""Read-only plugin artifact evidence for the Control artifact authority.

The module never imports or executes plugin code. It normalizes digests, locates
the actual engine-bundled platform plugin, and validates the optional standalone
``egress`` declaration against the same ``plugin-egress-v1`` contract Control
uses. Canonical rules are returned as data only; Control owns their digest.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

import yaml

from .extension_policy import tree_digest
from .hermes_support import AgentAPIError
from .plugin_formats import NATIVE, read_manifest

EGRESS_VERSION = "plugin-egress-v1"
MAX_RULES = 8
MAX_ENDPOINTS_PER_RULE = 16
MAX_PATH_TEMPLATE = 512
DIGEST_PREFIX = "sha256:"

_DIGEST = re.compile(r"^(?:sha256:)?([a-fA-F0-9]{64})$")
_HOST = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)
_ENDPOINT = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_FORBIDDEN_HEADERS = frozenset(
    {
        "host",
        "cookie",
        "connection",
        "transfer-encoding",
        "content-length",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "upgrade",
    }
)


class PluginArtifactError(Exception):
    """The requested artifact or declaration cannot be proven."""


def normalize_digest(raw: Any) -> str:
    """Return a canonical lowercase ``sha256:<hex>`` digest."""
    if not isinstance(raw, str):
        raise PluginArtifactError("digest_invalid")
    match = _DIGEST.fullmatch(raw.strip())
    if match is None:
        raise PluginArtifactError("digest_invalid")
    return f"{DIGEST_PREFIX}{match.group(1).lower()}"


def bundled_plugin_root() -> Path | None:
    """Return the engine's actual bundled plugin root, never a repo fallback."""
    spec = importlib.util.find_spec("hermes_cli")
    if spec is None or not spec.submodule_search_locations:
        return None
    return Path(next(iter(spec.submodule_search_locations))).parent / "plugins"


def bundled_platform_path(plugin: str) -> Path | None:
    """Return the server-selected platform plugin path when it exists."""
    root = bundled_plugin_root()
    if root is None:
        return None
    path = root / "platforms" / plugin
    if not path.is_dir() or path.is_symlink():
        return None
    return path


def builtin_platform_metadata(plugin: str) -> dict[str, str] | None:
    """Return the read-only ``{plugin, artifact_digest}`` projection, or None.

    This is presentation metadata only. The private authority endpoint
    recomputes the digest and checks ownership again.
    """
    path = bundled_platform_path(plugin)
    if path is None:
        return None
    try:
        digest = tree_digest(path)
        manifest = read_manifest(path)
    except (AgentAPIError, OSError, ValueError, yaml.YAMLError):
        return None
    if (
        manifest.get("format") != NATIVE
        or manifest.get("name") != plugin
        or manifest.get("kind") != "platform"
    ):
        return None
    return {"plugin": plugin, "artifact_digest": f"{DIGEST_PREFIX}{digest}"}


def canonical_egress(value: Any) -> dict[str, Any]:
    """Validate and canonicalize one ``plugin-egress-v1`` declaration."""
    if not isinstance(value, dict) or set(value) != {"version", "rules"}:
        raise PluginArtifactError("egress_invalid")
    if value.get("version") != EGRESS_VERSION:
        raise PluginArtifactError("egress_version_invalid")
    raw_rules = value.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules or len(raw_rules) > MAX_RULES:
        raise PluginArtifactError("egress_invalid")

    endpoints: set[str] = set()
    auth: dict[str, str] | None = None
    rules: list[dict[str, Any]] = []
    for raw_rule in raw_rules:
        rule = _canonical_rule(raw_rule)
        for endpoint in rule["endpoints"]:
            if endpoint in endpoints:
                raise PluginArtifactError("egress_invalid")
            endpoints.add(endpoint)
        if auth is None:
            auth = rule["auth"]
        elif rule["auth"] != auth:
            # One connection has one credential placement.
            raise PluginArtifactError("egress_invalid")
        rules.append(rule)
    return {"version": EGRESS_VERSION, "rules": rules}


def _canonical_rule(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "host",
        "methods",
        "path_template",
        "endpoints",
        "auth",
    }:
        raise PluginArtifactError("egress_invalid")

    host = value.get("host")
    if not isinstance(host, str):
        raise PluginArtifactError("egress_invalid")
    host = host.strip().lower()
    if (
        not _HOST.fullmatch(host)
        or host.startswith("localhost")
        or host.endswith(".localhost")
        or ".." in host
        or host.endswith(".")
        or _ip_like(host)
    ):
        raise PluginArtifactError("egress_invalid")

    raw_methods = value.get("methods")
    if not isinstance(raw_methods, list) or not raw_methods or len(raw_methods) > 2:
        raise PluginArtifactError("egress_invalid")
    methods: list[str] = []
    for raw_method in raw_methods:
        if not isinstance(raw_method, str):
            raise PluginArtifactError("egress_invalid")
        method = raw_method.strip().upper()
        if method not in {"GET", "POST"} or method in methods:
            raise PluginArtifactError("egress_invalid")
        methods.append(method)
    methods.sort()

    template = value.get("path_template")
    if not isinstance(template, str):
        raise PluginArtifactError("egress_invalid")
    template = template.strip()
    if not _valid_path_template(template):
        raise PluginArtifactError("egress_invalid")

    raw_endpoints = value.get("endpoints")
    if (
        not isinstance(raw_endpoints, list)
        or not raw_endpoints
        or len(raw_endpoints) > MAX_ENDPOINTS_PER_RULE
        or "{endpoint}" not in template
    ):
        raise PluginArtifactError("egress_invalid")
    endpoints: list[str] = []
    for raw_endpoint in raw_endpoints:
        if not isinstance(raw_endpoint, str):
            raise PluginArtifactError("egress_invalid")
        endpoint = raw_endpoint.strip()
        if not _ENDPOINT.fullmatch(endpoint) or endpoint in {".", ".."} or endpoint in endpoints:
            raise PluginArtifactError("egress_invalid")
        endpoints.append(endpoint)
    endpoints.sort()

    auth = _canonical_auth(value.get("auth"), template)
    return {
        "host": host,
        "methods": methods,
        "path_template": template,
        "endpoints": endpoints,
        "auth": auth,
    }


def _valid_path_template(template: str) -> bool:
    if (
        not template
        or not template.startswith("/")
        or len(template) > MAX_PATH_TEMPLATE
        or any(char in template for char in "?#\\%")
        or "//" in template
        or ".." in template
    ):
        return False
    if any(segment in {".", ".."} for segment in template.split("/")):
        return False
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in template):
        return False
    remaining = template.replace("{token}", "").replace("{endpoint}", "")
    if "{" in remaining or "}" in remaining:
        return False
    return template.count("{token}") <= 1 and template.count("{endpoint}") == 1


def _canonical_auth(value: Any, template: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) - {"kind", "header", "prefix"}:
        raise PluginArtifactError("egress_invalid")
    kind = value.get("kind")
    if not isinstance(kind, str):
        raise PluginArtifactError("egress_invalid")
    kind = kind.strip().lower()
    header = value.get("header")
    prefix = value.get("prefix")
    if header is not None and not isinstance(header, str):
        raise PluginArtifactError("egress_invalid")
    if prefix is not None and not isinstance(prefix, str):
        raise PluginArtifactError("egress_invalid")

    if kind == "path":
        if template.count("{token}") != 1 or str(header or "").strip() or str(prefix or ""):
            raise PluginArtifactError("egress_invalid")
        return {"kind": "path"}

    if "{token}" in template:
        raise PluginArtifactError("egress_invalid")

    if kind == "bearer":
        name = str(header or "").strip() or "Authorization"
        if not name.casefold() == "authorization":
            raise PluginArtifactError("egress_invalid")
        value = str(prefix) if prefix else "Bearer "
        if not _valid_header_value(value):
            raise PluginArtifactError("egress_invalid")
        return {"kind": "bearer", "header": "Authorization", "prefix": value}

    if kind == "header":
        name = str(header or "").strip()
        lower = name.lower()
        if (
            not _HEADER_NAME.fullmatch(name)
            or lower in _FORBIDDEN_HEADERS
            or lower.startswith(("x-forwarded-", "proxy-", "content-"))
        ):
            raise PluginArtifactError("egress_invalid")
        value = str(prefix or "")
        if not _valid_header_value(value):
            raise PluginArtifactError("egress_invalid")
        result = {"kind": "header", "header": name}
        if value:
            result["prefix"] = value
        return result

    raise PluginArtifactError("egress_invalid")


def _valid_header_value(value: str) -> bool:
    return len(value) <= 128 and not any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)


def _ip_like(host: str) -> bool:
    if not host or not all(char.isdigit() or char in ".:" for char in host):
        return False
    return "." in host
