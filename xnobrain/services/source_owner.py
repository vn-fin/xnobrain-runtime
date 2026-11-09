"""Private source-owner probe: status-only proof over configured local state."""

from __future__ import annotations

import json
import os
import stat
import time
from itertools import islice
from pathlib import Path
from typing import Any, Mapping

from ..defaults import BIG_BROTHER_AGENT_ID
from ..integrations.hermes_support import AGENT_NAME_RE, DEFAULT_LEGACY_AGENTS_ROOT
from ..integrations.workload_identity import (
    WorkloadIdentityError,
    read_source_identity_file,
    read_source_owner_attestation,
)
from .authority_actor import require_personal_actor

MAX_PROFILES = 512
MAX_MARKER_BYTES = 4096
SCAN_DEADLINE_SECONDS = 2.0
_OWNER_MARKER = ".community-profile-owner.json"
_CHANNEL_OWNER = Path(".xnobrain") / "channels" / "owner.json"


class _ProbeConflict(Exception):
    """A malformed, mixed, or unreadable owner record fails the probe closed."""


class SourceOwnerService:
    """Inspect only configured owner markers; never write or claim state."""

    def __init__(self, platform):
        self.platform = platform
        self.repository = platform.repository
        agents = getattr(platform, "agents", None)
        self.root_profile = Path(
            getattr(agents, "root_profile", None) or platform.config.root_profile
        )
        self.profiles_root = Path(
            getattr(agents, "profiles_root", None) or self.repository.profiles_root
        )
        self.legacy_agents_root = Path(
            getattr(agents, "legacy_agents_root", None)
            or os.getenv("HERMES_LEGACY_AGENTS_ROOT")
            or os.getenv("HERMES_AGENTS_ROOT")
            or DEFAULT_LEGACY_AGENTS_ROOT
        )

    def probe(self, body: Mapping[str, Any], trusted: Any) -> dict[str, str]:
        del body  # environment/runtime_source_key are labels; actor is signed.
        actor_user, actor_tenant = self._actor(trusted)

        identity = None
        try:
            identity = read_source_identity_file(
                expected_user_id=actor_user,
                expected_tenant_id=actor_tenant,
            )
        except WorkloadIdentityError as error:
            if error.reason != "identity_unavailable":
                return {"status": "mismatch"}
            # A future explicit identity path is a valid enrollment bootstrap
            # state only for this probe; exact operator attestation still has
            # to match below. The grant reader remains fail-closed.

        try:
            self._scan_profiles(
                self.root_profile,
                self.profiles_root,
                self.legacy_agents_root,
                actor_user,
                actor_tenant,
            )
        except _ProbeConflict:
            return {"status": "mismatch"}

        if identity is not None:
            return {"status": "match"}

        try:
            attestation = read_source_owner_attestation()
        except WorkloadIdentityError:
            return {"status": "legacy_unproven"}
        if attestation is None:
            # Matching per-agent markers never prove unmarked workspace data,
            # and absence of markers is not freshness.
            return {"status": "legacy_unproven"}
        if attestation != (actor_user, actor_tenant):
            return {"status": "mismatch"}
        return {"status": "match"}

    @staticmethod
    def _actor(trusted: Any) -> tuple[str, str]:
        return require_personal_actor(trusted, label="verified source owner")

    def _scan_profiles(
        self,
        root: Path,
        profiles_root: Path,
        legacy_agents_root: Path,
        actor_user: str,
        actor_tenant: str,
    ) -> int:
        deadline = time.monotonic() + SCAN_DEADLINE_SECONDS
        if root.is_symlink() or profiles_root.is_symlink() or legacy_agents_root.is_symlink():
            raise _ProbeConflict
        directories: list[tuple[Path, str]] = []
        seen: set[str] = set()
        if root.is_dir():
            directories.append((root, BIG_BROTHER_AGENT_ID))
            seen.add(root.absolute().as_posix())

        if profiles_root.is_dir():
            entries = self._bounded_entries(
                profiles_root, MAX_PROFILES - len(directories), deadline
            )
            for entry in entries:
                if entry.is_symlink():
                    raise _ProbeConflict
                key = entry.absolute().as_posix()
                if entry.is_dir() and key not in seen:
                    directories.append((entry, entry.name))
                    seen.add(key)

        remaining = MAX_PROFILES - len(directories)
        if legacy_agents_root.is_dir():
            entries = self._bounded_entries(legacy_agents_root, remaining, deadline)
            for entry in entries:
                if time.monotonic() > deadline:
                    raise _ProbeConflict
                if entry.is_symlink():
                    raise _ProbeConflict
                if not entry.is_dir() or not AGENT_NAME_RE.fullmatch(entry.name):
                    continue
                profile = entry / ".profile"
                if profile.is_symlink():
                    raise _ProbeConflict
                if not profile.is_dir():
                    continue
                key = profile.absolute().as_posix()
                if key in seen:
                    continue
                directories.append((profile, entry.name))
                seen.add(key)

        if len(directories) > MAX_PROFILES:
            raise _ProbeConflict
        for directory, logical_agent_id in directories:
            if time.monotonic() > deadline:
                raise _ProbeConflict
            self._check_marker(directory, actor_user, actor_tenant)
            self._check_channel_owner(directory, logical_agent_id, actor_user, actor_tenant)
        return len(directories)

    @staticmethod
    def _bounded_entries(path: Path, maximum: int, deadline: float) -> list[Path]:
        if maximum < 0:
            raise _ProbeConflict
        try:
            entries = list(islice(path.iterdir(), maximum + 1))
        except OSError as error:
            raise _ProbeConflict from error
        if len(entries) > maximum:
            raise _ProbeConflict
        entries.sort(key=lambda item: item.name)
        for _entry in entries:
            if time.monotonic() > deadline:
                raise _ProbeConflict
        return entries

    def _check_marker(self, profile: Path, actor_user: str, actor_tenant: str) -> None:
        value = read_json_file(profile / _OWNER_MARKER)
        if value is None:
            return
        subject = value.get("subject")
        tenant = value.get("tenant_id")
        organization = value.get("organization_id")
        if (
            not isinstance(subject, str)
            or not isinstance(tenant, str)
            or subject != actor_user
            or tenant != actor_tenant
            or organization
        ):
            raise _ProbeConflict

    def _check_channel_owner(
        self,
        profile: Path,
        logical_agent_id: str,
        actor_user: str,
        actor_tenant: str,
    ) -> None:
        metadata = profile / ".xnobrain"
        channels = metadata / "channels"
        if metadata.is_symlink() or channels.is_symlink():
            raise _ProbeConflict
        value = read_json_file(profile / _CHANNEL_OWNER)
        if value is None:
            return
        user_id = value.get("user_id")
        tenant_id = value.get("tenant_id")
        agent_id = value.get("agent_id")
        organization = value.get("organization_id")
        if (
            not isinstance(user_id, str)
            or not isinstance(tenant_id, str)
            or not isinstance(agent_id, str)
            or user_id != actor_user
            or tenant_id != actor_tenant
            or agent_id != logical_agent_id
            or organization
        ):
            raise _ProbeConflict


def read_json_file(path: Path) -> dict | None:
    if path.is_symlink():
        raise _ProbeConflict
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
    except FileNotFoundError:
        return None
    except OSError as error:
        raise _ProbeConflict from error
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise _ProbeConflict
        if details.st_size <= 0 or details.st_size > MAX_MARKER_BYTES:
            raise _ProbeConflict
        chunks = []
        remaining = MAX_MARKER_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
    finally:
        os.close(descriptor)
    if len(raw) > MAX_MARKER_BYTES:
        raise _ProbeConflict
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise _ProbeConflict from error
    if not isinstance(value, dict):
        raise _ProbeConflict
    return value
