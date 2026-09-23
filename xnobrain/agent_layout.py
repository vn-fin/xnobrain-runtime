"""Operator-owned profile layout, resolved before importing the embedded engine.

Legacy deployments remain on their existing profiles until an explicit cutover.
This module never moves data and does not use request-controlled paths.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, MutableMapping

DEFAULT_AGENT_DATA_ROOT = Path("/opt/data/agent")


@dataclass(frozen=True)
class AgentLayout:
    root_profile: Path
    profiles_root: Path
    canonical: bool


def resolve_layout(env: Mapping[str, str]) -> AgentLayout:
    configured = env.get("RUNTIME_AGENT_DATA_ROOT", "").strip()
    legacy_root = (
        env.get("RUNTIME_HERMES_ROOT_PROFILE")
        or env.get("HERMES_ROOT_PROFILE")
        or env.get("RUNTIME_HERMES_HOME")
        or env.get("HERMES_HOME")
    )
    legacy_profiles = env.get("RUNTIME_HERMES_PROFILES_ROOT") or env.get("HERMES_PROFILES_ROOT")
    if not configured and legacy_root:
        root = Path(legacy_root)
        return AgentLayout(
            root, Path(legacy_profiles) if legacy_profiles else root / "profiles", False
        )
    base = Path(configured) if configured else DEFAULT_AGENT_DATA_ROOT
    if not base.is_absolute() or ".." in base.parts:
        raise ValueError("RUNTIME_AGENT_DATA_ROOT must be an absolute normalized path")
    for node in (base, *base.parents):
        if node.is_symlink():
            raise ValueError("Agent data root must not contain symlinks")
    for source in (
        Path("/opt/xnobrain"),
        Path("/opt/xnobrain-app"),
        Path("/usr/local/lib/hermes-agent"),
        Path("/workspace"),
    ):
        if base.is_relative_to(source) or source.is_relative_to(base):
            raise ValueError("Agent data root must be disjoint from source and dependencies")
    root = base / "big-brother"
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise ValueError("Root profile must be a real directory")
    if base.exists() and not base.is_dir():
        raise ValueError("Agent data root must be a directory")
    # Check every supplied alias, not only the highest-precedence value.
    for key in (
        "RUNTIME_HERMES_HOME",
        "HERMES_HOME",
        "RUNTIME_HERMES_ROOT_PROFILE",
        "HERMES_ROOT_PROFILE",
    ):
        if env.get(key) and Path(env[key]) != root:
            raise ValueError("Conflicting profile roots; explicit offline migration is required")
    for key in ("RUNTIME_HERMES_PROFILES_ROOT", "HERMES_PROFILES_ROOT"):
        if env.get(key) and Path(env[key]) != base:
            raise ValueError("Conflicting profiles root; explicit offline migration is required")
    if legacy_root and Path(legacy_root) != root:
        raise ValueError("Conflicting profile roots; explicit offline migration is required")
    if legacy_profiles and Path(legacy_profiles) != base:
        raise ValueError("Conflicting profiles root; explicit offline migration is required")
    if not configured:
        candidates = (
            Path(env.get("HOME", str(Path.home()))) / ".hermes",
            Path("/opt/data/hermes/root"),
            Path("/srv/xnobrain-data/hermes/root"),
        )
        if any(item.is_dir() and any(item.iterdir()) for item in candidates):
            raise ValueError("Legacy agent data found; configure its root or migrate explicitly")
    return AgentLayout(root, base, True)


def configure_layout(env: MutableMapping[str, str] | None = None) -> AgentLayout:
    target = os.environ if env is None else env
    layout = resolve_layout(target)
    target["HERMES_HOME"] = str(layout.root_profile)
    target["HERMES_ROOT_PROFILE"] = str(layout.root_profile)
    target["HERMES_PROFILES_ROOT"] = str(layout.profiles_root)
    return layout
