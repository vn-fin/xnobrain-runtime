"""Per-profile extension orchestration and approval policy."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from ..integrations import extension_engine, extension_policy, extension_recovery
from ..repositories.base import StoreError
from ..repositories.custom_page_locks import lifecycle_gate
from ..repositories.extension_operations import ExtensionOperationStore
from ..repositories.plugin_catalog import load_plugin_catalog
from .base import ServiceError

_PLUGIN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_GIT_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
_MAX_CLONE_BYTES = 8 * 1024 * 1024
_CLONE_TIMEOUT = 300
_LOGGER = logging.getLogger(__name__)


class ExtensionsService:
    def __init__(self, repository, agents):
        self.repository = repository
        self.agents = agents
        self.store = ExtensionOperationStore(repository.data_dir)
        self.worker = ExtensionOperationWorker(self)

    def _profile(
        self, agent_id: str, trusted: Any, *, recover_publication: bool = True
    ) -> tuple[Path, str]:
        self._require_feature()
        if not getattr(trusted, "subject", "") or not getattr(trusted, "tenant_id", ""):
            raise ServiceError(
                "Verified personal identity required", status=401, code="identity_required"
            )
        ownership = dict(getattr(trusted, "ownership_context", None) or {})
        if getattr(trusted, "organization_id", "") or ownership.get("owner_kind") == "organization":
            raise ServiceError("Personal workspace required", status=403, code="permission_denied")
        if ownership.get("state", "active") != "active":
            raise ServiceError(
                "Personal workspace is inactive", status=403, code="permission_denied"
            )
        try:
            profile = Path(self.agents.profile_path(agent_id))
        except Exception as error:
            raise ServiceError("Agent profile not found", status=404, code="not_found") from error
        if profile.is_symlink() or not profile.is_dir():
            raise ServiceError("Agent profile not found", status=404, code="not_found")
        profile = profile.resolve()
        if (
            profile != self.repository.root_profile
            and self.repository.profiles_root not in profile.parents
        ):
            raise ServiceError("Invalid agent profile", status=404, code="not_found")
        marker = profile / ".community-profile-owner.json"
        if marker.is_symlink():
            raise ServiceError(
                "Profile ownership unavailable", status=403, code="permission_denied"
            )
        if marker.exists():
            try:
                owner = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                raise ServiceError(
                    "Profile ownership unavailable", status=403, code="permission_denied"
                ) from error
            expected = {
                "subject": trusted.subject,
                "tenant_id": trusted.tenant_id,
                "organization_id": "",
            }
            if owner != expected:
                raise ServiceError(
                    "Profile ownership unavailable", status=403, code="permission_denied"
                )
        if recover_publication:
            self._recover_publication(agent_id, profile)
            if (extension_policy.state_root(profile) / "mcp-recovery.json").exists():
                with self._lifecycle(agent_id):
                    self._recover_mcp_mirror(profile)
        return profile, f"{trusted.tenant_id}\0{trusted.subject}"

    @staticmethod
    def _require_feature() -> None:
        from ..feature_flags import FEATURE_AGENT_EXTENSIONS, enabled

        if not enabled(FEATURE_AGENT_EXTENSIONS):
            raise ServiceError("Agent extensions are disabled", status=404, code="not_found")

    @staticmethod
    def _state(profile: Path) -> dict[str, Any]:
        return extension_policy.load_state(profile)

    @staticmethod
    def _save(profile: Path, state: dict[str, Any]) -> None:
        extension_policy.save_state(profile, state)

    @contextmanager
    def _lifecycle(self, agent_id: str):
        try:
            with lifecycle_gate(self.repository.data_dir, agent_id):
                yield
        except StoreError as error:
            if error.code == "custom_page_jobs_active":
                raise ServiceError(
                    "Agent has an active run", status=409, code="agent_busy"
                ) from error
            raise

    def _snapshot_config(self, agent_id: str, profile: Path) -> bytes | None:
        path = profile / "config.yaml"
        if path.is_symlink():
            raise ServiceError("Unsafe profile config", status=409, code="invalid_profile")
        if not path.exists():
            return None
        if not path.is_file():
            raise ServiceError(
                "Profile config is invalid", status=409, code="invalid_profile_config"
            )
        content = path.read_bytes()
        self.repository.snapshot(agent_id, "config", "config", content)
        return content

    def _profile_generation(self, profile: Path, *, initialize: bool = False) -> str:
        state = self._state(profile)
        generation = state.get("profile_generation")
        if isinstance(generation, str) and re.fullmatch(r"[a-f0-9]{32}", generation):
            return generation
        if not initialize:
            raise ServiceError(
                "Agent profile was recreated after operation acceptance",
                status=409,
                code="profile_generation_conflict",
            )
        generation = uuid.uuid4().hex
        state["profile_generation"] = generation
        self._save(profile, state)
        return generation

    def _require_profile_generation(self, profile: Path, expected: Any) -> None:
        if not isinstance(expected, str) or self._profile_generation(profile) != expected:
            raise ServiceError(
                "Agent profile was recreated after operation acceptance",
                status=409,
                code="profile_generation_conflict",
            )

    def _recover_publication(self, agent_id: str, profile: Path) -> bool:
        try:
            intent = extension_recovery.publication_path(profile)
            if not intent.exists():
                return False
            with self._lifecycle(agent_id):
                return extension_recovery.recover_publication(
                    profile,
                    staging_root=self.repository.data_dir / "extension-staging",
                )
        except extension_recovery.ExtensionRecoveryError as error:
            raise ServiceError(
                "Plugin publication recovery is required",
                status=503,
                code="extension_recovery_required",
            ) from error

    def _recover_all_publications(self) -> None:
        candidates = []
        root_profile = self.repository.root_profile
        if root_profile is not None and root_profile.is_dir() and not root_profile.is_symlink():
            candidates.append(("big-brother", root_profile))
        profiles_root = self.repository.profiles_root
        if profiles_root.is_dir() and not profiles_root.is_symlink():
            for profile in profiles_root.iterdir():
                if profile.is_dir() and not profile.is_symlink():
                    candidates.append((profile.name, profile))
        for agent_id, profile in candidates:
            try:
                self._recover_publication(agent_id, profile)
                extension_recovery.discard_unjournaled_stages(profile)
            except (ServiceError, StoreError) as error:
                _LOGGER.warning(
                    "Plugin publication recovery deferred",
                    extra={
                        "agent_id": agent_id,
                        "error_code": getattr(error, "code", "recovery_failed"),
                    },
                )
        try:
            extension_recovery.discard_staging_root(self.repository.data_dir / "extension-staging")
        except extension_recovery.ExtensionRecoveryError:
            _LOGGER.warning(
                "Extension staging cleanup deferred",
                extra={"error_code": "extension_recovery_required"},
            )

    def catalog(self) -> dict[str, Any]:
        self._require_feature()
        try:
            catalog = load_plugin_catalog()
        except (ValueError, OSError) as error:
            raise ServiceError(
                "Extension catalog is unavailable", status=503, code="catalog_unavailable"
            ) from error
        isolated = extension_engine.mcp_profile_isolation_supported()
        catalog["engine_capabilities"] = {"mcp_profile_isolation": isolated}
        if not isolated:
            for entry in catalog.get("entries", []):
                if entry.get("installability") == "installable":
                    entry["capability"] = {
                        "installable": False,
                        "reason_code": "mcp_profile_isolation_unavailable",
                    }
        return catalog

    def list_plugins(self, agent_id: str, trusted: Any) -> dict[str, Any]:
        profile, _owner = self._profile(agent_id, trusted)
        state = self._state(profile)
        try:
            plugins = extension_engine.plugin_inventory(profile)
            mcp_status = extension_engine.mcp_status(profile)
        except (ImportError, AttributeError, RuntimeError) as error:
            raise ServiceError(
                "Extension runtime is unavailable", status=503, code="extension_engine_unavailable"
            ) from error
        records = state["plugins"]
        result = []
        for item in plugins:
            plugin_id = str(item.get("id") or "")
            if not _PLUGIN_ID.fullmatch(plugin_id):
                continue
            record = records.get(plugin_id, {})
            artifact = profile / "plugins" / plugin_id
            try:
                digest = extension_policy.tree_digest(artifact) if artifact.is_dir() else ""
            except Exception:
                digest = ""
            required = extension_policy.trusted_required(profile, plugin_id)
            approval = record.get("approval") or {}
            scan = record.get("scan") or {"status": "NOT_SCANNED", "verdict": "unknown"}
            if required:
                scan = {
                    "status": "COMPLETED",
                    "verdict": "system",
                    "coverage": {"files_scanned": 0, "unsupported_files": []},
                    "findings": [],
                }
            result.append(
                {
                    "id": plugin_id,
                    "name": item.get("name", plugin_id),
                    "version": item.get("version", ""),
                    "source": item.get("source", "unknown"),
                    "enabled": bool(item.get("enabled")),
                    "hidden": bool(record.get("hidden", False)),
                    "required": required,
                    "system_managed": required,
                    "artifact_digest": digest,
                    "status": item.get("status", "enabled" if item.get("enabled") else "disabled"),
                    "revision": int(record.get("revision", 1)),
                    "provides_hooks": self._safe_names(item.get("provides_hooks")),
                    "provides_skills": self._safe_names(item.get("provides_skills")),
                    "scan": self._public_scan(scan),
                    "approval": {
                        "required": not required,
                        "approved": required
                        or (
                            bool(approval.get("approved"))
                            and approval.get("artifact_digest") == digest
                        ),
                        "artifact_digest": digest if required else approval.get("artifact_digest"),
                    },
                }
            )
        mcp_rows = []
        by_id = {str(item.get("catalog_id")): item for item in mcp_status if isinstance(item, dict)}
        for catalog_id, record in state["mcp"].items():
            server_name = record.get("server_name", "")
            status = by_id.get(catalog_id, {})
            mcp_rows.append(
                {
                    "catalog_id": catalog_id,
                    "server_name": server_name,
                    "enabled": bool(record.get("enabled", True)),
                    "auth_configured": bool(record.get("auth_configured", False)),
                    "connected": bool(status.get("connected", False)),
                    "status": status.get("status", "configured"),
                    "revision": int(record.get("revision", 1)),
                }
            )
        return {"agent_id": agent_id, "plugins": result, "mcp_servers": mcp_rows}

    @staticmethod
    def _safe_names(values: Any) -> list[str]:
        if not isinstance(values, list):
            return []
        return [value[:128] for value in values if isinstance(value, str) and 0 < len(value) <= 128]

    @staticmethod
    def _public_scan(scan: dict[str, Any]) -> dict[str, Any]:
        coverage = scan.get("coverage") or {}
        if not isinstance(coverage, dict):
            coverage = {
                "files_scanned": int(scan.get("files", 0)),
                "unsupported_files": []
                if coverage == "complete_text"
                else ["unscanned_or_nontext"],
            }
        return {
            "status": scan.get("status", "BLOCKED"),
            "verdict": scan.get("verdict", "unknown"),
            "coverage": {
                "files_scanned": int(coverage.get("files_scanned", 0)),
                "unsupported_files": list(coverage.get("unsupported_files", [])),
            },
            "findings": [
                {
                    "severity": str(item.get("severity", "")),
                    "category": str(item.get("category", "")),
                    "file": str(item.get("file", "")),
                    "line": item.get("line"),
                    "description": str(item.get("description", item.get("message", ""))),
                }
                for item in scan.get("findings", [])
                if isinstance(item, dict)
            ],
        }

    def install_catalog(self, agent_id: str, body: dict[str, Any], trusted: Any) -> dict[str, Any]:
        profile, _owner = self._profile(agent_id, trusted)
        catalog = self.catalog()
        entry = next(
            (item for item in catalog["entries"] if item["id"] == body["catalog_id"]), None
        )
        if entry is None:
            raise ServiceError(
                "Catalog entry not found", status=404, code="catalog_entry_not_found"
            )
        if body["recipe_revision"] != entry["recipe_revision"]:
            raise ServiceError(
                "Catalog recipe changed; refresh and retry",
                status=409,
                code="recipe_revision_conflict",
            )
        if entry.get("installability") != "installable":
            raise ServiceError(
                "Catalog entry is not installable", status=422, code="catalog_entry_unsupported"
            )
        if not extension_engine.mcp_profile_isolation_supported():
            raise ServiceError(
                "MCP profile isolation is unavailable in this Runtime",
                status=503,
                code="plugins_unavailable",
            )
        if entry.get("mcp", {}).get("transport") != "http":
            raise ServiceError(
                "Only remote HTTP integrations are supported",
                status=422,
                code="unsupported_transport",
            )
        server_config, endpoint_mode, auth_configured = self._catalog_server_config(
            entry, body.get("fields", {})
        )
        server_name = f"xnobrain-{entry['id']}"
        with self._lifecycle(agent_id):
            self._recover_mcp_mirror(profile)
            config_path = profile / "config.yaml"
            if config_path.is_symlink():
                raise ServiceError("Unsafe profile config", status=409, code="invalid_profile")
            try:
                config = (
                    yaml.safe_load(config_path.read_text(encoding="utf-8"))
                    if config_path.is_file()
                    else {}
                )
            except (OSError, yaml.YAMLError) as error:
                raise ServiceError(
                    "Profile config is invalid", status=409, code="invalid_profile_config"
                ) from error
            if config is None:
                config = {}
            if not isinstance(config, dict):
                raise ServiceError(
                    "Profile config is invalid", status=409, code="invalid_profile_config"
                )
            servers = config.get(extension_policy.MANAGED_MCP_KEY) or {}
            if not isinstance(servers, dict):
                raise ServiceError("MCP config is invalid", status=409, code="invalid_mcp_config")
            prior = servers.get(server_name)
            prior_record = self._state(profile)["mcp"].get(entry["id"])
            if prior and prior_record:
                if prior_record.get("server_name") != server_name:
                    raise ServiceError(
                        "Server name is already in use", status=409, code="mcp_server_conflict"
                    )
            elif prior:
                raise ServiceError(
                    "Server name is already in use", status=409, code="mcp_server_conflict"
                )
            servers[server_name] = server_config
            config[extension_policy.MANAGED_MCP_KEY] = servers
            from ..integrations.llm_router_support import normalize_llm_router_config

            normalize_llm_router_config(config)
            self._snapshot_config(agent_id, profile)
            self._profile_generation(profile, initialize=True)
            state = self._state(profile)
            old = state["mcp"].get(entry["id"], {})
            state["mcp"][entry["id"]] = {
                "server_name": server_name,
                "enabled": True,
                "auth_configured": auth_configured,
                "endpoint_mode": endpoint_mode,
                "recipe_revision": entry["recipe_revision"],
                "revision": int(old.get("revision", 0)) + 1,
            }
            marker = extension_policy.state_root(profile) / "mcp-recovery.json"
            extension_policy.atomic_json(
                marker,
                {
                    "catalog_id": entry["id"],
                    "server_name": server_name,
                    "config_digest": self._mcp_config_digest(server_config),
                    "record": state["mcp"][entry["id"]],
                },
            )
            self.repository.atomic_yaml(config_path, config)
            state["revision"] = int(state.get("revision", 1)) + 1
            self._save(profile, state)
            marker.unlink(missing_ok=True)
        return {
            "agent_id": agent_id,
            "catalog_id": entry["id"],
            "configured": True,
            "revision": state["mcp"][entry["id"]]["revision"],
            "server_name": server_name,
            "enabled": True,
            "auth_configured": auth_configured,
        }

    def _catalog_server_config(self, entry: dict[str, Any], fields: dict[str, str]):
        mcp = entry.get("mcp", {})
        auth = mcp.get("auth", {})
        expected = set()
        url = mcp.get("url", "")
        if mcp.get("endpoint_mode") == "user_configured":
            expected.add("endpoint_url")
            url = fields.get("endpoint_url", "")
        key = ""
        if auth.get("kind") == "api_key":
            expected.add(str(auth.get("field", "")))
            key = fields.get(str(auth["field"]), "")
        if set(fields) != expected:
            raise ServiceError(
                "Catalog fields do not match this recipe", status=422, code="invalid_catalog_fields"
            )
        if auth.get("kind") == "api_key" and (
            not key or len(key) > 8192 or any(ord(c) < 32 for c in key)
        ):
            raise ServiceError("API key is invalid", status=422, code="invalid_api_key")
        self._validate_catalog_endpoint(str(url), str(mcp.get("url_policy", "")))
        result: dict[str, Any] = {"url": url}
        if auth.get("kind") == "api_key":
            if auth.get("delivery") != "header":
                raise ServiceError(
                    "Catalog auth delivery is unsupported", status=422, code="unsupported_auth"
                )
            header = str(auth.get("header", ""))
            if not re.fullmatch(r"[A-Za-z0-9-]{1,80}", header):
                raise ServiceError(
                    "Catalog auth header is invalid", status=422, code="invalid_catalog"
                )
            result["headers"] = {header: str(auth.get("prefix", "")) + key}
        elif auth.get("kind") != "none":
            raise ServiceError(
                "Catalog authentication is unsupported", status=422, code="unsupported_auth"
            )
        return result, str(mcp.get("endpoint_mode", "static")), auth.get("kind") == "api_key"

    @staticmethod
    def _validate_catalog_endpoint(url: str, policy: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ServiceError(
                "MCP endpoint must be public HTTPS", status=422, code="unsafe_endpoint"
            )
        if parsed.port not in (None, 443):
            raise ServiceError(
                "MCP endpoint port is not allowed", status=422, code="unsafe_endpoint"
            )
        host = parsed.hostname.rstrip(".").lower()
        if policy == "https_only_public_composio_host" and not (
            host == "composio.dev" or host.endswith(".composio.dev")
        ):
            raise ServiceError(
                "Composio endpoint host is not allowed", status=422, code="unsafe_endpoint"
            )
        if policy not in {"", "https_only_public_composio_host"}:
            raise ServiceError(
                "Catalog URL policy is unsupported", status=422, code="unsafe_endpoint"
            )
        try:
            addresses = (
                {ipaddress.ip_address(host)}
                if _is_ip(host)
                else {
                    ipaddress.ip_address(item[4][0])
                    for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
                }
            )
        except (OSError, ValueError) as error:
            raise ServiceError(
                "MCP endpoint could not be resolved", status=422, code="unsafe_endpoint"
            ) from error
        if not addresses or any(not address.is_global for address in addresses):
            raise ServiceError(
                "MCP endpoint must resolve to public addresses", status=422, code="unsafe_endpoint"
            )

    @staticmethod
    def _is_catalog_owned(config: Any, catalog_id: str) -> bool:
        return isinstance(config, dict) and config.get("xnobrain_catalog_id") == catalog_id

    def _recover_mcp_mirror(self, profile: Path) -> None:
        marker = profile / ".xnobrain" / "extensions" / "mcp-recovery.json"
        if not marker.exists():
            return
        path = profile / "config.yaml"
        try:
            config = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except (OSError, yaml.YAMLError) as error:
            raise ServiceError(
                "MCP recovery is unavailable", status=503, code="mcp_recovery_failed"
            ) from error
        servers = (
            config.get(extension_policy.MANAGED_MCP_KEY, {}) if isinstance(config, dict) else {}
        )
        try:
            intent = json.loads(marker.read_text())
            catalog_id = intent["catalog_id"]
            server_name = intent["server_name"]
            record = intent["record"]
            expected = intent["config_digest"]
            if (
                not isinstance(servers, dict)
                or not isinstance(catalog_id, str)
                or not _PLUGIN_ID.fullmatch(catalog_id)
                or server_name != f"xnobrain-{catalog_id}"
                or not isinstance(record, dict)
                or record.get("server_name") != server_name
            ):
                raise ValueError("invalid recovery intent")
            actual = (
                self._mcp_config_digest(servers[server_name]) if server_name in servers else None
            )
            if actual == expected:
                state = self._state(profile)
                state["mcp"][catalog_id] = record
                state["revision"] = int(state.get("revision", 1)) + 1
                self._save(profile, state)
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise ServiceError(
                "MCP recovery is unavailable", status=503, code="mcp_recovery_failed"
            ) from error
        marker.unlink(missing_ok=True)

    @staticmethod
    def _mcp_config_digest(config: Any) -> str:
        return hashlib.sha256(
            json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def disconnect_catalog(self, agent_id: str, catalog_id: str, trusted: Any) -> dict[str, Any]:
        profile, _owner = self._profile(agent_id, trusted)
        with self._lifecycle(agent_id):
            state = self._state(profile)
            record = state["mcp"].get(catalog_id)
            if not record:
                raise ServiceError(
                    "Catalog integration is not configured", status=404, code="not_found"
                )
            self._recover_mcp_mirror(profile)
            config_path = profile / "config.yaml"
            if config_path.is_symlink():
                raise ServiceError("Unsafe profile config", status=409, code="invalid_profile")
            config = (
                yaml.safe_load(config_path.read_text(encoding="utf-8"))
                if config_path.is_file()
                else {}
            )
            if not isinstance(config, dict):
                raise ServiceError(
                    "Profile config is invalid", status=409, code="invalid_profile_config"
                )
            servers = config.get(extension_policy.MANAGED_MCP_KEY) or {}
            server_name = record["server_name"]
            if server_name not in servers:
                raise ServiceError(
                    "Catalog integration is not configured", status=404, code="not_found"
                )
            self._snapshot_config(agent_id, profile)
            servers.pop(server_name, None)
            config[extension_policy.MANAGED_MCP_KEY] = servers
            marker = extension_policy.state_root(profile) / "mcp-recovery.json"
            record["enabled"] = False
            record["revision"] = int(record.get("revision", 1)) + 1
            extension_policy.atomic_json(
                marker,
                {
                    "catalog_id": catalog_id,
                    "server_name": server_name,
                    "config_digest": None,
                    "record": record,
                },
            )
            self.repository.atomic_yaml(config_path, config)
            state["revision"] = int(state.get("revision", 1)) + 1
            self._save(profile, state)
            marker.unlink(missing_ok=True)
            extension_engine.mcp_disconnect(profile, server_name)
        return {"agent_id": agent_id, "catalog_id": catalog_id, "enabled": False}

    def set_plugin_visibility(
        self, agent_id: str, plugin_id: str, body: dict[str, Any], trusted: Any
    ) -> dict[str, Any]:
        profile, _owner = self._profile(agent_id, trusted)
        if not _PLUGIN_ID.fullmatch(plugin_id):
            raise ServiceError("Plugin id is invalid", status=422, code="invalid_plugin_id")
        with self._lifecycle(agent_id):
            state = self._state(profile)
            record = state["plugins"].get(plugin_id)
            if record is None:
                if not (profile / "plugins" / plugin_id).is_dir():
                    raise ServiceError("Plugin not found", status=404, code="plugin_not_found")
                record = {"revision": 1}
                state["plugins"][plugin_id] = record
            self._require_revision(record, body["expected_revision"])
            record["hidden"] = bool(body["hidden"])
            record["revision"] = int(record.get("revision", 1)) + 1
            state["revision"] = int(state.get("revision", 1)) + 1
            self._save(profile, state)
        return {
            "agent_id": agent_id,
            "plugin_id": plugin_id,
            "hidden": record["hidden"],
            "revision": record["revision"],
        }

    def approve_plugin(
        self, agent_id: str, plugin_id: str, body: dict[str, Any], trusted: Any
    ) -> dict[str, Any]:
        profile, _owner = self._profile(agent_id, trusted)
        if not _PLUGIN_ID.fullmatch(plugin_id):
            raise ServiceError("Plugin id is invalid", status=422, code="invalid_plugin_id")
        with self._lifecycle(agent_id):
            if (
                plugin_id in extension_policy.REQUIRED_PLUGINS
                and not extension_policy.trusted_required(profile, plugin_id)
            ):
                raise ServiceError(
                    "Required system plugin does not match its packaged source",
                    status=409,
                    code="required_plugin_invalid",
                )
            artifact = profile / "plugins" / plugin_id
            digest = extension_policy.tree_digest(artifact)
            if digest != body["artifact_digest"]:
                raise ServiceError(
                    "Plugin changed since scan", status=409, code="plugin_scan_stale"
                )
            state = self._state(profile)
            record = state["plugins"].get(plugin_id)
            if not record:
                raise ServiceError(
                    "Plugin scan is required", status=409, code="plugin_scan_required"
                )
            self._require_revision(record, body["expected_revision"])
            scan = record.get("scan") or {}
            if scan.get("status") != "COMPLETED" or scan.get("verdict") == "dangerous":
                raise ServiceError(
                    "Plugin has no approvable complete scan",
                    status=409,
                    code="plugin_scan_required",
                )
            record["approval"] = {"approved": bool(body["approved"]), "artifact_digest": digest}
            record["revision"] = int(record.get("revision", 1)) + 1
            state["revision"] = int(state.get("revision", 1)) + 1
            self._save(profile, state)
        return {
            "agent_id": agent_id,
            "plugin_id": plugin_id,
            "approved": bool(body["approved"]),
            "artifact_digest": digest,
            "revision": record["revision"],
        }

    def set_plugin_enabled(
        self, agent_id: str, plugin_id: str, enabled: bool, trusted: Any
    ) -> dict[str, Any]:
        profile, _owner = self._profile(agent_id, trusted)
        if not _PLUGIN_ID.fullmatch(plugin_id):
            raise ServiceError("Plugin id is invalid", status=422, code="invalid_plugin_id")
        with self._lifecycle(agent_id):
            artifact = profile / "plugins" / plugin_id
            if not artifact.is_dir() or artifact.is_symlink():
                raise ServiceError("Plugin not found", status=404, code="plugin_not_found")
            if enabled:
                extension_policy.validate_profile_extensions(profile)
                state = self._state(profile)
                record = state["plugins"].get(plugin_id, {})
                artifact = profile / "plugins" / plugin_id
                digest = extension_policy.tree_digest(artifact)
                scan = record.get("scan") or {}
                approval = record.get("approval") or {}
                required = extension_policy.trusted_required(profile, plugin_id)
                if not required and (
                    scan.get("status") != "COMPLETED"
                    or scan.get("verdict") == "dangerous"
                    or approval.get("approved") is not True
                    or approval.get("artifact_digest") != digest
                ):
                    raise ServiceError(
                        "Plugin requires a complete scan and approval",
                        status=409,
                        code="plugin_approval_required",
                    )
            elif extension_policy.trusted_required(profile, plugin_id):
                raise ServiceError(
                    "Required system plugin cannot be disabled", status=409, code="required_plugin"
                )
            elif plugin_id in extension_policy.REQUIRED_PLUGINS:
                raise ServiceError(
                    "Required system plugin does not match its packaged source",
                    status=409,
                    code="required_plugin_invalid",
                )
            self._snapshot_config(agent_id, profile)
            try:
                result = extension_engine.plugin_set_enabled(profile, plugin_id, enabled)
            except (ImportError, AttributeError, RuntimeError, ValueError) as error:
                raise ServiceError(
                    "Plugin lifecycle is unavailable",
                    status=503,
                    code="extension_engine_unavailable",
                ) from error
        return {"agent_id": agent_id, "plugin_id": plugin_id, "enabled": enabled, "result": result}

    def list_toolsets(self, agent_id: str, trusted: Any) -> dict[str, Any]:
        profile, _owner = self._profile(agent_id, trusted)
        try:
            toolsets = extension_engine.toolset_inventory(profile)
        except (ImportError, AttributeError, RuntimeError) as error:
            raise ServiceError(
                "Tool registry is unavailable", status=503, code="extension_engine_unavailable"
            ) from error
        revisions = self._state(profile).setdefault("toolsets", {})
        for item in toolsets:
            item["revision"] = int(revisions.get(item.get("id"), 1))
        return {"agent_id": agent_id, "toolsets": toolsets}

    def set_toolset(
        self, agent_id: str, toolset: str, body: dict[str, Any], trusted: Any
    ) -> dict[str, Any]:
        profile, _owner = self._profile(agent_id, trusted)
        with self._lifecycle(agent_id):
            inventory = extension_engine.toolset_inventory(profile)
            item = next((entry for entry in inventory if entry.get("id") == toolset), None)
            if not item or not item.get("mutable"):
                raise ServiceError(
                    "Toolset cannot be changed", status=422, code="toolset_not_mutable"
                )
            if body["enabled"] and not item.get("available"):
                raise ServiceError("Toolset is unavailable", status=409, code="toolset_unavailable")
            state = self._state(profile)
            revisions = state.setdefault("toolsets", {})
            revision = int(revisions.get(toolset, 1))
            if revision != body["expected_revision"]:
                raise ServiceError(
                    "Toolset state changed", status=409, code="extension_revision_conflict"
                )
            self._snapshot_config(agent_id, profile)
            extension_engine.toolset_set_enabled(profile, toolset, body["enabled"])
            revisions[toolset] = revision + 1
            state["revision"] = int(state.get("revision", 1)) + 1
            self._save(profile, state)
        return {
            "agent_id": agent_id,
            "toolset": toolset,
            "enabled": body["enabled"],
            "revision": revisions[toolset],
        }

    def list_hooks(self, agent_id: str, trusted: Any) -> dict[str, Any]:
        profile, _owner = self._profile(agent_id, trusted)
        state = self._state(profile)
        try:
            engine_rows = extension_engine.hook_list(profile)
            event_contract = extension_engine.hook_events()
            observability = extension_engine.hook_observability(profile)
        except (ImportError, AttributeError, RuntimeError) as error:
            raise ServiceError(
                "Hook runtime is unavailable", status=503, code="extension_engine_unavailable"
            ) from error
        active = {
            (item.get("event"), item.get("command_label")): item
            for item in engine_rows
            if isinstance(item, dict)
        }
        hooks = []
        for hook_id, record in state["hooks"].items():
            label = "hook-" + hashlib.sha256(record.get("command", "").encode()).hexdigest()[:12]
            native = active.get((record.get("event"), label), {})
            approval = record.get("approval") or {}
            hooks.append(
                {
                    "id": hook_id,
                    "event": record.get("event"),
                    "matcher": record.get("matcher"),
                    "timeout": record.get("timeout"),
                    "enabled": bool(record.get("enabled")) and bool(native),
                    "command_digest": record.get("command_digest"),
                    "approval": {
                        "approved": approval.get("approved") is True
                        and approval.get("digest") == record.get("command_digest")
                    },
                    "revision": int(record.get("revision", 1)),
                }
            )
        return {
            "agent_id": agent_id,
            "hooks": hooks,
            "available_events": event_contract.get("valid_events", []),
            "unsupported_events": event_contract.get("unsupported", []),
            "plugin_hooks": observability.get("plugin_hooks", []),
            "gateway_hooks": observability.get("gateway_hooks", []),
        }

    def create_hook(self, agent_id: str, body: dict[str, Any], trusted: Any) -> dict[str, Any]:
        profile, _owner = self._profile(agent_id, trusted)
        try:
            event_contract = extension_engine.hook_events()
        except (ImportError, AttributeError, RuntimeError) as error:
            raise ServiceError(
                "Hook runtime is unavailable", status=503, code="extension_engine_unavailable"
            ) from error
        event, command = body["event"], body["command"].strip()
        valid = set(event_contract.get("valid_events", []))
        unsupported = set(event_contract.get("unsupported", []))
        if event not in valid or event in unsupported:
            raise ServiceError("Hook event is unsupported", status=422, code="invalid_hook_event")
        matcher = str(body.get("matcher") or "").strip() or None
        if matcher:
            try:
                re.compile(matcher)
            except re.error as error:
                raise ServiceError(
                    "Hook matcher is invalid", status=422, code="invalid_hook_matcher"
                ) from error
        if "\x00" in command or len(command.encode()) > 4096:
            raise ServiceError("Hook command is invalid", status=422, code="invalid_hook_command")
        timeout = int(body.get("timeout", 30))
        if not 1 <= timeout <= 300:
            raise ServiceError("Hook timeout is invalid", status=422, code="invalid_hook_timeout")
        record_digest = self._hook_digest(event, command, matcher, timeout)
        with self._lifecycle(agent_id):
            state = self._state(profile)
            hook_id = hashlib.sha256(os.urandom(32)).hexdigest()[:24]
            state["hooks"][hook_id] = {
                "event": event,
                "command": command,
                "matcher": matcher,
                "timeout": timeout,
                "command_digest": record_digest,
                "enabled": False,
                "approval": {"approved": False, "digest": ""},
                "revision": 1,
            }
            self._save(profile, state)
        return {
            "agent_id": agent_id,
            "id": hook_id,
            "event": event,
            "command_digest": record_digest,
            "enabled": False,
            "approval_required": True,
            "revision": 1,
        }

    @staticmethod
    def _hook_digest(event: str, command: str, matcher: str | None, timeout: int) -> str:
        payload = json.dumps(
            {"event": event, "command": command, "matcher": matcher, "timeout": timeout},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def approve_hook(
        self, agent_id: str, hook_id: str, body: dict[str, Any], trusted: Any
    ) -> dict[str, Any]:
        profile, _owner = self._profile(agent_id, trusted)
        with self._lifecycle(agent_id):
            state = self._state(profile)
            record = state["hooks"].get(hook_id)
            if not record:
                raise ServiceError("Hook not found", status=404, code="not_found")
            self._require_revision(record, body["expected_revision"])
            if body["command_digest"] != record["command_digest"]:
                raise ServiceError(
                    "Hook changed since review", status=409, code="hook_approval_stale"
                )
            if body["approved"]:
                extension_engine.hook_record_approval(profile, record["event"], record["command"])
                record["enabled"] = False
                record["approval"] = {"approved": True, "digest": record["command_digest"]}
            else:
                self._snapshot_config(agent_id, profile)
                extension_engine.hook_revoke(profile, record["event"], record["command"])
                record["enabled"] = False
                record["approval"] = {"approved": False, "digest": ""}
            record["revision"] = int(record.get("revision", 1)) + 1
            state["revision"] = int(state.get("revision", 1)) + 1
            self._save(profile, state)
        return {
            "agent_id": agent_id,
            "id": hook_id,
            "approved": body["approved"],
            "revision": record["revision"],
        }

    def set_hook_enabled(
        self, agent_id: str, hook_id: str, enabled: bool, trusted: Any
    ) -> dict[str, Any]:
        profile, _owner = self._profile(agent_id, trusted)
        with self._lifecycle(agent_id):
            state = self._state(profile)
            record = state["hooks"].get(hook_id)
            if not record:
                raise ServiceError("Hook not found", status=404, code="not_found")
            if enabled:
                approval = record.get("approval") or {}
                if approval.get("approved") is not True or approval.get("digest") != record.get(
                    "command_digest"
                ):
                    raise ServiceError(
                        "Hook requires approval", status=409, code="hook_approval_required"
                    )
            if enabled:
                extension_engine.hook_record_approval(profile, record["event"], record["command"])
                self._snapshot_config(agent_id, profile)
                extension_engine.hook_register(
                    profile,
                    record["event"],
                    record["command"],
                    record.get("matcher"),
                    record["timeout"],
                )
            else:
                self._snapshot_config(agent_id, profile)
                extension_engine.hook_revoke(profile, record["event"], record["command"])
            record["enabled"] = enabled
            record["revision"] = int(record.get("revision", 1)) + 1
            state["revision"] = int(state.get("revision", 1)) + 1
            self._save(profile, state)
        return {
            "agent_id": agent_id,
            "id": hook_id,
            "enabled": enabled,
            "revision": record["revision"],
        }

    def delete_hook(self, agent_id: str, hook_id: str, trusted: Any) -> dict[str, Any]:
        profile, _owner = self._profile(agent_id, trusted)
        with self._lifecycle(agent_id):
            state = self._state(profile)
            record = state["hooks"].get(hook_id)
            if not record:
                raise ServiceError("Hook not found", status=404, code="not_found")
            self._snapshot_config(agent_id, profile)
            extension_engine.hook_revoke(profile, record["event"], record["command"])
            state["hooks"].pop(hook_id, None)
            state["revision"] = int(state.get("revision", 1)) + 1
            self._save(profile, state)
        return {"agent_id": agent_id, "id": hook_id, "deleted": True}

    def create_operation(self, agent_id: str, body: dict[str, Any], trusted: Any) -> dict[str, Any]:
        profile, owner = self._profile(agent_id, trusted)
        kind = str(body.get("kind", ""))
        plugin_id = str(body.get("plugin_id", ""))
        if not _PLUGIN_ID.fullmatch(plugin_id):
            raise ServiceError("Plugin id is invalid", status=422, code="invalid_plugin_id")
        with self._lifecycle(agent_id):
            profile_generation = self._profile_generation(profile, initialize=True)
            if kind in {"install", "update"}:
                if kind == "update" and not body.get("source"):
                    prior = self._state(profile)["plugins"].get(plugin_id, {})
                    if not prior.get("source"):
                        raise ServiceError(
                            "Plugin source is unavailable",
                            status=409,
                            code="plugin_source_unavailable",
                        )
                    source, ref = self._validate_git_source(
                        prior["source"], body.get("ref") or prior.get("ref") or ""
                    )
                else:
                    source, ref = self._validate_git_source(body.get("source"), body.get("ref"))
                target = profile / "plugins" / plugin_id
                if kind == "install" and target.exists():
                    raise ServiceError(
                        "Plugin is already installed; use update", status=409, code="plugin_exists"
                    )
                if kind == "update" and not target.is_dir():
                    raise ServiceError(
                        "Plugin is not installed", status=404, code="plugin_not_found"
                    )
            else:
                source, ref = "", ""
            inputs = {
                "plugin_id": plugin_id,
                "source": source,
                "ref": ref,
                "profile_generation": profile_generation,
            }
            result, _created = self.store.create(
                agent_id=agent_id,
                owner=owner,
                kind=kind,
                inputs=inputs,
                idempotency_key=str(body.get("idempotency_key", "")),
            )
        return result

    @staticmethod
    def _validate_git_source(source: Any, ref: Any) -> tuple[str, str]:
        if not isinstance(source, str) or len(source) > 2048:
            raise ServiceError("Git source is invalid", status=422, code="invalid_git_source")
        if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", source):
            source = f"https://github.com/{source}.git"
        parsed = urlsplit(source)
        if parsed.scheme != "https" or parsed.username or parsed.password or not parsed.hostname:
            raise ServiceError(
                "Git source must use public HTTPS", status=422, code="invalid_git_source"
            )
        host = parsed.hostname.lower().rstrip(".")
        if parsed.port not in (None, 443) or parsed.query or parsed.fragment:
            raise ServiceError("Git host is not allowed", status=422, code="invalid_git_source")
        if not parsed.path.endswith(".git") and parsed.path.count("/") != 2:
            raise ServiceError(
                "Git repository path is invalid", status=422, code="invalid_git_source"
            )
        if ref is not None and (not isinstance(ref, str) or not _GIT_REF.fullmatch(ref)):
            raise ServiceError("Git branch or tag is invalid", status=422, code="invalid_git_ref")
        return source.rstrip("/"), str(ref or "")

    def list_operations(self, agent_id: str, trusted: Any) -> list[dict[str, Any]]:
        _profile, owner = self._profile(agent_id, trusted)
        return self.store.list(agent_id, owner)

    def get_operation(self, agent_id: str, operation_id: str, trusted: Any) -> dict[str, Any]:
        _profile, owner = self._profile(agent_id, trusted)
        return self.store.get(agent_id, owner, operation_id)

    def cancel_operation(self, agent_id: str, operation_id: str, trusted: Any) -> dict[str, Any]:
        _profile, owner = self._profile(agent_id, trusted)
        return self.store.cancel(agent_id, owner, operation_id)

    @staticmethod
    def _require_revision(record: dict[str, Any], expected: int) -> None:
        if int(record.get("revision", 1)) != expected:
            raise ServiceError(
                "Extension state changed", status=409, code="extension_revision_conflict"
            )


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


class ExtensionOperationWorker:
    """One durable Runtime worker for clone, scan, probe and removal jobs."""

    def __init__(self, service: ExtensionsService):
        self.service = service
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._worker_fd: int | None = None

    async def start(self) -> None:
        if self._task is None or self._task.done():
            if self._worker_fd is not None:
                self.service.store.release_worker(self._worker_fd)
                self._worker_fd = None
            descriptor = self.service.store.acquire_worker()
            if descriptor is None:
                return
            self._worker_fd = descriptor
            try:
                self.service._recover_all_publications()
                self.service.store.reconcile()
            except BaseException:
                self.service.store.release_worker(descriptor)
                self._worker_fd = None
                raise
            self._stopping = False
            self._task = asyncio.create_task(self._run(), name="xnobrain-extension-worker")

    async def shutdown(self) -> None:
        self._stopping = True
        if self._task is not None:
            await self._task
            self._task = None
        if self._worker_fd is not None:
            self.service.store.release_worker(self._worker_fd)
            self._worker_fd = None

    async def _run(self) -> None:
        while not self._stopping:
            operation = self.service.store.claim_next()
            if operation is None:
                await asyncio.sleep(0.5)
                continue
            try:
                await asyncio.to_thread(self._process, operation)
            except Exception as error:
                code = getattr(error, "code", "extension_operation_failed")
                if not isinstance(code, str) or not re.fullmatch(r"[a-z0-9_]{1,64}", code):
                    code = "extension_operation_failed"
                if self.service.store.cancelled(operation):
                    status = "CANCELLED"
                elif code in {"plugin_scan_incomplete", "plugin_unsafe", "plugin_manifest_invalid"}:
                    status = "BLOCKED"
                else:
                    status = "FAILED"
                self.service.store.update(
                    operation,
                    status=status,
                    error={
                        "code": code,
                        "message": "Operation cancelled"
                        if status == "CANCELLED"
                        else "Operation failed",
                    },
                )

    def _process(self, operation: dict[str, Any]) -> None:
        inputs = operation.get("inputs") or {}
        agent_id = operation["agent_id"]
        owner = str(operation.get("owner", ""))
        tenant_id, separator, subject = owner.partition("\0")
        if not separator or not tenant_id or not subject:
            self.service.store.update(
                operation,
                status="FAILED",
                error={
                    "code": "operation_identity_invalid",
                    "message": "Operation identity is invalid",
                },
            )
            return
        from ..trusted_context import TrustedRequestContext

        trusted = TrustedRequestContext(subject=subject, tenant_id=tenant_id)
        profile, _ = self.service._profile(agent_id, trusted, recover_publication=False)
        self.service._require_profile_generation(profile, inputs.get("profile_generation"))
        plugin_id = inputs["plugin_id"]
        if operation["kind"] == "probe":
            state = self.service._state(profile)
            record = state["mcp"].get(plugin_id)
            if not record:
                raise ServiceError(
                    "Catalog integration is not configured", status=404, code="not_found"
                )
            result = extension_engine.mcp_probe(profile, record["server_name"])
            if self.service.store.cancelled(operation):
                self.service.store.update(operation, status="CANCELLED")
                return
            self.service.store.update(operation, status="COMPLETED", result=result)
            return
        if operation["kind"] == "remove":
            if plugin_id in extension_policy.REQUIRED_PLUGINS:
                self.service.store.update(
                    operation,
                    status="BLOCKED",
                    error={
                        "code": "required_plugin",
                        "message": "Required system plugin cannot be removed",
                    },
                )
                return
            with self.service._lifecycle(agent_id):
                self.service._require_profile_generation(profile, inputs.get("profile_generation"))
                if self.service.store.cancelled(operation):
                    self.service.store.update(operation, status="CANCELLED")
                    return
                self.service._snapshot_config(agent_id, profile)
                extension_engine.plugin_remove(profile, plugin_id)
                state = self.service._state(profile)
                state["plugins"].pop(plugin_id, None)
                state["revision"] = int(state.get("revision", 1)) + 1
                self.service._save(profile, state)
            self.service.store.update(
                operation, status="COMPLETED", result={"plugin_id": plugin_id, "removed": True}
            )
            return
        if operation["kind"] == "scan":
            artifact = profile / "plugins" / plugin_id
            with self.service._lifecycle(agent_id):
                self.service._require_profile_generation(profile, inputs.get("profile_generation"))
                if not artifact.is_dir() or artifact.is_symlink():
                    raise ServiceError("Plugin not found", status=404, code="plugin_not_found")
                self.service._snapshot_config(agent_id, profile)
                extension_engine.plugin_set_enabled(profile, plugin_id, False)
                expected_digest = extension_policy.tree_digest(artifact)
            self.service.store.update(
                operation, status="PROCESSING", progress={"phase": "scanning"}
            )
            if self.service.store.cancelled(operation):
                self.service.store.update(operation, status="CANCELLED")
                return
            scan_result = extension_engine.scan_plugin(artifact)
            if self.service.store.cancelled(operation):
                self.service.store.update(operation, status="CANCELLED")
                return
            with self.service._lifecycle(agent_id):
                self.service._require_profile_generation(profile, inputs.get("profile_generation"))
                if extension_policy.tree_digest(artifact) != expected_digest:
                    raise ServiceError(
                        "Plugin changed during scan", status=409, code="plugin_scan_stale"
                    )
                state = self.service._state(profile)
                prior = state["plugins"].get(plugin_id, {})
                prior.update(
                    source=prior.get("source", ""),
                    ref=prior.get("ref", ""),
                    commit=prior.get("commit", ""),
                    artifact_digest=scan_result["artifact_digest"],
                    scan=scan_result["scan"],
                    approval={"approved": False, "artifact_digest": ""},
                    revision=int(prior.get("revision", 0)) + 1,
                )
                state["plugins"][plugin_id] = prior
                state["revision"] = int(state.get("revision", 1)) + 1
                self.service._save(profile, state)
            status = "COMPLETED" if scan_result["scan"]["status"] == "COMPLETED" else "BLOCKED"
            self.service.store.update(
                operation,
                status=status,
                result={
                    "plugin_id": plugin_id,
                    "artifact_digest": scan_result["artifact_digest"],
                    "scan": self.service._public_scan(scan_result["scan"]),
                },
            )
            return
        self._install_or_update(operation, profile, plugin_id, inputs, trusted)

    def _install_or_update(
        self,
        operation: dict[str, Any],
        profile: Path,
        plugin_id: str,
        inputs: dict[str, Any],
        trusted: Any,
    ) -> None:
        if plugin_id in extension_policy.REQUIRED_PLUGINS:
            self.service.store.update(
                operation,
                status="BLOCKED",
                error={"code": "required_plugin", "message": "Required system plugin is protected"},
            )
            return
        stage_root = self.service.repository.data_dir / "extension-staging"
        if stage_root.is_symlink():
            raise ServiceError("Operation staging path is unsafe", status=409, code="invalid_path")
        stage_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        stage = stage_root / operation["operation_id"]
        if stage.exists() or stage.is_symlink():
            raise ServiceError("Operation staging path is unsafe", status=409, code="invalid_path")
        target_root = extension_policy.safe_child(profile, "plugins")
        target_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = target_root / plugin_id
        replacement = target_root / f".{plugin_id}.{operation['operation_id']}.stage"
        rollback = target_root / f".{plugin_id}.{operation['operation_id']}.rollback"
        if target.is_symlink():
            raise ServiceError("Plugin path is unsafe", status=409, code="invalid_path")
        if operation["kind"] == "install" and target.exists():
            raise ServiceError(
                "Plugin is already installed; use update", status=409, code="plugin_exists"
            )
        if operation["kind"] == "update" and not target.is_dir():
            raise ServiceError("Plugin is not installed", status=404, code="plugin_not_found")
        try:
            self.service.store.update(operation, status="PROCESSING", progress={"phase": "cloning"})
            commit = extension_engine.clone_plugin(
                inputs["source"],
                inputs["ref"],
                stage,
                cancelled=lambda: self.service.store.cancelled(operation),
            )
            self.service.store.update(
                operation, status="PROCESSING", progress={"phase": "scanning"}
            )
            scan_result = extension_engine.scan_plugin(stage)
            scan = scan_result["scan"]
            if self.service.store.cancelled(operation):
                self.service.store.update(operation, status="CANCELLED")
                return
            manifest = yaml.safe_load((stage / "plugin.yaml").read_text(encoding="utf-8"))
            declared = str(manifest.get("key") or manifest.get("name") or "")
            if declared != plugin_id:
                raise ServiceError(
                    "Plugin id does not match its manifest", status=422, code="plugin_id_mismatch"
                )
            if scan.get("status") != "COMPLETED" or scan.get("verdict") == "dangerous":
                self.service.store.update(
                    operation,
                    status="BLOCKED",
                    result={
                        "plugin_id": plugin_id,
                        "commit": commit,
                        "scan": self.service._public_scan(scan),
                    },
                    error={
                        "code": "plugin_scan_incomplete",
                        "message": "Plugin did not pass a complete scan",
                    },
                )
                return
            shutil.copytree(stage, replacement, symlinks=False)
            with self.service._lifecycle(operation["agent_id"]):
                self.service._require_profile_generation(profile, inputs.get("profile_generation"))
                if self.service.store.cancelled(operation):
                    self.service.store.update(operation, status="CANCELLED")
                    return
                if operation["kind"] == "install" and target.exists():
                    raise ServiceError(
                        "Plugin is already installed; use update", status=409, code="plugin_exists"
                    )
                if operation["kind"] == "update" and not target.is_dir():
                    raise ServiceError(
                        "Plugin is not installed", status=404, code="plugin_not_found"
                    )
                self.service._snapshot_config(operation["agent_id"], profile)
                had_old = target.exists()
                old_digest = extension_policy.tree_digest(target) if had_old else ""
                if had_old:
                    self._backup_plugin(profile, plugin_id, target)
                extension_recovery.write_publication(
                    profile,
                    {
                        "operation_id": operation["operation_id"],
                        "plugin_id": plugin_id,
                        "phase": "prepared",
                        "had_old": had_old,
                        "old_artifact_digest": old_digest,
                        "new_artifact_digest": scan_result["artifact_digest"],
                    },
                )
                try:
                    extension_engine.plugin_set_enabled(profile, plugin_id, False)
                    if had_old:
                        os.replace(target, rollback)
                        extension_recovery.sync_directory(target_root)
                        extension_recovery.write_publication(
                            profile,
                            {
                                "operation_id": operation["operation_id"],
                                "plugin_id": plugin_id,
                                "phase": "old_moved",
                                "had_old": had_old,
                                "old_artifact_digest": old_digest,
                                "new_artifact_digest": scan_result["artifact_digest"],
                            },
                        )
                    os.replace(replacement, target)
                    extension_recovery.sync_directory(target_root)
                    extension_recovery.write_publication(
                        profile,
                        {
                            "operation_id": operation["operation_id"],
                            "plugin_id": plugin_id,
                            "phase": "published",
                            "had_old": had_old,
                            "old_artifact_digest": old_digest,
                            "new_artifact_digest": scan_result["artifact_digest"],
                        },
                    )
                    if self.service.store.cancelled(operation):
                        raise ServiceError("Operation cancelled", code="operation_cancelled")
                    state = self.service._state(profile)
                    previous = state["plugins"].get(plugin_id, {})
                    state["plugins"][plugin_id] = {
                        "source": inputs["source"],
                        "ref": inputs["ref"],
                        "commit": commit,
                        "artifact_digest": scan_result["artifact_digest"],
                        "scan": scan,
                        "approval": {"approved": False, "artifact_digest": ""},
                        "hidden": bool(previous.get("hidden", False)),
                        "revision": int(previous.get("revision", 0)) + 1,
                    }
                    state["revision"] = int(state.get("revision", 1)) + 1
                    self.service._save(profile, state)
                    extension_recovery.write_publication(
                        profile,
                        {
                            "operation_id": operation["operation_id"],
                            "plugin_id": plugin_id,
                            "phase": "state_saved",
                            "had_old": had_old,
                            "old_artifact_digest": old_digest,
                            "new_artifact_digest": scan_result["artifact_digest"],
                        },
                    )
                    if rollback.exists():
                        shutil.rmtree(rollback)
                        extension_recovery.sync_directory(target_root)
                    extension_recovery.clear_publication(profile)
                except BaseException:
                    try:
                        extension_recovery.recover_publication(
                            profile,
                            staging_root=self.service.repository.data_dir / "extension-staging",
                        )
                    except Exception as recovery_error:
                        _LOGGER.error(
                            "Plugin publication rollback deferred",
                            extra={
                                "agent_id": operation["agent_id"],
                                "error_code": getattr(
                                    recovery_error, "code", "extension_recovery_required"
                                ),
                            },
                        )
                    raise
            self.service.store.update(
                operation,
                status="COMPLETED",
                result={
                    "plugin_id": plugin_id,
                    "commit": commit,
                    "artifact_digest": scan_result["artifact_digest"],
                    "scan": self.service._public_scan(scan),
                },
            )
        finally:
            pending_intent = extension_recovery.publication_path(profile).exists()
            if stage.exists() and not pending_intent:
                shutil.rmtree(stage, ignore_errors=True)
            if replacement.exists() and not pending_intent:
                shutil.rmtree(replacement, ignore_errors=True)

    @staticmethod
    def _backup_plugin(profile: Path, plugin_id: str, target: Path) -> None:
        import zipfile

        root = extension_policy.safe_child(profile, ".xnobrain", "extensions", "backups", plugin_id)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        digest = extension_policy.tree_digest(target)
        backup = root / f"{digest}.zip"
        if backup.exists():
            return
        fd, temporary = tempfile.mkstemp(prefix=".plugin-backup-", dir=root)
        os.close(fd)
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(target.rglob("*")):
                    if path.is_symlink():
                        raise ServiceError(
                            "Plugin contains a symbolic link", status=409, code="plugin_unsafe"
                        )
                    if (
                        path.is_file()
                        and ".git" not in path.relative_to(target).parts
                        and "__pycache__" not in path.parts
                    ):
                        archive.write(path, path.relative_to(target).as_posix())
            os.chmod(temporary, 0o600)
            with open(temporary, "rb") as archive_file:
                os.fsync(archive_file.fileno())
            os.replace(temporary, backup)
            extension_recovery.sync_directory(root)
        finally:
            Path(temporary).unlink(missing_ok=True)
