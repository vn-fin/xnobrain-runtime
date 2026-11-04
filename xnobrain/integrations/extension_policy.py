"""Profile-local extension trust records and execution admission checks."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import re
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

from .hermes_support import AgentAPIError

PLUGIN_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$")
REQUIRED_PLUGINS = frozenset({"xnobrain-runtime-updates"})
MANAGED_MCP_KEY = "xnobrain_managed_mcp_servers"


class _HookMetadataFilter(logging.Filter):
    def filter(self, record):
        # The upstream logger includes shell commands in registration/consent
        # messages. Keep those user-owned bytes out of Runtime logs.
        record.msg = "Shell hook lifecycle event"
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        return True


logging.getLogger("agent.shell_hooks").addFilter(_HookMetadataFilter())


def fail(message: str, code: str = "plugin_approval_required", status: int = 409):
    raise AgentAPIError(message, code=code, status=status)


def safe_child(root: Path, *parts: str) -> Path:
    path = root
    if root.is_symlink():
        fail("Unsafe extension storage", "invalid_path")
    for index, part in enumerate(parts):
        if part in {"", ".", ".."} or "/" in part or "\\" in part:
            fail("Invalid extension path", "invalid_path")
        path = path / part
        if path.is_symlink():
            fail("Unsafe extension storage", "invalid_path")
        if index < len(parts) - 1 and path.exists() and not path.is_dir():
            fail("Unsafe extension storage", "invalid_path")
    return path


def state_root(profile: Path) -> Path:
    root = safe_child(profile, ".xnobrain", "extensions")
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError:
        fail("Extension storage is unavailable", "extension_state_invalid", 503)
    return root


def atomic_json(path: Path, payload: Any) -> None:
    if path.is_symlink():
        fail("Unsafe extension storage", "invalid_path")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".extension-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


def load_state(profile: Path) -> dict[str, Any]:
    path = safe_child(profile, ".xnobrain", "extensions", "state.json")
    if not path.exists():
        return {"plugins": {}, "hooks": {}, "mcp": {}, "revision": 1}
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(result, dict) or any(
            not isinstance(result.get(key, {}), dict) for key in ("plugins", "hooks", "mcp")
        ):
            raise ValueError("invalid state")
        for key in ("plugins", "hooks", "mcp"):
            result.setdefault(key, {})
        result.setdefault("revision", 1)
        return result
    except (ValueError, OSError):
        fail("Extension state is unavailable", "extension_state_invalid", 503)


def save_state(profile: Path, state: dict[str, Any]) -> None:
    atomic_json(state_root(profile) / "state.json", state)


# Repository content the plugin loader never imports or reads. It is dropped
# from a cloned artifact so CI files, test fixtures and documentation cannot
# block or bloat the scan of the code that actually runs.
_NON_RUNTIME_DIRS = frozenset(
    {
        ".github",
        ".gitlab",
        ".circleci",
        "tests",
        "test",
        "docs",
        "bench",
        "benchmarks",
        "benchmarking",
    }
)
_NON_RUNTIME_FILES = frozenset(
    {
        "readme",
        "readme.md",
        "readme.rst",
        "readme.txt",
        "changelog.md",
        "changes.md",
        "history.md",
        "contributing.md",
        "code_of_conduct.md",
        "security.md",
        "roadmap.md",
        "agents.md",
        "claude.md",
        "conftest.py",
        "pytest.ini",
        "tox.ini",
        ".pre-commit-config.yaml",
        ".editorconfig",
        ".gitattributes",
    }
)


def prune_plugin_artifact(path: Path) -> list[str]:
    """Remove top-level non-runtime entries from a freshly cloned plugin.

    Symlinks are left in place so ``tree_digest`` still rejects them.
    """
    removed = []
    for item in sorted(path.iterdir()):
        name = item.name.lower()
        if item.is_symlink():
            continue
        if item.is_dir() and name in _NON_RUNTIME_DIRS:
            shutil.rmtree(item)
            removed.append(item.name + "/")
        elif item.is_file() and name in _NON_RUNTIME_FILES:
            item.unlink()
            removed.append(item.name)
    return removed


def tree_digest(path: Path) -> str:
    if not path.is_dir() or path.is_symlink():
        fail("Plugin artifact is unavailable", "plugin_not_found", 404)
    digest = hashlib.sha256()
    total = 0
    files = 0
    for item in sorted(path.rglob("*")):
        relative = item.relative_to(path)
        if ".git" in relative.parts:
            continue
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode):
            fail("Plugin symlinks are not supported", "plugin_unsafe")
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode):
            fail("Plugin special files are not supported", "plugin_unsafe")
        total += info.st_size
        files += 1
        if files > 500 or total > 8 * 1024 * 1024 or info.st_size > 1024 * 1024:
            fail("Plugin exceeds scan limits", "plugin_scan_incomplete")
        content = item.read_bytes()
        name = relative.as_posix().encode()
        digest.update(len(name).to_bytes(8, "big") + name)
        digest.update(len(content).to_bytes(8, "big") + content)
    return digest.hexdigest()


def required_plugin_root() -> Path:
    packaged = Path(os.getenv("XNOBRAIN_REQUIRED_PLUGINS_DIR", "/opt/xnobrain/required-plugins"))
    if packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parents[2] / "runtime" / "required-plugins"


def trusted_required(profile: Path, name: str) -> bool:
    if name not in REQUIRED_PLUGINS:
        return False
    actual = safe_child(profile, "plugins", name)
    expected = safe_child(required_plugin_root(), name)
    if not actual.is_dir() or actual.is_symlink() or not expected.is_dir():
        return False
    # Interpreter caches are generated runtime state. Drop them so Python
    # recompiles from the verified source instead of trusting stale bytecode.
    for cache in list(actual.rglob("__pycache__")):
        if cache.is_dir() and not cache.is_symlink():
            shutil.rmtree(cache)
    return tree_digest(actual) == tree_digest(expected)


def read_config(profile: Path) -> dict[str, Any]:
    path = safe_child(profile, "config.yaml")
    try:
        config = yaml.safe_load(path.read_text()) if path.is_file() else {}
    except (OSError, ValueError, yaml.YAMLError):
        fail("Agent configuration is unavailable", "invalid_agent_config", 503)
    if config is None:
        return {}
    if not isinstance(config, dict):
        fail("Agent configuration is invalid", "invalid_agent_config")
    return config


def validate_profile_extensions(profile: Path) -> None:
    """Refuse execution of changed/unapproved user plugins before engine loading."""
    # Profile executable artifacts are source-only. Do not create a bytecode
    # execution channel outside the scanned source tree.
    sys.dont_write_bytecode = True
    if safe_child(profile, ".xnobrain", "extensions", "publication.json").exists():
        fail("Extension publication requires recovery", "extension_recovery_required", 503)
    if safe_child(profile, ".xnobrain", "extensions", "mcp-recovery.json").exists():
        fail("Extension configuration requires recovery", "extension_recovery_required", 503)
    config = read_config(profile)
    plugins = config.get("plugins") or {}
    if not isinstance(plugins, dict):
        fail("Plugin configuration is invalid", "plugin_unsafe")
    state = load_state(profile)
    enabled = plugins.get("enabled") or []
    disabled = set(plugins.get("disabled") or [])
    if not isinstance(enabled, list):
        fail("Plugin configuration is invalid", "plugin_unsafe")
    for name in enabled:
        if not isinstance(name, str) or not PLUGIN_ID.fullmatch(name):
            fail("Plugin identifier is invalid", "plugin_unsafe")
        if name in disabled:
            continue
        artifact = safe_child(profile, "plugins", name)
        if not artifact.exists():
            # Image-owned bundled plugins are trusted like the engine itself.
            spec = importlib.util.find_spec("hermes_cli")
            bundled = (
                Path(next(iter(spec.submodule_search_locations))).parent / "plugins"
                if spec and spec.submodule_search_locations
                else None
            )
            if bundled and (bundled / name / "plugin.yaml").is_file():
                continue
            # A missing image-owned required plugin has no code to run; the
            # profile repair step installs it. User plugins still fail closed.
            if name in REQUIRED_PLUGINS:
                continue
            fail("Enabled plugin artifact is unavailable", "plugin_not_found")
        if trusted_required(profile, name):
            continue
        record = state["plugins"].get(name, {})
        scan = record.get("scan") or {}
        approval = record.get("approval") or {}
        if scan.get("status") != "COMPLETED" or scan.get("verdict") == "dangerous":
            fail("Plugin must pass a complete scan before execution", "plugin_scan_required")
        current = tree_digest(artifact)
        if current != record.get("artifact_digest") or current != approval.get("artifact_digest"):
            fail("Plugin changed since review; scan again", "plugin_scan_stale")
        if not approval.get("approved"):
            fail("Plugin requires explicit approval")
        entry = (plugins.get("entries") or {}).get(name, {})
        if isinstance(entry, dict) and (
            entry.get("allow_tool_override")
            or "tools.override" in (entry.get("capabilities") or [])
        ):
            fail("Plugin tool overrides are not supported", "plugin_unsafe")
    # Only approved managed shell hooks are written into config. Guard them
    # again when generic config editing/restoration may have changed the bytes.
    for record in state["hooks"].values():
        if not record.get("enabled"):
            continue
        approval = record.get("approval") or {}
        if not approval.get("approved") or approval.get("digest") != record.get("command_digest"):
            fail("Shell hook requires approval", "hook_approval_required")
    if config.get("hooks"):
        from agent import shell_hooks
        from hermes_constants import reset_hermes_home_override, set_hermes_home_override

        token = set_hermes_home_override(str(profile))
        try:
            for spec in shell_hooks.iter_configured_hooks(config):
                if not shell_hooks.allowlist_entry_for(spec.event, spec.command):
                    fail("Shell hook requires explicit approval", "hook_approval_required")
        finally:
            reset_hermes_home_override(token)
