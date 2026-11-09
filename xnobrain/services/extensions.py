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
import stat
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from ..integrations import (
    extension_engine,
    extension_policy,
    extension_recovery,
    plugin_formats,
)
from ..integrations.hermes_support import AgentAPIError
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
_MARKETPLACE_LOCK = threading.RLock()
_MARKETPLACE_MANIFESTS = (
    Path(".claude-plugin") / "marketplace.json",
    Path(".agents") / "plugins" / "marketplace.json",
)


class ExtensionsService:
    def __init__(self, repository, agents):
        self.repository = repository
        self.agents = agents
        self.store = ExtensionOperationStore(repository.data_dir)
        self.worker = ExtensionOperationWorker(self)

    def _personal_identity(self, trusted: Any) -> str:
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
        return f"{trusted.tenant_id}\0{trusted.subject}"

    def _profile(
        self, agent_id: str, trusted: Any, *, recover_publication: bool = True
    ) -> tuple[Path, str]:
        owner_key = self._personal_identity(trusted)
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
            # Plugins enabled before one-shot shadows existed get them on first load.
            if self._plugin_mcp_shadows(profile) is not None:
                try:
                    with self._lifecycle(agent_id):
                        self._sync_plugin_mcp_shadows(profile)
                except ServiceError:
                    pass  # busy or invalid config: the next request retries
        return profile, owner_key

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

    def _marketplaces_path(self) -> Path:
        root = self.repository.data_dir / "extensions"
        path = root / "marketplaces.json"
        if root.is_symlink() or path.is_symlink():
            raise ServiceError(
                "Marketplace storage is unsafe", status=503, code="marketplace_storage_invalid"
            )
        if path.exists() and not path.is_file():
            raise ServiceError(
                "Marketplace storage is unsafe", status=503, code="marketplace_storage_invalid"
            )
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if root.is_symlink() or not root.is_dir():
            raise ServiceError(
                "Marketplace storage is unsafe", status=503, code="marketplace_storage_invalid"
            )
        return path

    def _read_marketplaces(self) -> list[dict[str, Any]]:
        path = self._marketplaces_path()
        if not path.exists():
            return []
        try:
            if not stat.S_ISREG(path.stat().st_mode) or path.stat().st_size > 4 * 1024 * 1024:
                raise ValueError("invalid marketplace store")
            value = json.loads(path.read_text(encoding="utf-8"))
            rows = value["marketplaces"]
            if not isinstance(rows, list):
                raise ValueError("invalid marketplaces")
            return rows
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ServiceError(
                "Marketplace storage is unavailable", status=503, code="marketplace_storage_invalid"
            ) from error

    def _write_marketplaces(self, rows: list[dict[str, Any]]) -> None:
        path = self._marketplaces_path()
        try:
            extension_policy.atomic_json(path, {"marketplaces": rows})
        except (OSError, ValueError) as error:
            raise ServiceError(
                "Marketplace storage is unavailable", status=503, code="marketplace_storage_invalid"
            ) from error

    @staticmethod
    def _marketplace_source(source: Any) -> tuple[str, str]:
        if not isinstance(source, str) or len(source) > 2048:
            raise ServiceError("Git source is invalid", status=422, code="invalid_git_source")
        raw = source.strip()
        ref = ""
        if "#" in raw:
            if raw.count("#") != 1:
                raise ServiceError("Git reference is invalid", status=422, code="invalid_git_ref")
            raw, ref = raw.rsplit("#", 1)
            if not ref or not _GIT_REF.fullmatch(ref):
                raise ServiceError("Git reference is invalid", status=422, code="invalid_git_ref")
        normalized, validated_ref, _ = ExtensionsService._validate_git_source(raw, ref)
        return normalized, validated_ref

    def _fetch_marketplace(self, source: str, ref: str) -> dict[str, Any]:
        root = self.repository.data_dir / "extension-staging"
        if root.is_symlink():
            raise ServiceError(
                "Extension staging is unsafe", status=503, code="extension_staging_invalid"
            )
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if root.is_symlink() or not root.is_dir():
            raise ServiceError(
                "Extension staging is unsafe", status=503, code="extension_staging_invalid"
            )
        stage_parent = Path(tempfile.mkdtemp(prefix="marketplace-", dir=root))
        stage = stage_parent / "repo"
        try:
            # Shortcut: marketplace refresh is synchronous; clone_plugin has its own bounded timeout.
            # Only the manifest is read, so skip the per-plugin artifact limits (the
            # 32 MiB Git limit still applies). shortcut: repos above 32 MiB fail; use a
            # sparse checkout of the manifest if large marketplaces are needed.
            commit = extension_engine.clone_plugin(source, ref or None, stage, artifact=False)
            manifest = None
            for relative in _MARKETPLACE_MANIFESTS:
                candidate = stage / relative
                current = stage
                for part in relative.parts:
                    current = current / part
                    if current.is_symlink():
                        raise ServiceError(
                            "Marketplace manifest is unsafe",
                            status=422,
                            code="marketplace_manifest_invalid",
                        )
                if candidate.is_symlink():
                    raise ServiceError(
                        "Marketplace manifest is unsafe",
                        status=422,
                        code="marketplace_manifest_invalid",
                    )
                if candidate.is_file():
                    info = candidate.stat()
                    if not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024:
                        raise ServiceError(
                            "Marketplace manifest is too large",
                            status=422,
                            code="marketplace_manifest_invalid",
                        )
                    try:
                        manifest = json.loads(candidate.read_text(encoding="utf-8"))
                    except (OSError, ValueError) as error:
                        raise ServiceError(
                            "Marketplace manifest is invalid",
                            status=422,
                            code="marketplace_manifest_invalid",
                        ) from error
                    break
            if manifest is None:
                raise ServiceError(
                    "Marketplace manifest is missing",
                    status=422,
                    code="marketplace_manifest_missing",
                )
            if not isinstance(manifest, dict):
                raise ServiceError(
                    "Marketplace manifest is invalid",
                    status=422,
                    code="marketplace_manifest_invalid",
                )
            entries, skipped = self._parse_marketplace_entries(manifest, source, commit)
            name = manifest.get("name")
            if not isinstance(name, str) or not name.strip() or len(name) > 80:
                name = (
                    urlsplit(source).path.rstrip("/").split("/")[-1].removesuffix(".git")
                    or "Marketplace"
                )
            return {
                "name": name.strip(),
                "source": source,
                "ref": ref or None,
                "commit": commit,
                "refreshed_at": int(time.time()),
                "entries": entries,
                "skipped": skipped,
            }
        finally:
            shutil.rmtree(stage_parent, ignore_errors=True)

    def _parse_marketplace_entries(self, manifest: dict[str, Any], source: str, commit: str):
        plugins = manifest.get("plugins", [])
        if not isinstance(plugins, list):
            plugins = []
        entries, skipped = [], []
        for item in plugins[:200]:
            if not isinstance(item, dict):
                skipped.append({"name": "", "reason": "invalid_source"})
                continue
            name = item.get("name")
            if not isinstance(name, str) or not _PLUGIN_ID.fullmatch(name):
                skipped.append({"name": str(name or "")[:128], "reason": "invalid_name"})
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            plugin_root = metadata.get("pluginRoot")
            if plugin_root is not None and (
                not isinstance(plugin_root, str) or not plugin_root.startswith("./")
            ):
                skipped.append({"name": name, "reason": "invalid_source"})
                continue
            spec = item.get("source")
            try:
                if isinstance(spec, str) and spec.startswith("./"):
                    relative = "/".join(
                        filter(
                            None, [plugin_root.removeprefix("./") if plugin_root else "", spec[2:]]
                        )
                    )
                    path = "/".join(extension_engine.plugin_subdirectory(relative))
                    entry_source, entry_ref = source, commit
                elif isinstance(spec, dict) and spec.get("source") == "local":
                    local = spec.get("path")
                    if not isinstance(local, str) or not local.startswith("./"):
                        raise ValueError("invalid_source")
                    relative = "/".join(
                        filter(
                            None, [plugin_root.removeprefix("./") if plugin_root else "", local[2:]]
                        )
                    )
                    path = "/".join(extension_engine.plugin_subdirectory(relative))
                    entry_source, entry_ref = source, commit
                elif isinstance(spec, dict) and spec.get("source") == "github":
                    repo = spec.get("repo")
                    if not isinstance(repo, str):
                        raise ValueError("invalid_source")
                    entry_source, entry_ref, path = self._validate_git_source(
                        f"https://github.com/{repo}.git", spec.get("sha") or spec.get("ref")
                    )
                elif isinstance(spec, dict) and spec.get("source") in {"url", "git-subdir"}:
                    if not isinstance(spec.get("url"), str):
                        raise ValueError("invalid_source")
                    entry_source, entry_ref, path = self._validate_git_source(
                        spec.get("url"),
                        spec.get("sha") or spec.get("ref"),
                        spec.get("path") if spec.get("source") == "git-subdir" else None,
                    )
                else:
                    raise ValueError("unsupported_source")
            except Exception as error:
                reason = (
                    str(error)
                    if str(error) in {"unsupported_source", "invalid_source"}
                    else "invalid_source"
                )
                skipped.append({"name": name, "reason": reason})
                continue
            description = item.get("description", "")
            version = item.get("version", "")
            entries.append(
                {
                    "name": name,
                    "description": description if isinstance(description, str) else "",
                    "version": version if isinstance(version, str) else "",
                    "source": entry_source,
                    "path": path or None,
                    "ref": entry_ref or None,
                }
            )
        if len(plugins) > 200:
            skipped.extend({"name": "", "reason": "entry_limit"} for _ in range(len(plugins) - 200))
        return entries, skipped

    def list_marketplaces(self, trusted: Any) -> dict[str, Any]:
        self._personal_identity(trusted)
        with _MARKETPLACE_LOCK:
            return {"marketplaces": self._read_marketplaces()}

    def add_marketplace(self, source: Any, trusted: Any) -> dict[str, Any]:
        self._personal_identity(trusted)
        normalized, ref = self._marketplace_source(source)
        identifier = hashlib.sha256(normalized.encode()).hexdigest()[:12]

        def check(rows: list[dict[str, Any]]) -> None:
            if any(row.get("source") == normalized for row in rows):
                raise ServiceError(
                    "Marketplace already exists", status=409, code="marketplace_exists"
                )
            if len(rows) >= 20:
                raise ServiceError(
                    "Marketplace limit reached", status=409, code="marketplace_limit"
                )

        with _MARKETPLACE_LOCK:
            check(self._read_marketplaces())
        # Clone outside the lock so a slow Git host never blocks list/delete.
        result = {"id": identifier, **self._fetch_marketplace(normalized, ref)}
        with _MARKETPLACE_LOCK:
            rows = self._read_marketplaces()
            check(rows)
            self._write_marketplaces([*rows, result])
        return result

    def refresh_marketplace(self, marketplace_id: str, trusted: Any) -> dict[str, Any]:
        self._personal_identity(trusted)
        with _MARKETPLACE_LOCK:
            row = next(
                (row for row in self._read_marketplaces() if row.get("id") == marketplace_id),
                None,
            )
        if row is None:
            raise ServiceError("Marketplace not found", status=404, code="not_found")
        result = {
            "id": marketplace_id,
            **self._fetch_marketplace(row["source"], row.get("ref") or ""),
        }
        with _MARKETPLACE_LOCK:
            rows = self._read_marketplaces()
            index = next(
                (i for i, item in enumerate(rows) if item.get("id") == marketplace_id), None
            )
            if index is None:  # Deleted while refreshing.
                raise ServiceError("Marketplace not found", status=404, code="not_found")
            rows[index] = result
            self._write_marketplaces(rows)
        return result

    def delete_marketplace(self, marketplace_id: str, trusted: Any) -> dict[str, bool]:
        self._personal_identity(trusted)
        with _MARKETPLACE_LOCK:
            rows = self._read_marketplaces()
            next_rows = [row for row in rows if row.get("id") != marketplace_id]
            if len(next_rows) == len(rows):
                raise ServiceError("Marketplace not found", status=404, code="not_found")
            self._write_marketplaces(next_rows)
        return {"deleted": True}

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
                    "format": str(item.get("format") or "native"),
                    "commit": str(record.get("commit") or "")[:72],
                    "pinned": bool(record.get("pinned")),
                    "path": str(record.get("path") or "")[:512],
                    "dropped_components": self._safe_names(item.get("dropped_components")),
                    "mcp_servers": self._plugin_mcp_rows(
                        profile,
                        plugin_id,
                        item.get("mcp_servers"),
                        bool(item.get("enabled")),
                        record,
                    ),
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
    def _plugin_mcp_rows(
        profile: Path, plugin_id: str, servers: Any, enabled: bool, record: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Return a portable plugin's MCP servers with their chat status."""
        if not isinstance(servers, list):
            return []
        revision = str(record.get("commit") or "")
        state = extension_policy.load_state(profile)
        mappings = state.get("plugin_mcp") or {}
        rows = [
            {
                "name": str(item.get("name", ""))[:128],
                "host": str(item.get("host", ""))[:255],
                "auth_kind": item.get("auth_kind", "none"),
                "upstream_url": item.get("upstream_url", ""),
                "plugin_revision": revision,
                "connection_id": (
                    (mappings.get(f"{plugin_id}/{item.get('name')}") or {}).get("connection_id")
                    if (mappings.get(f"{plugin_id}/{item.get('name')}") or {}).get(
                        "plugin_revision"
                    )
                    == revision
                    else None
                ),
            }
            for item in servers
            if isinstance(item, dict) and item.get("name")
        ]
        status: dict[str, str] = {}
        engine_names: dict[str, str] = {}
        if enabled and rows:
            try:
                from hermes_cli.plugins import _portable_skill_namespace

                from ..integrations.managed_mcp import plugin_mcp_status

                namespace = _portable_skill_namespace(plugin_id)
                engine_names = {row["name"]: f"{namespace}__{row['name']}" for row in rows}
                status = plugin_mcp_status(profile, list(engine_names.values()))
            except Exception:
                status = {}
        for row in rows:
            mapped = row["connection_id"] is not None
            live = status.get(engine_names.get(row["name"], ""), "pending")
            if row["auth_kind"] == "none" and (mapped or live == "needs_sign_in"):
                # No secret header, yet it needs credentials: the server signs in with OAuth.
                row["auth_kind"] = "oauth"
            needs_auth = row["auth_kind"] != "none"
            row["chat_status"] = (
                "disabled"
                if not enabled
                else (
                    status.get(engine_names.get(row["name"], ""), "pending")
                    if mapped or not needs_auth
                    else "needs_sign_in"
                )
            )
        return rows

    def set_plugin_mcp_connection(
        self, agent_id: str, plugin_id: str, server_name: str, body: dict[str, Any], trusted: Any
    ) -> dict[str, Any]:
        profile, _ = self._profile(agent_id, trusted)
        with self._lifecycle(agent_id):
            state = self._state(profile)
            record = state["plugins"].get(plugin_id)
            revision = str(record.get("commit") or "") if record else ""
            if not revision or body.get("plugin_revision") != revision:
                raise ServiceError(
                    "Plugin revision changed", status=409, code="plugin_revision_mismatch"
                )
            from hermes_cli.plugins import _portable_skill_namespace

            engine_name = f"{_portable_skill_namespace(plugin_id)}__{server_name}"
            if engine_name not in extension_engine.portable_plugin_mcp_names(profile, plugin_id):
                raise ServiceError("Plugin MCP server not found", status=404, code="not_found")
            mappings = state.setdefault("plugin_mcp", {})
            mappings[f"{plugin_id}/{server_name}"] = {
                "connection_id": body["connection_id"],
                "plugin_revision": revision,
            }
            self._save(profile, state)
        return {
            "agent_id": agent_id,
            "plugin_id": plugin_id,
            "server_name": server_name,
            "connection_id": body["connection_id"],
            "plugin_revision": revision,
        }

    def delete_plugin_mcp_connection(
        self, agent_id: str, plugin_id: str, server_name: str, trusted: Any
    ) -> dict[str, Any]:
        profile, _ = self._profile(agent_id, trusted)
        with self._lifecycle(agent_id):
            state = self._state(profile)
            state.setdefault("plugin_mcp", {}).pop(f"{plugin_id}/{server_name}", None)
            self._save(profile, state)
        return {
            "agent_id": agent_id,
            "plugin_id": plugin_id,
            "server_name": server_name,
            "deleted": True,
        }

    def reconnect_plugin_mcp(self, agent_id: str, trusted: Any) -> dict[str, Any]:
        """Wake proxy MCP tasks parked on sign-in; Control attaches a fresh grant."""
        profile, _ = self._profile(agent_id, trusted)
        self._set_mcp_grant(profile, trusted)
        return {"agent_id": agent_id, "reconnecting": True}

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
        self._set_mcp_grant(profile, trusted)
        entry = self._catalog_entry(body, trusted)
        server_config, endpoint_mode, auth_configured = self._catalog_server_config(
            entry, body.get("fields", {}), body.get("connection_id")
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

    def _catalog_entry(self, body: dict[str, Any], trusted: Any) -> dict[str, Any]:
        self._personal_identity(trusted)
        entry = next(
            (item for item in self.catalog()["entries"] if item["id"] == body["catalog_id"]),
            None,
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
        return entry

    @staticmethod
    def _read_profile_config(profile: Path) -> dict[str, Any]:
        path = profile / "config.yaml"
        if path.is_symlink():
            raise ServiceError("Unsafe profile config", status=409, code="invalid_profile")
        try:
            config = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
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
        return config

    def _catalog_server_config(
        self, entry: dict[str, Any], fields: dict[str, str], connection_id: str | None = None
    ):
        mcp = entry.get("mcp", {})
        auth = mcp.get("auth", {})
        expected = set()
        url = mcp.get("url", "")
        if auth.get("kind") in {"api_key", "oauth"}:
            if fields or not connection_id:
                raise ServiceError(
                    "Catalog fields do not match this recipe",
                    status=422,
                    code="invalid_catalog_fields",
                )
            try:
                parsed_id = uuid.UUID(connection_id)
            except (TypeError, ValueError, AttributeError) as error:
                raise ServiceError(
                    "Catalog connection ID is invalid", status=422, code="invalid_catalog_fields"
                ) from error
            if str(parsed_id) != connection_id:
                raise ServiceError(
                    "Catalog connection ID is invalid", status=422, code="invalid_catalog_fields"
                )
            control_url = os.environ.get("RUNTIME_CONTROL_URL", "").rstrip("/")
            if not control_url:
                raise ServiceError(
                    "Control MCP proxy is unavailable", status=503, code="mcp_unavailable"
                )
            url = f"{control_url}/xnobrain/api/control/internal/v1/mcp/{connection_id}"
            return (
                {"url": url, "xnobrain_connection_id": connection_id},
                str(mcp.get("endpoint_mode", "static")),
                True,
            )
        if mcp.get("endpoint_mode") == "user_configured":
            expected.add("endpoint_url")
            url = fields.get("endpoint_url", "")
        if set(fields) != expected:
            raise ServiceError(
                "Catalog fields do not match this recipe", status=422, code="invalid_catalog_fields"
            )
        self._validate_catalog_endpoint(str(url), str(mcp.get("url_policy", "")))
        result: dict[str, Any] = {"url": url}
        if auth.get("kind") != "none":
            raise ServiceError(
                "Catalog authentication is unsupported", status=422, code="unsupported_auth"
            )
        return (
            result,
            str(mcp.get("endpoint_mode", "static")),
            False,
        )

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
            self._detach_catalog(agent_id, profile, catalog_id, remove=False)
        return {"agent_id": agent_id, "catalog_id": catalog_id, "enabled": False}

    def _detach_catalog(
        self, agent_id: str, profile: Path, catalog_id: str, *, remove: bool
    ) -> None:
        """Stop a catalog connection; ``remove`` also forgets its record.

        The caller holds the agent lifecycle lock.
        """
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
            yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
        )
        if config is None:
            config = {}
        if not isinstance(config, dict):
            raise ServiceError(
                "Profile config is invalid", status=409, code="invalid_profile_config"
            )
        servers = config.get(extension_policy.MANAGED_MCP_KEY) or {}
        if not isinstance(servers, dict):
            raise ServiceError("MCP config is invalid", status=409, code="invalid_mcp_config")
        server_name = record["server_name"]
        connected = server_name in servers
        if not connected and not remove:
            raise ServiceError(
                "Catalog integration is not configured", status=404, code="not_found"
            )
        self._snapshot_config(agent_id, profile)
        if connected:
            removed_config = servers.get(server_name)
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
        if remove:
            state["mcp"].pop(catalog_id, None)
        state["revision"] = int(state.get("revision", 1)) + 1
        self._save(profile, state)
        if connected:
            marker.unlink(missing_ok=True)
            extension_engine.mcp_disconnect(profile, server_name)

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
                self._sync_plugin_mcp_shadows(profile)
            except (ImportError, AttributeError, RuntimeError, ValueError) as error:
                raise ServiceError(
                    "Plugin lifecycle is unavailable",
                    status=503,
                    code="extension_engine_unavailable",
                ) from error
        return {"agent_id": agent_id, "plugin_id": plugin_id, "enabled": enabled, "result": result}

    def _sync_plugin_mcp_shadows(self, profile: Path) -> None:
        config = self._plugin_mcp_shadows(profile)
        if config is not None:
            extension_engine._write_config(profile, config)

    def _plugin_mcp_shadows(self, profile: Path) -> dict[str, Any] | None:
        """Return the config with one-shot plugin MCP shadows, or None if unchanged."""
        config = self._read_profile_config(profile)
        servers = config.get("mcp_servers") or {}
        if not isinstance(servers, dict):
            raise ServiceError("MCP config is invalid", status=409, code="invalid_mcp_config")
        original = json.dumps(config, sort_keys=True, separators=(",", ":"))
        servers = dict(servers)
        for name, server in list(servers.items()):
            if isinstance(server, dict) and server.get("xnobrain_plugin_shadow"):
                servers.pop(name, None)
        plugin_root = profile / "plugins"
        if plugin_root.is_dir():
            for plugin in plugin_root.iterdir():
                if not plugin.is_dir() or plugin.is_symlink():
                    continue
                try:
                    names = extension_engine.portable_plugin_mcp_names(profile, plugin.name)
                except Exception:  # noqa: BLE001 - an unreadable package is not loadable by the engine either
                    continue
                for name in names:
                    if name not in servers:
                        servers[name] = {"enabled": False, "xnobrain_plugin_shadow": True}
        if servers:
            config["mcp_servers"] = servers
        else:
            config.pop("mcp_servers", None)
        if json.dumps(config, sort_keys=True, separators=(",", ":")) == original:
            return None
        return config

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

    def create_operation(
        self, agent_id: str, body: dict[str, Any], trusted: Any, *, _upload: bool = False
    ) -> dict[str, Any]:
        profile, owner = self._profile(agent_id, trusted)
        kind = str(body.get("kind", ""))
        if kind in {"install", "probe"}:
            self._set_mcp_grant(profile, trusted)
        plugin_id = str(body.get("plugin_id", ""))
        if not _PLUGIN_ID.fullmatch(plugin_id):
            raise ServiceError("Plugin id is invalid", status=422, code="invalid_plugin_id")
        with self._lifecycle(agent_id):
            profile_generation = self._profile_generation(profile, initialize=True)
            if kind in {"install", "update"}:
                if kind == "update" and str(body.get("source") or "").startswith("upload:"):
                    raise ServiceError(
                        "Uploaded plugins must be uploaded again to update",
                        status=422,
                        code="upload_update_requires_reupload",
                    )
                if kind == "install" and str(body.get("source") or "").startswith("upload:"):
                    if not _upload:
                        raise ServiceError(
                            "Upload sources are not accepted here",
                            status=422,
                            code="invalid_git_source",
                        )
                    source = str(body["source"])
                    ref, path = "", ""
                elif kind == "update" and self._state(profile)["plugins"].get(plugin_id, {}).get(
                    "source", ""
                ).startswith("upload:"):
                    raise ServiceError(
                        "Uploaded plugins must be uploaded again to update",
                        status=422,
                        code="upload_update_requires_reupload",
                    )
                elif kind == "update" and not body.get("source"):
                    prior = self._state(profile)["plugins"].get(plugin_id, {})
                    if not prior.get("source"):
                        raise ServiceError(
                            "Plugin source is unavailable",
                            status=409,
                            code="plugin_source_unavailable",
                        )
                    # A pinned commit stays pinned until the user names a new ref.
                    source, ref, path = self._validate_git_source(
                        prior["source"],
                        body.get("ref") or prior.get("ref") or "",
                        body.get("path") or prior.get("path") or "",
                    )
                else:
                    source, ref, path = self._validate_git_source(
                        body.get("source"), body.get("ref"), body.get("path")
                    )
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
                source, ref, path = "", "", ""
            inputs = {
                "plugin_id": plugin_id,
                "source": source,
                "ref": ref,
                "path": path,
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
    def _set_mcp_grant(profile: Path, trusted: Any) -> None:
        grant = str(getattr(trusted, "mcp_grant", "") or "")
        if not grant:
            return
        from ..integrations.control_grants import set as set_control_grant
        from ..integrations.managed_mcp import _profile_scope

        set_control_grant(_profile_scope(profile), grant)

    @staticmethod
    def _validate_git_source(source: Any, ref: Any, path: Any = None) -> tuple[str, str, str]:
        if not isinstance(source, str) or len(source) > 2048 or source.startswith("upload:"):
            raise ServiceError("Git source is invalid", status=422, code="invalid_git_source")
        if path is not None and not isinstance(path, str):
            raise ServiceError("Plugin subdirectory is invalid", status=422, code="invalid_source")
        path = (path or "").rstrip("/")
        shorthand = re.fullmatch(r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)((?:/[^/]+)+)?", source)
        if shorthand and "://" not in source:
            # GitHub "owner/repo/sub/dir" installs one subdirectory of the repo.
            if shorthand.group(2):
                if path:
                    raise ServiceError(
                        "Give the subdirectory once", status=422, code="invalid_source"
                    )
                path = shorthand.group(2).strip("/")
            source = f"https://github.com/{shorthand.group(1)}.git"
        try:
            extension_engine.plugin_subdirectory(path)
        except Exception as error:
            raise ServiceError(
                "Plugin subdirectory is invalid", status=422, code="invalid_source"
            ) from error
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
        if ref and (not isinstance(ref, str) or not _GIT_REF.fullmatch(ref)):
            raise ServiceError(
                "Git branch, tag or commit is invalid", status=422, code="invalid_git_ref"
            )
        return source.rstrip("/"), str(ref or ""), path

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
                elif code in {
                    "plugin_scan_incomplete",
                    "plugin_unsafe",
                    "plugin_manifest_invalid",
                    "plugin_kind_unsupported",
                }:
                    status = "BLOCKED"
                else:
                    status = "FAILED"
                # Expected errors carry fixed, public reasons; anything else stays generic.
                message = "Operation failed"
                if isinstance(error, (ServiceError, AgentAPIError)) and str(error):
                    message = str(error)[:200]
                self.service.store.update(
                    operation,
                    status=status,
                    progress=None,
                    error={
                        "code": code,
                        "message": "Operation cancelled" if status == "CANCELLED" else message,
                    },
                )
            finally:
                source = (operation.get("inputs") or {}).get("source") or ""
                upload_id = source.partition(":")[2] if source.startswith("upload:") else ""
                if re.fullmatch(
                    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                    upload_id,
                ):
                    root = self.service.repository.data_dir / "extension-staging"
                    uploads = root / "uploads"
                    if not root.is_symlink() and not uploads.is_symlink():
                        (uploads / f"{upload_id}.zip").unlink(missing_ok=True)

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
                artifact = profile / "plugins" / plugin_id
                state = self.service._state(profile)
                has_artifact = artifact.is_dir() and not artifact.is_symlink()
                if not has_artifact and plugin_id in state["mcp"]:
                    # Catalog integrations have no artifact; removing one drops
                    # its managed connection and its catalog record.
                    self.service._detach_catalog(agent_id, profile, plugin_id, remove=True)
                elif not has_artifact and plugin_id not in state["plugins"]:
                    raise ServiceError("Plugin not found", status=404, code="plugin_not_found")
                else:
                    self.service._snapshot_config(agent_id, profile)
                    extension_engine.plugin_remove(profile, plugin_id)
                    self.service._sync_plugin_mcp_shadows(profile)
                    state = self.service._state(profile)
                    state["plugins"].pop(plugin_id, None)
                    state["plugin_mcp"] = {
                        key: value
                        for key, value in (state.get("plugin_mcp") or {}).items()
                        if not key.startswith(f"{plugin_id}/")
                    }
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
        if operation["kind"] == "update" and (inputs.get("source") or "").startswith("upload:"):
            raise ServiceError(
                "Uploaded plugins must be uploaded again to update",
                status=422,
                code="upload_update_requires_reupload",
            )
        try:
            self.service.store.update(
                operation,
                status="PROCESSING",
                progress={
                    "phase": "extracting"
                    if (inputs.get("source") or "").startswith("upload:")
                    else "cloning"
                },
            )
            if (inputs.get("source") or "").startswith("upload:"):
                upload_id = inputs["source"].partition(":")[2]
                if not re.fullmatch(
                    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", upload_id
                ):
                    raise ServiceError(
                        "Upload reference is invalid", status=422, code="invalid_plugin_archive"
                    )
                uploads = stage_root / "uploads"
                archive = uploads / f"{upload_id}.zip"
                if uploads.is_symlink() or archive.is_symlink() or not archive.is_file():
                    raise ServiceError(
                        "Uploaded archive is unavailable", status=422, code="invalid_plugin_archive"
                    )
                commit = extension_engine.extract_plugin_archive(
                    archive, stage, cancelled=lambda: self.service.store.cancelled(operation)
                )
            else:
                commit = extension_engine.clone_plugin(
                    inputs["source"],
                    inputs["ref"],
                    stage,
                    cancelled=lambda: self.service.store.cancelled(operation),
                    **({"path": inputs["path"]} if inputs.get("path") else {}),
                )
            declared, package_format = plugin_formats.plugin_identity(stage)
            dropped: list[str] | None = None
            if package_format == plugin_formats.CLAUDE:
                # Only skills and remote MCP survive; the rest is reported.
                dropped = plugin_formats.convert_claude_plugin(stage)
            if package_format in {plugin_formats.PORTABLE, plugin_formats.CLAUDE}:
                plugin_formats.strip_secret_placeholders(stage)
            self.service.store.update(
                operation, status="PROCESSING", progress={"phase": "scanning"}
            )
            scan_result = extension_engine.scan_plugin(stage)
            scan = scan_result["scan"]
            if self.service.store.cancelled(operation):
                self.service.store.update(operation, status="CANCELLED")
                return
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
                        "commit": ("upload:" + commit)
                        if (inputs.get("source") or "").startswith("upload:")
                        else commit,
                        "format": package_format,
                        "dropped_components": dropped or [],
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
                    self.service._sync_plugin_mcp_shadows(profile)
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
                    self.service._sync_plugin_mcp_shadows(profile)
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
                        "path": inputs.get("path") or "",
                        "pinned": bool(re.fullmatch(r"[0-9a-f]{40}", inputs["ref"] or "")),
                        "commit": ("upload:" + commit)
                        if (inputs.get("source") or "").startswith("upload:")
                        else commit,
                        "format": package_format,
                        "dropped_components": dropped,
                        "artifact_digest": scan_result["artifact_digest"],
                        "scan": scan,
                        "approval": {"approved": False, "artifact_digest": ""},
                        "hidden": bool(previous.get("hidden", False)),
                        "revision": int(previous.get("revision", 0)) + 1,
                    }
                    state["plugin_mcp"] = {
                        key: value
                        for key, value in (state.get("plugin_mcp") or {}).items()
                        if not key.startswith(f"{plugin_id}/")
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
                    "commit": ("upload:" + commit)
                    if (inputs.get("source") or "").startswith("upload:")
                    else commit,
                    "format": package_format,
                    "dropped_components": dropped or [],
                    "artifact_digest": scan_result["artifact_digest"],
                    "scan": self.service._public_scan(scan),
                },
            )
        finally:
            source = inputs.get("source") or ""
            upload_id = source.partition(":")[2] if source.startswith("upload:") else ""
            uploads = stage_root / "uploads"
            if (
                re.fullmatch(
                    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                    upload_id,
                )
                and not uploads.is_symlink()
            ):
                (uploads / f"{upload_id}.zip").unlink(missing_ok=True)
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
