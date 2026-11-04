"""Discard executable-extension trust when a profile crosses an import boundary."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .extension_policy import MANAGED_MCP_KEY


def reset_imported_extension_trust(profile: Path, config: dict[str, Any]) -> None:
    """Keep plugin artifacts inert; an archive cannot approve its own code.

    Called only on the validated, private import staging tree, before publication.
    Required plugins are restored by the receiving Runtime's template installer,
    rather than trusting a protected name supplied by an archive.
    """
    plugin_config = config.get("plugins")
    if not isinstance(plugin_config, dict):
        plugin_config = {}
    disabled: set[str] = set()
    for key in ("enabled", "disabled"):
        values = plugin_config.get(key)
        if isinstance(values, list):
            disabled.update(value for value in values if isinstance(value, str))
    plugin_root = profile / "plugins"
    if plugin_root.is_dir() and not plugin_root.is_symlink():
        disabled.update(child.name for child in plugin_root.iterdir() if child.is_dir())
    if plugin_config or disabled:
        plugin_config["enabled"] = []
        plugin_config["disabled"] = sorted(disabled)
        plugin_config.pop("entries", None)
        config["plugins"] = plugin_config

    # A noninteractive engine can auto-accept configured shell hooks. Import
    # must remove their executable config, not merely delete an approval file.
    config.pop("hooks", None)
    config.pop(MANAGED_MCP_KEY, None)
    for filename in ("shell-hooks-allowlist.json",):
        (profile / filename).unlink(missing_ok=True)
    extension_state = profile / ".xnobrain" / "extensions"
    if extension_state.is_symlink():
        extension_state.unlink()
    elif extension_state.exists():
        shutil.rmtree(extension_state)
    # Gateway directory hooks have a separate loader, independent of `hooks:`
    # in config.yaml. Retain their files outside its discovery directory.
    gateway_hooks = profile / "hooks"
    if gateway_hooks.is_symlink():
        gateway_hooks.unlink()
    elif gateway_hooks.is_dir():
        quarantine = profile / ".xnobrain" / "imported-gateway-hooks"
        quarantine.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if quarantine.exists():
            shutil.rmtree(quarantine)
        gateway_hooks.rename(quarantine)
