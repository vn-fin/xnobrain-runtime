"""Mediated registration policy for third-party Hermes plugins.

Hermes imports plugin modules and calls ``register(ctx)`` in-process. This
module wraps that seam: for untrusted plugins it returns a per-manager module
facade whose ``register`` context has the five forbidden host-registration
methods removed. The cached module and engine classes are never mutated.

Scan-time detection is deliberately non-executing: a bounded AST review flags
only explicit ``ctx.<forbidden>(...)`` calls in ``.py`` files. Calls computed at
runtime, aliased contexts, or code in imported helpers are known only when the
approved plugin actually loads; the load guard is the enforcement point.

This is a registration policy, not a sandbox. Native plugin Python can still
import modules, open sockets, or call manager internals directly; the network
and filesystem boundary remains plan 017 Phase 2.
"""

from __future__ import annotations

import ast
import threading
from functools import wraps
from pathlib import Path
from typing import Any

from .extension_policy import REQUIRED_PLUGINS, trusted_required
from .hermes_support import AgentAPIError

FORBIDDEN_MESSAGE = "Plugin uses a forbidden registration capability"
FORBIDDEN_REGISTRATIONS = frozenset(
    {
        "register_middleware",
        "register_approval_transport",
        "register_redaction_patterns",
        "register_secret_source",
        "register_dashboard_auth_provider",
    }
)

_POLICY_ATTR = "_xnobrain_registration_policy"
_VIOLATIONS_ATTR = "_xnobrain_registration_violations"
_INSTALL_LOCK = threading.Lock()


class _ForbiddenRegistration(Exception):
    """Internal marker; the public API always reports a fixed AgentAPIError."""


class _ModuleFacade:
    """Per-manager module view with a guarded ``register``."""

    __slots__ = ("_module", "register")

    def __init__(self, module: Any, register: Any) -> None:
        object.__setattr__(self, "_module", module)
        object.__setattr__(self, "register", register)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_module"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        object.__setattr__(self, name, value)


def bootstrap(manager: Any) -> None:
    """Install policy on one manager and clear its previous violation state."""
    install(manager)
    setattr(manager, _VIOLATIONS_ATTR, [])


def install(manager: Any) -> None:
    """Wrap this manager's load seam once, without touching other managers."""
    with _INSTALL_LOCK:
        if getattr(manager, _POLICY_ATTR, False):
            return
        original_directory = manager._load_directory_module
        original_entrypoint = manager._load_entrypoint_module
        original_discover = manager.discover_and_load

        @wraps(original_directory)
        def load_directory(manifest: Any, **kwargs: Any) -> Any:
            module = original_directory(manifest, **kwargs)
            return _guard_module(module, manifest, manager)

        @wraps(original_entrypoint)
        def load_entrypoint(manifest: Any) -> Any:
            module = original_entrypoint(manifest)
            return _guard_module(module, manifest, manager)

        @wraps(original_discover)
        def discover_and_load(force: bool = False) -> None:
            original_discover(force=force)
            enforce(manager)

        manager._load_directory_module = load_directory
        manager._load_entrypoint_module = load_entrypoint
        manager.discover_and_load = discover_and_load
        setattr(manager, _POLICY_ATTR, True)


def enforce(manager: Any) -> None:
    """Reject a completed load that attempted a forbidden registration."""
    violations = getattr(manager, _VIOLATIONS_ATTR, None) or []
    if not violations:
        return
    for plugin_key in dict.fromkeys(item[0] for item in violations):
        try:
            manager.unload(plugin_key)
        except Exception:
            # The plugin already failed closed if its register() raised. A
            # cleanup failure must not hide the stable public rejection.
            pass
    # Keep the violation state sticky so a discovery that caught this error
    # internally cannot silently continue and allow a later turn to reload the
    # same forbidden artifact. bootstrap() clears it for the next managed cycle.
    raise AgentAPIError(FORBIDDEN_MESSAGE, code="plugin_capability_forbidden", status=409)


def static_forbidden_calls(path: Path) -> list[tuple[str, int, str]]:
    """Return explicit ``ctx.<forbidden>(...)`` calls without importing code.

    This is a bounded review aid, not a completeness claim: aliases, dynamic
    dispatch, and calls in imported helpers are not detected here.
    """
    findings = []
    for file in sorted(path.rglob("*.py")):
        relative = file.relative_to(path)
        if ".git" in relative.parts or file.is_symlink():
            continue
        try:
            tree = ast.parse(file.read_text(encoding="utf-8"), filename=relative.as_posix())
        except (OSError, UnicodeError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in FORBIDDEN_REGISTRATIONS
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "ctx"
            ):
                findings.append((relative.as_posix(), node.lineno, node.func.attr))
    return findings


def _guard_module(module: Any, manifest: Any, manager: Any) -> Any:
    if _is_trusted(manifest, manager):
        return module
    register = getattr(module, "register", None)
    if not callable(register):
        return module

    @wraps(register)
    def guarded_register(ctx: Any) -> Any:
        _guard_context(ctx, manifest, manager)
        return register(ctx)

    return _ModuleFacade(module, guarded_register)


def _guard_context(ctx: Any, manifest: Any, manager: Any) -> None:
    plugin_key = manifest.key or manifest.name

    for method in FORBIDDEN_REGISTRATIONS:

        def reject(*_args: Any, _method: str = method, **_kwargs: Any) -> None:
            _record(manager, plugin_key, _method)
            raise _ForbiddenRegistration(_method)

        setattr(ctx, method, reject)


def _record(manager: Any, plugin_key: str, method: str) -> None:
    violations = getattr(manager, _VIOLATIONS_ATTR, None)
    if violations is None:
        violations = []
        setattr(manager, _VIOLATIONS_ATTR, violations)
    entry = (plugin_key, method)
    if entry not in violations:
        violations.append(entry)


def _is_trusted(manifest: Any, manager: Any) -> bool:
    if manifest.source == "bundled":
        return True
    if manifest.name not in REQUIRED_PLUGINS:
        return False
    try:
        return trusted_required(Path(manager.home_path), manifest.name)
    except Exception:
        return False
