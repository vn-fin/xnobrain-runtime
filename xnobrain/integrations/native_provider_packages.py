"""Control-owned native provider package download, validation and staging.

Control returns the exact deterministic package JSON whose SHA-256 is bound to
a signed install/update intent. Runtime verifies and stages those bytes before
the operation is enqueued; it never downloads "latest" from the worker and
never copies executable content from Control.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, urlsplit

import httpx
import yaml

SCHEMA_VERSION = 1
MAX_PACKAGE_BYTES = 1024 * 1024
MAX_FILES = 32
MAX_FILE_BYTES = 256 * 1024
MAX_TOOLS = 16
MAX_CONNECTION_FIELDS = 16
MAX_REFERENCES = 32
MAX_TOOL_SCHEMA_BYTES = 64 * 1024
MAX_CACHE_FILES = 32
MAX_CACHE_BYTES = 32 * 1024 * 1024
PACKAGE_ENDPOINT = "/xnobrain/api/control/internal/v1/provider-packages/"

_PROVIDER_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_PACKAGE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_LANGUAGE = re.compile(r"^[a-z]{2}(?:-[A-Za-z0-9]{2,8})?$")
_SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_REFERENCE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.md$")
_REFERENCE_KEYS = frozenset({"$ref", "$dynamicRef", "$recursiveRef"})

_PLUGIN_INIT = b'''"""Register the installed Control-managed native provider definition."""

from pathlib import Path


def register(ctx):
    from xnobrain.integrations.managed_providers import register as register_provider

    register_provider(ctx, Path(__file__).parent / "provider.json")
'''


class NativePackageError(Exception):
    """Safe, stable package failure that carries no Control response content."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "native_provider_package_invalid",
        status: int = 422,
    ):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class NativeProviderPackage:
    provider: str
    package_version: str
    definition_revision: int
    package_sha256: str
    definition: Mapping[str, Any]
    files: Mapping[str, str]

    @property
    def tool_names(self) -> list[str]:
        return [str(tool["name"]) for tool in self.definition["tools"]]


def _invalid(message: str = "Native provider package is invalid") -> NativePackageError:
    return NativePackageError(message, code="native_provider_package_invalid", status=422)


def _bounded_text(value: Any, *, limit: int, field: str) -> str:
    if not isinstance(value, str):
        raise _invalid(f"Native provider package {field} must be text")
    if "\x00" in value or len(value.encode("utf-8")) > limit:
        raise _invalid(f"Native provider package {field} is invalid")
    return value


def _localized(value: Any, *, limit: int, field: str) -> dict[str, str]:
    if not isinstance(value, dict) or not value or len(value) > 8:
        raise _invalid(f"Native provider package {field} must be localized text")
    result: dict[str, str] = {}
    for language, text in value.items():
        if not isinstance(language, str) or not _LANGUAGE.fullmatch(language):
            raise _invalid(f"Native provider package {field} has an invalid language")
        if not isinstance(text, str) or not text.strip():
            raise _invalid(f"Native provider package {field} must be non-empty")
        result[language] = _bounded_text(text, limit=limit, field=field)
    if not result.get("en", "").strip():
        raise _invalid(f"Native provider package {field} requires English text")
    return result


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise _invalid() from error


def _reject_schema_references(value: Any, depth: int = 0) -> None:
    """Refuse external schema resolution and unbounded nesting."""
    if depth > 32:
        raise _invalid("Native provider tool input schema is too deep")
    if isinstance(value, dict):
        for key, child in value.items():
            if key in _REFERENCE_KEYS and isinstance(child, str):
                raise _invalid("Native provider tool input schemas cannot use references")
            _reject_schema_references(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _reject_schema_references(child, depth + 1)


def _validate_schema(tool_name: str, schema: Any) -> dict[str, Any]:
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise _invalid(f"Native tool {tool_name} input schema must be an object")
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        raise _invalid(f"Native tool {tool_name} input schema has no properties")
    if schema.get("additionalProperties") not in (None, False):
        raise _invalid(f"Native tool {tool_name} must reject unknown parameters")
    required = schema.get("required")
    if required is not None and (
        not isinstance(required, list)
        or len(required) > len(properties)
        or any(not isinstance(item, str) or item not in properties for item in required)
    ):
        raise _invalid(f"Native tool {tool_name} has invalid required parameters")
    for name, definition in properties.items():
        if not isinstance(name, str) or not name or not isinstance(definition, dict):
            raise _invalid(f"Native tool {tool_name} has invalid properties")
    _reject_schema_references(schema)
    if len(_canonical(schema)) > MAX_TOOL_SCHEMA_BYTES:
        raise _invalid(f"Native tool {tool_name} input schema is too large")
    return schema


def _validate_tool(tool: Any, seen: set[str]) -> dict[str, Any]:
    if not isinstance(tool, dict):
        raise _invalid("Native provider tool must be an object")
    name = tool.get("name")
    if not isinstance(name, str) or not _TOOL_NAME.fullmatch(name) or name in seen:
        raise _invalid("Native provider tool name is invalid")
    seen.add(name)
    mode = tool.get("mode")
    if mode not in {"read", "write"}:
        raise _invalid(f"Native tool {name} mode is invalid")
    description = _bounded_text(
        tool.get("description", ""), limit=4000, field=f"tool {name} description"
    )
    if not description.strip():
        raise _invalid(f"Native tool {name} description is empty")
    operation_mode = tool.get("operation_mode")
    if operation_mode not in (None, "") and operation_mode != mode:
        raise _invalid(f"Native tool {name} operation mode is invalid")
    schema = _validate_schema(name, tool.get("input_schema"))
    result: dict[str, Any] = {
        "name": name,
        "mode": mode,
        "description": description,
        "input_schema": schema,
    }
    if operation_mode:
        result["operation_mode"] = mode
    request = tool.get("request")
    if request is not None:
        if not isinstance(request, dict) or request.get("endpoint") not in {
            "read",
            "execute",
            "auto",
        }:
            raise _invalid(f"Native tool {name} request routing is invalid")
        result["request"] = {"endpoint": request["endpoint"]}
    return result


def _validate_field(field: Any, seen: set[str]) -> dict[str, Any]:
    if not isinstance(field, dict):
        raise _invalid("Native connection field must be an object")
    name = field.get("name")
    if not isinstance(name, str) or not _FIELD_NAME.fullmatch(name) or name in seen:
        raise _invalid("Native connection field name is invalid")
    seen.add(name)
    kind = field.get("kind")
    if kind not in {"text", "select"}:
        raise _invalid(f"Native connection field {name} kind is invalid")
    secret = field.get("secret")
    required = field.get("required")
    if not isinstance(secret, bool) or not isinstance(required, bool):
        raise _invalid(f"Native connection field {name} flags are invalid")
    max_length = field.get("max_length")
    if kind == "text":
        if (
            isinstance(max_length, bool)
            or not isinstance(max_length, int)
            or not 1 <= max_length <= 8192
        ):
            raise _invalid(f"Native connection field {name} length is invalid")
    elif max_length not in (None, 0):
        raise _invalid(f"Native connection field {name} length is invalid")
    label = _localized(field.get("label"), limit=200, field=f"field {name} label")
    result: dict[str, Any] = {
        "name": name,
        "kind": kind,
        "secret": secret,
        "required": required,
        "label": label,
    }
    if max_length not in (None, 0):
        result["max_length"] = max_length
    help_text = field.get("help")
    if help_text is not None:
        result["help"] = _localized(help_text, limit=512, field=f"field {name} help")
    if kind == "text":
        if field.get("options"):
            raise _invalid(f"Native connection field {name} options are invalid")
    else:
        options = field.get("options")
        if not isinstance(options, list) or not 1 <= len(options) <= 32:
            raise _invalid(f"Native connection field {name} options are invalid")
        values = []
        seen_values: set[str] = set()
        for option in options:
            if not isinstance(option, dict):
                raise _invalid(f"Native connection field {name} options are invalid")
            value = option.get("value")
            if (
                not isinstance(value, str)
                or not _PACKAGE_VERSION.fullmatch(value)
                or value in seen_values
            ):
                raise _invalid(f"Native connection field {name} options are invalid")
            seen_values.add(value)
            values.append(
                {
                    "value": value,
                    "label": _localized(
                        option.get("label"), limit=200, field=f"field {name} option label"
                    ),
                }
            )
        result["options"] = values
    return result


def _skill_path(provider: str) -> str:
    return f"skills/{provider}/SKILL.md"


def validate_definition(value: Any) -> dict[str, Any]:
    """Validate one installed or downloaded provider definition."""
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise _invalid("Native provider definition schema is unsupported")
    revision = value.get("revision")
    if (
        isinstance(revision, bool)
        or not isinstance(revision, int)
        or not 1 <= revision <= 1_000_000
    ):
        raise _invalid("Native provider definition revision is invalid")
    provider = value.get("id")
    if not isinstance(provider, str) or not _PROVIDER_ID.fullmatch(provider):
        raise _invalid("Native provider definition id is invalid")
    version = value.get("version")
    if not isinstance(version, str) or not _PACKAGE_VERSION.fullmatch(version):
        raise _invalid("Native provider definition version is invalid")
    auth_kind = value.get("auth_kind")
    if auth_kind not in (None, "", "oauth"):
        raise _invalid("Native provider definition auth kind is invalid")
    name = _localized(value.get("name"), limit=200, field="name")
    description = _localized(value.get("description"), limit=2000, field="description")
    tools = value.get("tools")
    if not isinstance(tools, list) or not 1 <= len(tools) <= MAX_TOOLS:
        raise _invalid("Native provider definition tools are invalid")
    seen_tools: set[str] = set()
    normalized_tools = [_validate_tool(tool, seen_tools) for tool in tools]
    fields = value.get("connection_fields", [])
    if auth_kind == "oauth":
        if not isinstance(fields, list) or len(fields) != 0:
            raise _invalid("Native provider connection fields are invalid")
        normalized_fields = []
    else:
        if not isinstance(fields, list) or not 1 <= len(fields) <= MAX_CONNECTION_FIELDS:
            raise _invalid("Native provider connection fields are invalid")
        seen_fields: set[str] = set()
        normalized_fields = [_validate_field(field, seen_fields) for field in fields]
    skill = value.get("skill")
    if not isinstance(skill, dict):
        raise _invalid("Native provider skill is invalid")
    skill_name = skill.get("name")
    if (
        not isinstance(skill_name, str)
        or not _SKILL_NAME.fullmatch(skill_name)
        or skill_name != provider
    ):
        raise _invalid("Native provider skill name is invalid")
    skill_path = skill.get("path")
    if skill_path != _skill_path(provider):
        raise _invalid("Native provider skill path is invalid")
    skill_description = _bounded_text(
        skill.get("description", ""), limit=2000, field="skill description"
    )
    if not skill_description.strip():
        raise _invalid("Native provider skill description is empty")
    references = value.get("references", [])
    if not isinstance(references, list) or len(references) > MAX_REFERENCES:
        raise _invalid("Native provider references are invalid")
    normalized_references = []
    seen_paths = {skill_path}
    for reference in references:
        if not isinstance(reference, dict):
            raise _invalid("Native provider reference is invalid")
        path = reference.get("path")
        if not isinstance(path, str):
            raise _invalid("Native provider reference path is invalid")
        prefix = f"skills/{provider}/references/"
        if not path.startswith(prefix):
            raise _invalid("Native provider reference path is invalid")
        filename = path[len(prefix) :]
        if not _REFERENCE_NAME.fullmatch(filename) or path in seen_paths:
            raise _invalid("Native provider reference path is invalid")
        seen_paths.add(path)
        title = _bounded_text(reference.get("title", ""), limit=200, field="reference title")
        if not title.strip():
            raise _invalid("Native provider reference title is empty")
        normalized_references.append({"path": path, "title": title})
    result = {
        "schema_version": SCHEMA_VERSION,
        "revision": revision,
        "id": provider,
        "version": version,
        "name": name,
        "description": description,
        "tools": normalized_tools,
        "connection_fields": normalized_fields,
        "skill": {
            "path": skill_path,
            "name": skill_name,
            "description": skill_description,
        },
        "references": normalized_references,
    }
    if auth_kind == "oauth":
        result["auth_kind"] = "oauth"
    return result


def parse_package(raw: bytes, *, expected_provider: str | None = None) -> NativeProviderPackage:
    """Validate exact endpoint bytes without accepting executable content."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_PACKAGE_BYTES:
        raise NativePackageError(
            "Native provider package is unavailable",
            code="native_provider_package_unavailable",
            status=503,
        )
    try:
        document = json.loads(bytes(raw).decode("utf-8"))
    except (UnicodeError, ValueError) as error:
        raise _invalid() from error
    if not isinstance(document, dict) or document.get("schema_version") != SCHEMA_VERSION:
        raise _invalid()
    definition = validate_definition(document.get("definition"))
    provider = definition["id"]
    if expected_provider is not None and provider != expected_provider:
        raise _invalid("Native provider package provider mismatch")
    files = document.get("files")
    if not isinstance(files, dict) or not 1 <= len(files) <= MAX_FILES:
        raise _invalid("Native provider package files are invalid")
    required_paths = {definition["skill"]["path"]} | {
        reference["path"] for reference in definition["references"]
    }
    if set(files) != required_paths:
        raise _invalid("Native provider package files do not match its definition")
    normalized_files: dict[str, str] = {}
    total = 0
    for path, content in files.items():
        if not isinstance(path, str) or not isinstance(content, str) or "\x00" in content:
            raise _invalid("Native provider package file is invalid")
        if not content:
            raise _invalid("Native provider package file is empty")
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_FILE_BYTES:
            raise _invalid("Native provider package file is too large")
        total += len(encoded)
        if total > MAX_PACKAGE_BYTES:
            raise _invalid("Native provider package content is too large")
        normalized_files[path] = content
    if not normalized_files.get(definition["skill"]["path"], "").strip():
        raise _invalid("Native provider skill content is empty")
    return NativeProviderPackage(
        provider=provider,
        package_version=definition["version"],
        definition_revision=definition["revision"],
        package_sha256=hashlib.sha256(bytes(raw)).hexdigest(),
        definition=definition,
        files=normalized_files,
    )


def load_installed_definition(path: Path) -> dict[str, Any]:
    """Read the reviewed definition shipped inside an installed package."""
    target = Path(path)
    if target.is_symlink() or not target.is_file():
        raise NativePackageError(
            "Native provider definition is unavailable",
            code="native_provider_package_unavailable",
            status=503,
        )
    try:
        if target.stat().st_size > MAX_PACKAGE_BYTES:
            raise ValueError("definition is too large")
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise _invalid("Native provider definition is unavailable") from error
    return validate_definition(value)


def _control_origin(base_url: str) -> str:
    parsed = urlsplit(str(base_url or "").strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise NativePackageError(
            "Control package endpoint is unavailable",
            code="native_provider_package_unavailable",
            status=503,
        )
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"


def download_package(
    *,
    base_url: str,
    provider: str,
    agent_id: str,
    grant: str,
    timeout: float = 15.0,
) -> bytes:
    """Download exact package bytes from Control with a bounded, origin-only call."""
    if not _PROVIDER_ID.fullmatch(str(provider or "")):
        raise _invalid()
    if not grant:
        raise NativePackageError(
            "Control provider package grant is required",
            code="native_provider_grant_required",
            status=401,
        )
    origin = _control_origin(base_url)
    url = f"{origin}{PACKAGE_ENDPOINT}{quote(provider, safe='')}"
    headers = {
        "Authorization": f"Bearer {grant}",
        "Accept": "application/json",
    }
    try:
        with httpx.Client(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            with client.stream(
                "GET",
                url,
                params={"agent_id": agent_id},
                headers=headers,
            ) as response:
                if response.status_code != 200:
                    raise _download_status_error(response.status_code)
                content_type = (
                    response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                )
                if content_type != "application/json":
                    raise NativePackageError(
                        "Native provider package is unavailable",
                        code="native_provider_package_unavailable",
                        status=503,
                    )
                declared = response.headers.get("content-length", "")
                if declared.isdecimal() and int(declared) > MAX_PACKAGE_BYTES:
                    raise _invalid("Native provider package is too large")
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_PACKAGE_BYTES:
                        raise _invalid("Native provider package is too large")
                return bytes(raw)
    except NativePackageError:
        raise
    except (httpx.HTTPError, OSError) as error:
        raise NativePackageError(
            "Native provider package is unavailable",
            code="native_provider_package_unavailable",
            status=503,
        ) from error


def _download_status_error(status: int) -> NativePackageError:
    if status == 401:
        return NativePackageError(
            "Control provider package grant is invalid",
            code="native_provider_grant_invalid",
            status=401,
        )
    if status == 404:
        return NativePackageError(
            "Native provider package is unavailable",
            code="native_provider_not_found",
            status=404,
        )
    if status == 422:
        return NativePackageError(
            "Native provider package request is invalid",
            code="native_provider_invalid_request",
            status=422,
        )
    return NativePackageError(
        "Native provider package is unavailable",
        code="native_provider_package_unavailable",
        status=503,
    )


def _cache_root(data_dir: Path) -> Path:
    staging = Path(data_dir) / "extension-staging"
    if staging.is_symlink() or (staging.exists() and not staging.is_dir()):
        raise NativePackageError(
            "Native provider package storage is unavailable",
            code="native_provider_package_unavailable",
            status=503,
        )
    root = staging / "native-packages"
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise NativePackageError(
            "Native provider package storage is unavailable",
            code="native_provider_package_unavailable",
            status=503,
        )
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink() or not root.is_dir():
        raise NativePackageError(
            "Native provider package storage is unavailable",
            code="native_provider_package_unavailable",
            status=503,
        )
    return root


def _cache_path(data_dir: Path, digest: str) -> Path:
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise _invalid()
    return _cache_root(data_dir) / f"{digest}.json"


@contextmanager
def cache_lock(data_dir: Path):
    """Serialize package publication with garbage collection.

    A digest can be unreferenced for the few milliseconds between caching the
    verified bytes and committing the durable operation row. Holding this lock
    across both steps prevents GC from evicting accepted-but-not-yet-enqueued
    bytes.
    """
    root = _cache_root(data_dir)
    descriptor = os.open(
        root / ".lock",
        os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | os.O_NOFOLLOW,
        0o600,
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def store_cached_package(data_dir: Path, digest: str, raw: bytes) -> None:
    """Persist verified bytes content-addressed before durable enqueue."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_PACKAGE_BYTES:
        raise _invalid("Native provider package is too large")
    if hashlib.sha256(bytes(raw)).hexdigest() != digest:
        raise NativePackageError(
            "Native provider package digest does not match",
            code="native_provider_package_digest_mismatch",
            status=409,
        )
    root = _cache_root(data_dir)
    path = _cache_path(data_dir, digest)
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise _invalid("Native provider package cache is invalid")
        return
    fd, temporary = tempfile.mkstemp(prefix=".native-package-", dir=root)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(bytes(raw))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def load_cached_package(data_dir: Path, digest: str) -> bytes:
    """Return the exact pinned bytes or fail without contacting Control."""
    path = _cache_path(data_dir, digest)
    if path.is_symlink() or not path.is_file():
        raise NativePackageError(
            "Native provider package is unavailable",
            code="native_provider_package_unavailable",
            status=503,
        )
    try:
        if path.stat().st_size > MAX_PACKAGE_BYTES:
            raise ValueError("package is too large")
        raw = path.read_bytes()
    except (OSError, ValueError) as error:
        raise NativePackageError(
            "Native provider package is unavailable",
            code="native_provider_package_unavailable",
            status=503,
        ) from error
    if hashlib.sha256(raw).hexdigest() != digest:
        raise NativePackageError(
            "Native provider package digest does not match",
            code="native_provider_package_digest_mismatch",
            status=409,
        )
    return raw


def prune_cached_packages(
    data_dir: Path,
    *,
    referenced: set[str] | None = None,
    referenced_provider=None,
    keep: int = MAX_CACHE_FILES,
    max_bytes: int = MAX_CACHE_BYTES,
    min_age_seconds: int = 60,
) -> None:
    """Bound the durable cache without racing a newly queued package.

    Referenced digests are never removed, and very recent unreferenced files are
    left alone so a package cached just before its operation row is committed
    cannot be collected. This is the only cache deletion path and it shares the
    publication lock with ``store_cached_package`` callers.
    """
    try:
        _cache_root(data_dir)
    except NativePackageError:
        return
    with cache_lock(data_dir):
        if referenced_provider is not None:
            referenced = referenced_provider()
        referenced = set(referenced or ())
        root = _cache_root(data_dir)
        now = time.time()
        entries = []
        for path in root.iterdir():
            if path.is_symlink() or not path.is_file():
                continue
            if not path.name.endswith(".json"):
                continue
            digest = path.name[: -len(".json")]
            if not _SHA256.fullmatch(digest):
                continue
            try:
                info = path.stat()
            except OSError:
                continue
            entries.append((info.st_mtime, path, digest, info.st_size))
        entries.sort(key=lambda item: item[0], reverse=True)
        retained = 0
        total = 0
        for mtime, path, digest, size in entries:
            if digest in referenced:
                retained += 1
                total += size
                continue
            if now - mtime < min_age_seconds:
                retained += 1
                total += size
                continue
            if retained >= keep or total + size > max_bytes:
                path.unlink(missing_ok=True)
                continue
            retained += 1
            total += size


def build_plugin(package: NativeProviderPackage, destination: Path) -> None:
    """Write only Runtime-owned code plus allowlisted package data."""
    target = Path(destination)
    if target.exists() or target.is_symlink():
        raise NativePackageError(
            "Native provider staging path is unavailable",
            code="native_provider_package_unavailable",
            status=503,
        )
    target.mkdir(mode=0o700)
    definition = package.definition
    provider = package.provider
    description = definition["description"].get("en") or definition["name"].get("en") or provider
    manifest = {
        "name": provider,
        "version": package.package_version,
        "description": description[:500],
        "author": "XNOBrain",
        "kind": "standalone",
        "requires_env": [],
        "provides_tools": package.tool_names,
        "provides_hooks": [],
        "provides_skills": [definition["skill"]["name"]],
    }
    (target / "plugin.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    (target / "__init__.py").write_bytes(_PLUGIN_INIT)
    (target / "provider.json").write_text(
        json.dumps(
            definition,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    for relative, content in package.files.items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
