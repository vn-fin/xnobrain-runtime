"""Pinned Hermes pairing-store adapter for channel pairing management.

Runtime does not own a second pairing store. Every read and mutation goes
through the pinned ``gateway.pairing.PairingStore`` used by the gateway's
profile-scoped authorization checks.

Cross-process mutation safety is layered at the existing launcher/factory
seams, not by editing the pinned engine. The API adapter and the gateway
launcher wrap the same pinned mutating methods with one canonical
``<pairing-dir>/.xnobrain-pairing.lock`` flock. The engine keeps its own
thread lock and atomic JSON save. Remove this wrapper once the pinned engine
provides an equivalent process-shared lock and public per-request deny.

The pinned engine exposes public ``list_pending``, ``list_approved``,
``approve_request``, and ``revoke``, but no per-request deny. Denial therefore
uses the store's own ``_lock`` and atomic ``_save_json`` to remove exactly one
pending request. This is the safe adapter option: it shares the engine lock and
store scope, never rewrites JSON unlocked, and never clears all pending
requests. A future engine ``deny_request`` should replace it.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from gateway.pairing import CODE_TTL_SECONDS, PairingStore
from hermes_constants import get_default_hermes_root

from ..repositories.file_lock import FileLock, FileLockTimeout

PAIRING_CODE_TTL_SECONDS = CODE_TTL_SECONDS
PAIRING_LOCK_TIMEOUT_SECONDS = 5.0
_CACHE_MAX = 64
_CACHE_LOCK = threading.RLock()
_CACHE: "OrderedDict[tuple[str, str, str], ChannelPairingStore]" = OrderedDict()
_LOGGER = logging.getLogger(__name__)


def pairing_lock_path_for_directory(directory: str | Path) -> Path:
    """Return the canonical lock path inside one validated pairing directory."""
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError("pairing store directory is unsafe")
    root = get_default_hermes_root().resolve()
    bases = [root]
    # Managed layouts symlink <root>/profiles to the shared profiles root, so
    # named-profile stores resolve beside <root> rather than beneath it.
    profiles = root / "profiles"
    if profiles.exists():
        bases.append(profiles.resolve())
    resolved = directory.resolve()
    if not any(resolved.is_relative_to(base) for base in bases):
        raise ValueError("pairing store directory escapes the Hermes root")
    return resolved / ".xnobrain-pairing.lock"


def pairing_lock_path(store: PairingStore) -> Path:
    """Return the canonical lock path inside one validated pairing store."""
    return pairing_lock_path_for_directory(store._dir)


def _pairing_file_lock(store: PairingStore) -> FileLock:
    return FileLock(
        pairing_lock_path(store),
        timeout=PAIRING_LOCK_TIMEOUT_SECONDS,
    )


_CONSTRUCTOR_LOCKING = False
_CONSTRUCTOR_LOCK = threading.RLock()


def install_pairing_constructor_locking() -> None:
    """Lock the pinned store's split-directory migration during construction."""
    global _CONSTRUCTOR_LOCKING
    with _CONSTRUCTOR_LOCK:
        if _CONSTRUCTOR_LOCKING:
            return
        from gateway import pairing as pairing_module

        original = pairing_module._migrate_split_pairing_dirs

        def locked_migrate(*, home=None, active=None):
            directory = Path(active) if active is not None else Path(pairing_module.PAIRING_DIR)
            with FileLock(
                pairing_lock_path_for_directory(directory),
                timeout=PAIRING_LOCK_TIMEOUT_SECONDS,
            ):
                return original(home=home, active=active)

        pairing_module._migrate_split_pairing_dirs = locked_migrate
        _CONSTRUCTOR_LOCKING = True


class ChannelPairingStore:
    """Resolve the same PairingStore scope the gateway uses for one agent."""

    def __init__(self, agent_id: str, root_profile_name: str):
        install_pairing_constructor_locking()
        # The pinned gateway uses PairingStore() for its active/default scope
        # and PairingStore(profile=name) for named multiplex profiles.
        if agent_id == root_profile_name:
            self.store = PairingStore(profile="default")
        else:
            self.store = PairingStore(profile=agent_id)

    def pending(self, platform: str) -> list[dict[str, Any]]:
        # The pinned list_pending also prunes expired rows and writes them back.
        with _pairing_file_lock(self.store):
            return self.store.list_pending(platform)

    def approved(self, platform: str) -> list[dict[str, Any]]:
        return self.store.list_approved(platform)

    def approve(self, platform: str, request_id: str) -> dict[str, Any] | None:
        with _pairing_file_lock(self.store):
            return self.store.approve_request(platform, request_id)

    def deny(self, platform: str, request_id: str) -> str:
        """Deny one pending request under the process-shared store lock."""
        store = self.store
        with _pairing_file_lock(store):
            with store._lock:
                path = store._pending_path(platform)
                pending = store._load_json(path)
                entry = pending.get(request_id)
                if entry is None:
                    return "not_found"
                created_at = entry.get("created_at") if isinstance(entry, dict) else None
                if not isinstance(created_at, (int, float)):
                    del pending[request_id]
                    store._save_json(path, pending)
                    return "not_found"
                if time.time() - created_at > CODE_TTL_SECONDS:
                    del pending[request_id]
                    store._save_json(path, pending)
                    return "expired"
                del pending[request_id]
                store._save_json(path, pending)
                return "denied"

    def revoke(self, platform: str, user_id: str) -> bool:
        with _pairing_file_lock(self.store):
            return self.store.revoke(platform, user_id)

    def is_approved(self, platform: str, user_id: str) -> bool:
        return self.store.is_approved(platform, user_id)


def get_channel_pairing_store(agent_id: str, root_profile_name: str) -> ChannelPairingStore:
    """Return a bounded shared per-profile adapter for concurrent API calls."""
    key = (str(agent_id), str(root_profile_name), str(get_default_hermes_root()))
    with _CACHE_LOCK:
        adapter = _CACHE.get(key)
        if adapter is not None:
            _CACHE.move_to_end(key)
            return adapter
        adapter = ChannelPairingStore(agent_id, root_profile_name)
        _CACHE[key] = adapter
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
        return adapter


_GATEWAY_LOCKED_METHODS = {
    "generate_code": None,
    "approve_code": None,
    "approve_request": None,
    "revoke": False,
    "clear_pending": 0,
    "list_pending": list,
}


def install_gateway_pairing_locking() -> None:
    """Wrap pinned gateway mutations with the same per-store flock.

    This runs in the launcher process before the pinned gateway starts. It does
    not modify the installed package and keeps unrelated profiles isolated by
    resolving one lock path per PairingStore instance.
    """
    install_pairing_constructor_locking()
    if getattr(PairingStore, "_xnobrain_pairing_locking", False):
        return
    for name, default in _GATEWAY_LOCKED_METHODS.items():
        original = getattr(PairingStore, name)

        def locked(self, *args, _original=original, _default=default, **kwargs):
            try:
                with _pairing_file_lock(self):
                    return _original(self, *args, **kwargs)
            except (FileLockTimeout, OSError, ValueError) as error:
                _LOGGER.warning(
                    "gateway pairing lock unavailable (%s); failing closed",
                    type(error).__name__,
                )
                return _default() if callable(_default) else _default

        setattr(PairingStore, name, locked)
    PairingStore._xnobrain_pairing_locking = True
