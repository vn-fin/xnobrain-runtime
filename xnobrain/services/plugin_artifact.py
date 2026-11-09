"""Private plugin artifact authority for Control-owned egress connections."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml

from ..integrations.extension_policy import (
    PLUGIN_ID,
    REQUIRED_PLUGINS,
    admit_user_plugin,
    load_state,
    read_config,
    safe_child,
    tree_digest,
)
from ..integrations.hermes_support import AgentAPIError
from ..integrations.plugin_artifacts import (
    PluginArtifactError,
    bundled_platform_path,
    canonical_egress,
    normalize_digest,
)
from ..integrations.plugin_formats import NATIVE, read_manifest
from ..integrations.workload_identity import (
    WorkloadIdentityError,
    resolve_workload_identity,
)
from .authority_actor import require_personal_actor
from .source_owner import _ProbeConflict, read_json_file

OWNER_MARKER = ".community-profile-owner.json"
BUILTIN_PLATFORM = "zalo_bot"
_OWNER_RECORD_KEYS = {"subject", "tenant_id", "organization_id"}
_UNAVAILABLE_CODES = frozenset(
    {
        "extension_recovery_required",
        "extension_state_invalid",
        "invalid_agent_config",
        "invalid_path",
    }
)


class PluginArtifactService:
    """Prove ownership and the actual approved artifact without executing it."""

    def __init__(self, platform):
        self.platform = platform
        self.agents = platform.agents

    def authority(self, body: Mapping[str, Any], trusted: Any) -> dict[str, Any]:
        actor_user, actor_tenant = require_personal_actor(trusted, label="verified Control actor")
        try:
            requested = normalize_digest(body.get("artifact_digest"))
        except PluginArtifactError:
            return {"status": "unsupported"}
        plugin = str(body.get("plugin") or "").strip().lower()
        platform = str(body.get("platform") or "").strip().lower()
        agent_id = str(body.get("agent_id") or "").strip()
        if not PLUGIN_ID.fullmatch(plugin) or not PLUGIN_ID.fullmatch(platform):
            return {"status": "unsupported"}

        profile = self._profile(agent_id)
        if profile is None:
            return {"status": "not_owned"}
        is_root = self._is_root(profile)
        if is_root:
            if not self._root_owned(actor_user, actor_tenant):
                return {"status": "not_owned"}
        elif not self._named_owned(profile, actor_user, actor_tenant):
            return {"status": "not_owned"}

        if plugin == BUILTIN_PLATFORM or platform == BUILTIN_PLATFORM:
            return self._builtin(plugin, platform, requested)
        return self._standalone(profile, plugin, requested)

    def _profile(self, agent_id: str) -> Path | None:
        if not agent_id:
            return None
        try:
            profile = Path(self.agents.profile_path(agent_id))
        except (AgentAPIError, KeyError, OSError, ValueError):
            return None
        root = Path(self.agents.root_profile).resolve()
        profiles = Path(self.agents.profiles_root).resolve()
        legacy = Path(self.agents.legacy_agents_root).resolve()
        if profile == root:
            return profile
        if profiles in profile.parents:
            return profile
        if legacy in profile.parents and profile.name == ".profile":
            return profile
        return None

    def _is_root(self, profile: Path) -> bool:
        return profile == Path(self.agents.root_profile).resolve()

    @staticmethod
    def _root_owned(actor_user: str, actor_tenant: str) -> bool:
        try:
            identity = resolve_workload_identity(
                expected_user_id=actor_user,
                expected_tenant_id=actor_tenant,
            )
        except WorkloadIdentityError:
            return False
        # Operator attestation and the legacy shared token are not ongoing
        # artifact authority. Only the Control-published source file or the
        # complete managed workload identity prove the root owner here.
        return (
            identity.kind in {"source", "managed"}
            and identity.user_id == actor_user
            and identity.tenant_id == actor_tenant
        )

    @staticmethod
    def _named_owned(profile: Path, actor_user: str, actor_tenant: str) -> bool:
        try:
            value = read_json_file(profile / OWNER_MARKER)
        except _ProbeConflict:
            return False
        if value is None or set(value) != _OWNER_RECORD_KEYS:
            return False
        return value == {
            "subject": actor_user,
            "tenant_id": actor_tenant,
            "organization_id": "",
        }

    def _builtin(self, plugin: str, platform: str, requested: str) -> dict[str, Any]:
        if plugin != BUILTIN_PLATFORM or platform != BUILTIN_PLATFORM:
            return {"status": "unsupported"}
        path = bundled_platform_path(plugin)
        if path is None:
            return {"status": "unavailable"}
        try:
            current = f"sha256:{tree_digest(path)}"
            manifest = read_manifest(path)
        except (AgentAPIError, OSError, ValueError, yaml.YAMLError):
            return {"status": "unavailable"}
        if current != requested:
            return {"status": "stale"}
        if (
            manifest.get("format") != NATIVE
            or manifest.get("name") != plugin
            or manifest.get("kind") != "platform"
        ):
            return {"status": "unavailable"}
        # Control owns the fixed Zalo Bot rule set; the builtin response never
        # supplies rules and never fabricates a per-agent install approval.
        return {
            "status": "match",
            "artifact_digest": current,
            "source": "bundled_platform",
        }

    @staticmethod
    def _admission_status(code: str) -> str:
        if code == "plugin_scan_stale":
            return "stale"
        if code in _UNAVAILABLE_CODES:
            return "unavailable"
        return "not_approved"

    def _standalone(self, profile: Path, plugin: str, requested: str) -> dict[str, Any]:
        if plugin in REQUIRED_PLUGINS:
            return {"status": "unsupported"}
        try:
            artifact = safe_child(profile, "plugins", plugin)
        except AgentAPIError:
            return {"status": "unavailable"}
        if not artifact.is_dir() or artifact.is_symlink():
            return {"status": "not_approved"}
        try:
            state = load_state(profile)
            config = read_config(profile)
            admission = admit_user_plugin(
                profile,
                plugin,
                artifact=artifact,
                state=state,
                config=config,
            )
        except AgentAPIError as error:
            return {"status": self._admission_status(error.code)}
        current = admission["artifact_digest"]
        if f"sha256:{current}" != requested:
            return {"status": "stale"}

        try:
            manifest = read_manifest(artifact)
        except (AgentAPIError, OSError, ValueError, yaml.YAMLError):
            return {"status": "unavailable"}
        if (
            manifest.get("format") != NATIVE
            or manifest.get("name") != plugin
            or manifest.get("kind", "standalone") != "standalone"
        ):
            return {"status": "unsupported"}
        try:
            rules = canonical_egress(manifest.get("egress"))
        except PluginArtifactError:
            return {"status": "unsupported"}
        return {
            "status": "match",
            "artifact_digest": f"sha256:{current}",
            "source": "user",
            "rules": rules,
        }
