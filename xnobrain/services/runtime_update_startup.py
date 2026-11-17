"""Defer managed profile repairs until durable rollout verification completes."""

from __future__ import annotations

import asyncio
import logging

import yaml

from ..repositories.base import StoreError
from ..repositories.runtime_update_gate import WorkspaceActivity


def cleanup_legacy_catalog_mcp(platform) -> None:
    """Remove catalog OAuth credentials and plaintext headers from profiles."""
    profiles = []
    root_profile = platform.repository.root_profile
    if root_profile and not root_profile.is_symlink() and root_profile.is_dir():
        profiles.append(root_profile)
    profiles_root = platform.repository.profiles_root
    if not profiles_root.is_symlink() and profiles_root.is_dir():
        profiles.extend(
            path for path in profiles_root.iterdir() if not path.is_symlink() and path.is_dir()
        )

    from ..integrations import extension_policy
    from ..integrations.hermes_support import AgentAPIError

    for profile in profiles:
        tokens = profile / "mcp-tokens"
        if not tokens.is_symlink() and tokens.is_dir():
            for token_file in tokens.iterdir():
                if token_file.name.startswith("xnobrain-"):
                    try:
                        token_file.lstat()
                        token_file.unlink()
                    except OSError:
                        continue

        config_path = profile / "config.yaml"
        if config_path.is_symlink() or not config_path.is_file():
            continue
        try:
            config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(config, dict):
            continue
        servers = config.get(extension_policy.MANAGED_MCP_KEY)
        if not isinstance(servers, dict):
            continue
        removed_names = {
            name
            for name, server in servers.items()
            if isinstance(name, str)
            and name.startswith("xnobrain-")
            and isinstance(server, dict)
            and ("headers" in server or str(server.get("auth") or "").lower() == "oauth")
        }
        if not removed_names:
            continue
        for name in removed_names:
            servers.pop(name, None)
        config[extension_policy.MANAGED_MCP_KEY] = servers
        platform.repository.atomic_yaml(config_path, config)
        try:
            state = extension_policy.load_state(profile)
        except (AgentAPIError, OSError):
            continue
        state["mcp"] = {
            catalog_id: record
            for catalog_id, record in state.get("mcp", {}).items()
            if not isinstance(record, dict) or record.get("server_name") not in removed_names
        }
        state["revision"] = int(state.get("revision", 1)) + 1
        extension_policy.save_state(profile, state)


async def finish_startup(platform, *, deferred: bool = False) -> None:
    # Maintenance is durable across process replacement. Read it on every
    # iteration; both verified resume and verified abort release this barrier.
    deferred = deferred or platform.runtime_updates.dispatch_paused
    while True:
        while platform.runtime_updates.dispatch_paused:
            await asyncio.sleep(0.25)
        try:
            activity = WorkspaceActivity(platform.repository.data_dir)
        except StoreError as error:
            if error.code not in {
                "runtime_update_maintenance",
                "workspace_rebalance_maintenance",
                "workspace_memory_maintenance",
            }:
                raise
            deferred = True
            await asyncio.sleep(0.25)
            continue
        with activity:
            try:
                cleanup_legacy_catalog_mcp(platform)
            except Exception:  # noqa: BLE001 - legacy dev cleanup must never block startup
                logging.getLogger(__name__).warning("Legacy catalog MCP cleanup failed")
            if deferred:
                platform.portability._cleanup_transfers()
                platform.config.ensure_write_approval_defaults()
                platform._ensure_existing_write_approval_defaults()
            await platform.ensure_default_agent()
        return
