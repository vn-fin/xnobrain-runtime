"""Adapter for the original Hermes CLI, core runtime, and native profiles."""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import string
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml

from ..defaults import (
    BIG_BROTHER_AGENT_ID,
    BIG_BROTHER_DESCRIPTION,
    BIG_BROTHER_DISPLAY_NAME,
    BIG_BROTHER_SKILL_CATEGORY,
    CUSTOM_SKILL_CATEGORY,
    honcho_memory_enabled,
)
from .nine_router import (
    NINE_ROUTER_DEFAULT_MODEL,
    NINE_ROUTER_PROVIDER,
    NineRouterAPIError,
    NineRouterManager,
    display_nine_router_model,
    normalize_nine_router_config,
    route_nine_router_model,
)


AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
GENERATED_AGENT_NAME_RE = re.compile(r"^[a-z][a-z0-9]{5}$")
SKILL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_TEXT_CHARS = 200_000
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_CHAT_TIMEOUT_SECONDS = 3600
DEFAULT_CHAT_TIMEOUT_SECONDS = 900
GENERATED_AGENT_ID_LENGTH = 6
GENERATED_AGENT_ID_FIRST_ALPHABET = string.ascii_lowercase
GENERATED_AGENT_ID_ALPHABET = string.ascii_lowercase + string.digits
PROVIDER_ERROR_OUTPUT_RE = re.compile(
    r"^HTTP\s+[45]\d{2}:\s+\[[^\]\n]+\]\s+\[[45]\d{2}\]:",
    re.IGNORECASE,
)
DEFAULT_ROOT_PROFILE = str(Path.home() / ".hermes")
DEFAULT_PROFILES_ROOT = str(Path.home() / ".hermes" / "profiles")
DEFAULT_LEGACY_AGENTS_ROOT = str(Path.home() / ".hermes" / "legacy-agents")
DEFAULT_AGENT_CONFIG_DIR = str(Path.home() / ".config" / "sandbox-agent")
DEFAULT_CONVERSATION_TITLE = "New Session"
DEFAULT_CONVERSATION_TITLE_RE = re.compile(
    r"^New Session(?: ([1-9][0-9]*))?$",
    re.IGNORECASE,
)
METADATA_FILE = "agent.json"
PROFILES_REGISTRY_FILE = "profiles.yaml"
CREDENTIAL_FILES = (".env", "auth.json")
AGENT_CREDENTIAL_ENV_KEYS = ("NINE_ROUTER_API_KEY",)
TEMPLATE_FILES = ("config.yaml", "SOUL.md", "AGENTS.md", "mcp.json")
TEMPLATE_DIRS = ("memories", "plugins")
PROFILE_STATE_DIRS = (
    "skills",
    "sessions",
    "logs",
    "memories",
    "cron",
    "plugins",
    "home",
)
MANAGED_RUNTIME_HELP_GUIDANCE = ""
LEGACY_MANAGED_AGENT_GUIDANCE = (
    """You are an AI agent. If asked who or what you are, describe yourself only
as an AI agent and summarize relevant capabilities. Do not identify the application,
runtime, framework, model provider, or implementation vendor.
When a response relies on web browsing or search, cite every source used with a
direct clickable link near the supported claim. When a response relies on a document,
cite its title or durable path and exact page number or range. Never fabricate a
citation, and distinguish sourced facts from inference.""",
    """You are an AI agent. If asked who or what you are, describe yourself only
as an AI agent and summarize relevant capabilities. Do not identify the application,
runtime, framework, model provider, or implementation vendor.
For direct identity questions, answer exactly as an AI agent without naming or
guessing any underlying product, runtime, framework, provider, or vendor.
When a response relies on web browsing or search, cite every source used with a
direct clickable link near the supported claim. When a response relies on a document,
cite its title or durable path and exact page number or range. Never fabricate a
citation, and distinguish sourced facts from inference.""",
    """You are an AI agent. If asked who or what you are, describe yourself only
as an AI agent and summarize relevant capabilities. Do not identify the
application, runtime, framework, model provider, or implementation vendor.
Cite every web source with a direct clickable link near the supported claim.
Cite document-derived claims with the document title or path and exact page
number or range. Never fabricate citations; distinguish inference clearly.""",
)


def _new_conversation_id() -> str:
    """Use the native CLI session shape for API-created conversations."""
    return f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


class AgentAPIError(ValueError):
    """Expected API error for named-agent operations."""

    def __init__(self, message: str, *, code: str = "invalid_agent", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


class AgentManager:
    """Filesystem-backed manager for named Hermes agents in one runtime process."""

    def __init__(
        self,
        *,
        root_profile: str | Path | None = None,
        profiles_root: str | Path | None = None,
        legacy_agents_root: str | Path | None = None,
        profile_template: str | Path | None = None,
    ):
        self.root_profile = Path(
            root_profile
            or os.environ.get("HERMES_ROOT_PROFILE")
            or DEFAULT_ROOT_PROFILE
        )
        self.profiles_root = Path(
            profiles_root
            or os.environ.get("HERMES_PROFILES_ROOT")
            or self.root_profile / "profiles"
            or DEFAULT_PROFILES_ROOT
        )
        self.legacy_agents_root = Path(
            legacy_agents_root
            or os.environ.get("HERMES_LEGACY_AGENTS_ROOT")
            or os.environ.get("HERMES_AGENTS_ROOT")
            or self.root_profile.parent / "legacy-agents"
        )
        configured_template = (
            profile_template
            or os.environ.get("XNOBRAIN_PROFILE_TEMPLATE")
            or self.root_profile / "profile-template"
        )
        configured_template = Path(configured_template)
        bundled_template = Path(__file__).resolve().parents[2] / "runtime" / "profile-templates"
        self.profile_template = (
            configured_template
            if configured_template.is_dir()
            else bundled_template
        )
        self.nine_router = NineRouterManager()
        self._active_runs: dict[str, dict[str, Any]] = {}
        self._stopped_runs: set[str] = set()
        self._registry_lock = threading.RLock()
        self._conversation_lock = threading.RLock()
        self._skill_sync_lock = threading.RLock()
        self.sync_profiles_registry()

    def list_agents(self) -> dict[str, Any]:
        self.profiles_root.mkdir(parents=True, exist_ok=True)
        self.sync_profiles_registry()
        agents = [self.describe_agent(BIG_BROTHER_AGENT_ID, include_memory=False)]
        seen: set[str] = {BIG_BROTHER_AGENT_ID}
        for path in sorted(self.profiles_root.iterdir(), key=lambda item: item.name):
            if path.name == BIG_BROTHER_AGENT_ID or not self._is_native_agent_profile(path):
                continue
            seen.add(path.name)
            agents.append(self.describe_agent(path.name, include_memory=False))
        if self.legacy_agents_root.is_dir():
            for path in sorted(self.legacy_agents_root.iterdir(), key=lambda item: item.name):
                if path.name in seen or not path.is_dir() or not (path / ".profile").is_dir():
                    continue
                agents.append(self.describe_agent(path.name, include_memory=False))
        agents.sort(
            key=lambda item: (
                str(item.get("name") or "") != BIG_BROTHER_AGENT_ID,
                str(item.get("name") or "").lower(),
            )
        )
        return {
            "object": "hermes.agents",
            "root": str(self.profiles_root),
            "agents": agents,
        }

    def list_agent_names(self) -> list[str]:
        """Return valid profile names without loading configs, skills, or memory."""
        names = [BIG_BROTHER_AGENT_ID]
        seen = {BIG_BROTHER_AGENT_ID}
        for path in sorted(self.profiles_root.iterdir(), key=lambda item: item.name):
            if path.name in seen or not self._is_native_agent_profile(path):
                continue
            seen.add(path.name)
            names.append(path.name)
        if self.legacy_agents_root.is_dir():
            for path in sorted(self.legacy_agents_root.iterdir(), key=lambda item: item.name):
                if path.name in seen or not path.is_dir() or not (path / ".profile").is_dir():
                    continue
                seen.add(path.name)
                names.append(path.name)
        return names

    def profile_path(self, raw_name: Any) -> Path:
        """Return an existing profile path without building its full agent DTO."""
        return self._require_profile(self._agent_name(raw_name)).resolve()

    def create_agent(self, body: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
        name = self._agent_name(body.get("name") or self._new_agent_name())
        existed = self._existing_profile_dir(name) is not None
        if existed and not bool(body.get("idempotent", False)):
            raise AgentAPIError(
                f"Agent already exists: {name}", code="agent_exists", status=409
            )

        profile_dir = self._profile_dir(name)
        workspace_dir = self._workspace_dir(name)
        try:
            profile_dir.mkdir(parents=True, exist_ok=True)
            workspace_dir.mkdir(parents=True, exist_ok=True)
            for dirname in PROFILE_STATE_DIRS:
                (profile_dir / dirname).mkdir(parents=True, exist_ok=True)

            if not existed or bool(body.get("refresh_seed", False)):
                self._copy_seed_profile(profile_dir, copy_credentials=bool(body.get("copy_credentials", True)))
                self._copy_root_skills(profile_dir, overwrite=True)
                self._clear_seeded_disabled_skills(profile_dir)
            self._ensure_router_profile(profile_dir, body.get("model"))
            self._write_workspace_cwd(profile_dir, workspace_dir)
            self._ensure_workspace_agents(profile_dir, workspace_dir)
            self._initialize_state_db(profile_dir)

            metadata = self._read_metadata(profile_dir)
            now = time.time()
            metadata.setdefault("name", name)
            metadata.setdefault("profile_name", name)
            metadata.setdefault("created_at", now)
            metadata["updated_at"] = now
            for field in ("description", "title", "display_name"):
                if field in body:
                    value = body.get(field)
                    metadata[field] = "" if value is None else str(value).strip()
            metadata.setdefault("display_name", str(metadata.get("title") or name))
            self._write_metadata(profile_dir, metadata)
            self._write_profile_manifest(profile_dir, metadata)
            self.sync_profiles_registry()

            if "soul" in body:
                self._write_text(profile_dir / "SOUL.md", body.get("soul"))
            if "memory" in body:
                self.write_memory(name, {"memory": body.get("memory")})
            if "instructions" in body:
                self._write_text(workspace_dir / "AGENTS.md", body.get("instructions"))
            if isinstance(body.get("config"), Mapping):
                self.update_config(name, body["config"])
            return self.describe_agent(name), 200 if existed else 201
        except Exception:
            if not existed:
                shutil.rmtree(profile_dir, ignore_errors=True)
                try:
                    self.sync_profiles_registry()
                except Exception:
                    pass
            raise

    def describe_agent(self, raw_name: Any, *, include_memory: bool = True) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        workspace_dir = self._workspace_dir(name)
        metadata = self._read_metadata(profile_dir)
        registry = self._registry_profile(name)
        if registry:
            metadata["display_name"] = registry["display_name"]
            metadata["description"] = registry["description"]
        if name == BIG_BROTHER_AGENT_ID:
            metadata["display_name"] = BIG_BROTHER_DISPLAY_NAME
            metadata["title"] = BIG_BROTHER_DISPLAY_NAME
            metadata["description"] = BIG_BROTHER_DESCRIPTION
        config = self._read_config(profile_dir)
        payload = {
            "object": "hermes.agent",
            "name": name,
            "profile_name": name,
            "path": str(self._agent_dir(name)),
            "profile_path": str(profile_dir),
            "workspace_path": str(workspace_dir),
            "metadata": metadata,
            "config": self._effective_config(config, metadata),
            "soul": self._read_text(profile_dir / "SOUL.md"),
            "skills": self.list_skills(name)["skills"],
        }
        if include_memory:
            payload["memory"] = self.read_memory(name)
        return payload

    def migrate_legacy_big_brother_profile(self) -> dict[str, int]:
        """Merge data created by the former named profile into the root profile."""
        legacy_profile = self.profiles_root / BIG_BROTHER_AGENT_ID
        if not legacy_profile.is_dir() or legacy_profile == self.root_profile:
            return {"skills": 0, "conversations": 0}

        migrated_skills = 0
        root_skills = self.root_profile / "skills"
        legacy_skills = legacy_profile / "skills"
        root_skills.mkdir(parents=True, exist_ok=True)
        if legacy_skills.is_dir():
            for skill_file in sorted(legacy_skills.rglob("SKILL.md")):
                frontmatter = self._read_skill_frontmatter(skill_file)
                skill_id = str(
                    frontmatter.get("name") or skill_file.parent.name
                ).strip()
                if not skill_id or self._find_agent_skill(self.root_profile, skill_id):
                    continue
                relative = skill_file.parent.relative_to(legacy_skills)
                if relative.parts[:1] != (CUSTOM_SKILL_CATEGORY,):
                    relative = Path(CUSTOM_SKILL_CATEGORY) / relative
                destination = root_skills / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.parent / (
                    f".{destination.name}.migrating-{uuid.uuid4().hex}"
                )
                shutil.copytree(skill_file.parent, temporary)
                try:
                    os.replace(temporary, destination)
                except FileExistsError:
                    shutil.rmtree(temporary, ignore_errors=True)
                    continue
                migrated_skills += 1

        legacy_db = legacy_profile / "state.db"
        if not legacy_db.is_file():
            return {"skills": migrated_skills, "conversations": 0}
        self._initialize_state_db(self.root_profile)
        root_db = self.root_profile / "state.db"

        with sqlite3.connect(root_db) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("ATTACH DATABASE ? AS legacy", (str(legacy_db),))
            root_session_ids = {
                str(row[0])
                for row in connection.execute("SELECT id FROM main.sessions")
            }
            legacy_session_ids = [
                str(row[0])
                for row in connection.execute("SELECT id FROM legacy.sessions")
                if str(row[0]) not in root_session_ids
            ]
            if not legacy_session_ids:
                connection.execute("DETACH DATABASE legacy")
                return {"skills": migrated_skills, "conversations": 0}

            backup = (
                self.root_profile
                / "snapshots"
                / "migrations"
                / f"before-big-brother-{time.time_ns()}.db"
            )
            self._write_bytes_atomic(backup, root_db.read_bytes(), mode=0o440)
            placeholders = ",".join("?" for _ in legacy_session_ids)
            try:
                connection.execute("BEGIN IMMEDIATE")
                session_columns = self._shared_sqlite_columns(
                    connection, "sessions", exclude=()
                )
                quoted_sessions = ", ".join(f'"{item}"' for item in session_columns)
                connection.execute(
                    f'INSERT INTO main.sessions ({quoted_sessions}) '
                    f'SELECT {quoted_sessions} FROM legacy.sessions '
                    f'WHERE id IN ({placeholders})',
                    legacy_session_ids,
                )
                message_columns = self._shared_sqlite_columns(
                    connection, "messages", exclude=("id",)
                )
                quoted_messages = ", ".join(f'"{item}"' for item in message_columns)
                connection.execute(
                    f'INSERT INTO main.messages ({quoted_messages}) '
                    f'SELECT {quoted_messages} FROM legacy.messages '
                    f'WHERE session_id IN ({placeholders})',
                    legacy_session_ids,
                )
                usage_columns = self._shared_sqlite_columns(
                    connection, "session_model_usage", exclude=()
                )
                if usage_columns:
                    quoted_usage = ", ".join(f'"{item}"' for item in usage_columns)
                    connection.execute(
                        f'INSERT OR IGNORE INTO main.session_model_usage ({quoted_usage}) '
                        f'SELECT {quoted_usage} FROM legacy.session_model_usage '
                        f'WHERE session_id IN ({placeholders})',
                        legacy_session_ids,
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.execute("DETACH DATABASE legacy")
        return {
            "skills": migrated_skills,
            "conversations": len(legacy_session_ids),
        }

    @staticmethod
    def _shared_sqlite_columns(
        connection: sqlite3.Connection,
        table: str,
        *,
        exclude: tuple[str, ...],
    ) -> list[str]:
        main = {
            str(row[1])
            for row in connection.execute(f'PRAGMA main.table_info("{table}")')
        }
        legacy = [
            str(row[1])
            for row in connection.execute(f'PRAGMA legacy.table_info("{table}")')
        ]
        return [item for item in legacy if item in main and item not in exclude]

    @staticmethod
    def _write_bytes_atomic(path: Path, payload: bytes, *, mode: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            os.fchmod(descriptor, mode)
            with os.fdopen(descriptor, "wb") as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    def sync_profiles_registry(self) -> dict[str, Any]:
        """Atomically backfill root ``profiles.yaml`` from all Hermes profiles."""
        self.root_profile.mkdir(parents=True, exist_ok=True)
        self.profiles_root.mkdir(parents=True, exist_ok=True)
        with self._registry_lock:
            current = {
                str(item.get("name") or ""): item
                for item in self._read_profiles_registry().get("profiles", [])
                if isinstance(item, Mapping) and str(item.get("name") or "")
            }
            entries = [self._profile_registry_entry("default", self.root_profile, current.get("default"))]
            for profile_dir in sorted(self.profiles_root.iterdir(), key=lambda item: item.name):
                if (
                    profile_dir.name != BIG_BROTHER_AGENT_ID
                    and self._is_native_agent_profile(profile_dir)
                ):
                    entries.append(self._profile_registry_entry(profile_dir.name, profile_dir, current.get(profile_dir.name)))
            if self.legacy_agents_root.is_dir():
                for agent_dir in sorted(self.legacy_agents_root.iterdir(), key=lambda item: item.name):
                    profile_dir = agent_dir / ".profile"
                    if profile_dir.is_dir() and agent_dir.name not in {item["name"] for item in entries}:
                        entries.append(self._profile_registry_entry(agent_dir.name, profile_dir, current.get(agent_dir.name)))
            payload = {"profiles": entries}
            if payload != self._read_profiles_registry():
                self._write_profiles_registry(payload)
            return payload

    def update_profile_registry(
        self,
        name: str,
        *,
        display_name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Update human-facing metadata in the authoritative root registry."""
        self._agent_name(name)
        registry_name = "default" if name == BIG_BROTHER_AGENT_ID else name
        with self._registry_lock:
            payload = self.sync_profiles_registry()
            entry = next(
                (item for item in payload["profiles"] if item["name"] == registry_name),
                None,
            )
            if entry is None:
                raise AgentAPIError(f"Agent not found: {name}", code="agent_not_found", status=404)
            if display_name is not None:
                clean_name = str(display_name).strip()
                if not clean_name:
                    raise AgentAPIError("display_name is required", code="invalid_display_name")
                entry["display_name"] = clean_name
            if description is not None:
                entry["description"] = str(description).strip()
            entry["updated_at"] = self._iso_timestamp(time.time())
            self._write_profiles_registry(payload)
            return entry

    def _profile_registry_entry(
        self,
        name: str,
        profile_dir: Path,
        existing: Mapping[str, Any] | None,
    ) -> dict[str, str]:
        metadata = self._read_metadata(profile_dir)
        profile_meta: dict[str, Any] = {}
        profile_yaml = profile_dir / "profile.yaml"
        if profile_yaml.is_file():
            try:
                loaded = yaml.safe_load(profile_yaml.read_text(encoding="utf-8")) or {}
                if isinstance(loaded, dict):
                    profile_meta = loaded
            except (OSError, yaml.YAMLError):
                pass
        existing = existing or {}
        display_name = str(
            existing.get("display_name")
            or metadata.get("display_name")
            or metadata.get("title")
            or ("Default profile" if name == "default" else name)
        ).strip()
        description = str(
            existing.get("description")
            or metadata.get("description")
            or profile_meta.get("description")
            or ""
        ).strip()
        updated_value = existing.get("updated_at") or metadata.get("updated_at")
        if updated_value is None:
            source = profile_dir / "config.yaml"
            updated_value = source.stat().st_mtime if source.exists() else profile_dir.stat().st_mtime
        return {
            "description": description,
            "name": name,
            "display_name": display_name,
            "updated_at": self._iso_timestamp(updated_value),
        }

    def _registry_profile(self, name: str) -> dict[str, Any] | None:
        registry_name = "default" if name == BIG_BROTHER_AGENT_ID else name
        for item in self._read_profiles_registry().get("profiles", []):
            if (
                isinstance(item, dict)
                and str(item.get("name") or "") == registry_name
            ):
                return item
        return None

    def _read_profiles_registry(self) -> dict[str, Any]:
        path = self.root_profile / PROFILES_REGISTRY_FILE
        if not path.is_file():
            return {"profiles": []}
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return {"profiles": []}
        profiles = loaded.get("profiles", []) if isinstance(loaded, dict) else []
        return {"profiles": profiles if isinstance(profiles, list) else []}

    def _write_profiles_registry(self, payload: Mapping[str, Any]) -> None:
        path = self.root_profile / PROFILES_REGISTRY_FILE
        serialized = yaml.safe_dump(dict(payload), sort_keys=False, allow_unicode=True).encode("utf-8")
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            os.fchmod(descriptor, 0o640)
            with os.fdopen(descriptor, "wb") as file:
                file.write(serialized)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    @staticmethod
    def _iso_timestamp(value: Any) -> str:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), timezone.utc).isoformat().replace("+00:00", "Z")
        text = str(value or "").strip()
        if not text:
            return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return text

    def delete_agent(self, raw_name: Any) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        if name == BIG_BROTHER_AGENT_ID:
            raise AgentAPIError(
                "Big Brother is the protected default Hermes profile",
                code="protected_agent",
                status=409,
            )
        profile_dir = self._require_profile(name)
        if profile_dir == self._legacy_profile_dir(name):
            shutil.rmtree(self._legacy_agent_dir(name))
        else:
            shutil.rmtree(profile_dir)
        return {"object": "hermes.agent_delete", "agent": name, "deleted": True}

    def update_config(self, raw_name: Any, body: Mapping[str, Any]) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        config_path = profile_dir / "config.yaml"
        config = self._read_config(profile_dir)

        allowed = {
            "provider",
            "model",
            "reasoning",
            "effort",
            "approval_mode",
            "skills_write_approval",
            "memory_write_approval",
            "system_prompt",
            "language",
            "stream_output",
            "config",
            "soul",
        }
        unknown = sorted(set(body) - allowed)
        if unknown:
            raise AgentAPIError(
                f"Unsupported config fields: {', '.join(unknown)}",
                code="invalid_agent_config",
            )

        if "provider" in body:
            provider = self._nonempty_string(body["provider"], "provider").lower()
            if provider not in {"9router", "nine-router", "auto", NINE_ROUTER_PROVIDER}:
                raise AgentAPIError(
                    "provider must be nine-router",
                    code="unsupported_provider",
                )
        if "model" in body:
            model = self._nonempty_string(body["model"], "model")
            self._set_nested(config, ("model", "default"), model)
        if isinstance(body.get("config"), Mapping):
            self._deep_merge(config, dict(body["config"]))
        if "reasoning" in body or "effort" in body:
            effort = str(body.get("effort") or self._get_nested(config, ("agent", "reasoning_effort"), "medium")).strip().lower()
            if "reasoning" in body and not self._coerce_bool(body["reasoning"]):
                effort = "none"
            elif effort == "none":
                effort = "medium"
            if effort not in {"none", "minimal", "low", "medium", "high", "xhigh"}:
                raise AgentAPIError(
                    "effort must be one of: minimal, low, medium, high, xhigh",
                    code="invalid_agent_config",
                )
            self._set_nested(config, ("agent", "reasoning_effort"), effort)
        if "approval_mode" in body:
            mode = self._approval_mode(body["approval_mode"])
            self._set_nested(config, ("approvals", "mode"), "manual" if mode == "on" else "off")
        if "skills_write_approval" in body:
            self._set_nested(
                config,
                ("skills", "write_approval"),
                self._coerce_bool(body["skills_write_approval"]),
            )
        if "memory_write_approval" in body:
            self._set_nested(
                config,
                ("memory", "write_approval"),
                self._coerce_bool(body["memory_write_approval"]),
            )
        if "system_prompt" in body:
            prompt = self._text_value(body["system_prompt"], field="system_prompt", max_chars=20_000)
            self._set_nested(config, ("agent", "system_prompt"), prompt)
        if "language" in body:
            language = self._text_value(body["language"], field="language", max_chars=128).strip()
            self._set_nested(config, ("agent", "language"), language)
        if "stream_output" in body:
            self._set_nested(config, ("agent", "stream_output"), self._coerce_bool(body["stream_output"]))
        self._set_nested(config, ("terminal", "backend"), self._get_nested(config, ("terminal", "backend"), "local"))
        self._set_nested(config, ("terminal", "cwd"), str(self._workspace_dir(name)))
        self._normalize_agent_skill_config(config)
        normalize_nine_router_config(
            config,
            str(body["model"]).strip() if "model" in body else None,
        )
        if "soul" in body:
            self._write_text(profile_dir / "SOUL.md", body["soul"])

        self._write_yaml_atomic(config_path, config)
        return self.describe_agent(name)

    @staticmethod
    def _safe_mcp_servers(servers: Mapping[str, Any]) -> dict[str, Any]:
        safe = copy.deepcopy(dict(servers))
        for server in safe.values():
            if not isinstance(server, dict):
                continue
            for field in ("env", "headers"):
                values = server.get(field)
                if not isinstance(values, dict):
                    continue
                server[field] = {
                    str(key): (
                        str(value)
                        if re.fullmatch(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", str(value))
                        else "***"
                    )
                    for key, value in values.items()
                }
        return safe

    def get_mcp(self, raw_name: Any) -> dict[str, Any]:
        profile_dir = self._require_profile(self._agent_name(raw_name))
        config = self._read_config(profile_dir)
        servers = config.get("mcp_servers")
        return {
            "servers": self._safe_mcp_servers(servers)
            if isinstance(servers, Mapping)
            else {},
        }

    def update_mcp(self, raw_name: Any, servers: Mapping[str, Any]) -> dict[str, Any]:
        try:
            from hermes_cli.mcp_security import validate_mcp_server_entry
        except ImportError:  # Source-only tests may not have Hermes on sys.path.
            def validate_mcp_server_entry(name: str, entry: Mapping[str, Any]) -> list[str]:
                issues: list[str] = []
                has_command = bool(str(entry.get("command") or "").strip())
                has_url = bool(str(entry.get("url") or "").strip())
                if has_command == has_url:
                    issues.append(
                        f"Server {name!r} must define exactly one of command or url"
                    )
                if "args" in entry and not isinstance(entry["args"], list):
                    issues.append(f"Server {name!r} args must be a list")
                for field in ("env", "headers", "tools"):
                    if field in entry and not isinstance(entry[field], dict):
                        issues.append(f"Server {name!r} {field} must be an object")
                return issues

        profile_dir = self._require_profile(self._agent_name(raw_name))
        cleaned = copy.deepcopy(dict(servers))
        issues: list[str] = []
        for server_name, server in cleaned.items():
            if not isinstance(server_name, str) or not server_name.strip():
                issues.append("MCP server names must be non-empty strings")
                continue
            if not isinstance(server, dict):
                issues.append(f"Server {server_name!r} must be an object")
                continue
            issues.extend(validate_mcp_server_entry(server_name, server))
        if issues:
            raise AgentAPIError(
                "; ".join(issues),
                code="invalid_mcp_config",
            )

        config = self._read_config(profile_dir)
        if cleaned:
            config["mcp_servers"] = cleaned
        else:
            config.pop("mcp_servers", None)
        self._write_yaml_atomic(profile_dir / "config.yaml", config)
        return self.get_mcp(raw_name)

    def list_skills(self, raw_name: Any) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        disabled = self._disabled_skills(self._read_config(profile_dir))
        skills_root = profile_dir / "skills"
        skills = []
        seen: set[str] = set()
        self._append_skills_from_dir(skills, seen, skills_root, disabled)
        return {
            "object": "hermes.agent_skills",
            "agent": name,
            "skills": sorted(skills, key=lambda item: (item["category"], item["name"])),
        }

    def preview_common_skill_sync(self, raw_names: list[str]) -> dict[str, Any]:
        """Describe how enabled root-profile skills would change each agent."""
        with self._skill_sync_lock:
            common = self._skill_sync_inventory(self.root_profile, exclude_control=True)
            revision = self._skill_sync_revision(common)
            plans = [self._skill_sync_plan(name, common) for name in raw_names]
            return {
                "source_revision": revision,
                "common": {
                    "enabled": sum(item["enabled"] for item in common.values()),
                    "disabled": sum(not item["enabled"] for item in common.values()),
                },
                "agents": plans,
                "totals": self._skill_sync_totals(plans),
            }

    def sync_common_skills(
        self,
        raw_names: list[str],
        expected_source_revision: str,
    ) -> dict[str, Any]:
        """Apply the common skill catalog, atomically for each selected profile."""
        with self._skill_sync_lock:
            common = self._skill_sync_inventory(self.root_profile, exclude_control=True)
            revision = self._skill_sync_revision(common)
            if revision != expected_source_revision:
                raise AgentAPIError(
                    "Common skills changed after the preview. Review the sync again.",
                    code="skills_sync_stale",
                    status=409,
                )
            results: list[dict[str, Any]] = []
            for raw_name in raw_names:
                plan = self._skill_sync_plan(raw_name, common)
                try:
                    self._apply_skill_sync(raw_name, common)
                    results.append({**plan, "status": "completed"})
                except (OSError, AgentAPIError) as exc:
                    results.append({
                        **plan,
                        "status": "failed",
                        "error": str(exc),
                    })
            completed = sum(item["status"] == "completed" for item in results)
            return {
                "source_revision": revision,
                "status": "completed" if completed == len(results) else "partial",
                "agents": results,
                "completed": completed,
                "failed": len(results) - completed,
            }

    def _skill_sync_plan(
        self,
        raw_name: str,
        common: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        target = self._skill_sync_inventory(self._require_profile(name))
        enabled_common = {key: value for key, value in common.items() if value["enabled"]}
        added = sorted(key for key in enabled_common if key not in target)
        updated = sorted(
            key for key, source in enabled_common.items()
            if key in target and (
                not target[key]["enabled"]
                or target[key]["digest"] != source["digest"]
                or target[key]["relative_path"] != source["relative_path"]
            )
        )
        removed = sorted(key for key, source in common.items() if not source["enabled"] and key in target)
        preserved = sorted(key for key in target if key not in common)
        unchanged = sorted(
            key for key, source in enabled_common.items()
            if key in target
            and target[key]["enabled"]
            and target[key]["digest"] == source["digest"]
            and target[key]["relative_path"] == source["relative_path"]
        )
        return {
            "agent_id": name,
            "added": added,
            "updated": updated,
            "removed": removed,
            "preserved": preserved,
            "unchanged": unchanged,
        }

    def _apply_skill_sync(
        self,
        raw_name: str,
        common: dict[str, dict[str, Any]],
    ) -> None:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        target = self._skill_sync_inventory(profile_dir)
        skills_root = profile_dir / "skills"
        temporary = profile_dir / f".skills-sync-{uuid.uuid4().hex}"
        backup = profile_dir / f".skills-backup-{uuid.uuid4().hex}"
        private_disabled: set[str] = set()
        committed = False
        try:
            temporary.mkdir(parents=True)
            for skill_id, item in target.items():
                if skill_id in common:
                    continue
                self._copy_skill_directory(item, temporary)
                if not item["enabled"]:
                    private_disabled.add(skill_id)
            for item in common.values():
                if item["enabled"]:
                    self._copy_skill_directory(item, temporary, overwrite=True)

            had_skills = skills_root.exists()
            if had_skills:
                os.replace(skills_root, backup)
            try:
                os.replace(temporary, skills_root)
                config = self._read_config(profile_dir)
                self._write_disabled_skills(profile_dir, config, private_disabled)
                committed = True
            except Exception:
                if skills_root.exists():
                    shutil.rmtree(skills_root)
                if had_skills and backup.exists():
                    os.replace(backup, skills_root)
                raise
            if committed and backup.exists():
                shutil.rmtree(backup)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
            if committed and backup.exists():
                shutil.rmtree(backup)

    @staticmethod
    def _copy_skill_directory(
        item: Mapping[str, Any],
        destination_root: Path,
        *,
        overwrite: bool = False,
    ) -> None:
        destination = destination_root / str(item["relative_path"])
        if overwrite and destination.exists():
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(Path(str(item["source"])), destination, symlinks=True)

    def _skill_sync_inventory(
        self,
        profile_dir: Path,
        *,
        exclude_control: bool = False,
    ) -> dict[str, dict[str, Any]]:
        root = profile_dir / "skills"
        disabled = self._disabled_skills(self._read_config(profile_dir))
        inventory: dict[str, dict[str, Any]] = {}
        if not root.is_dir():
            return inventory
        for skill_file in sorted(root.rglob("SKILL.md")):
            source = skill_file.parent
            relative = source.relative_to(root)
            if exclude_control and relative.parts[:1] == (BIG_BROTHER_SKILL_CATEGORY,):
                continue
            frontmatter = self._read_skill_frontmatter(skill_file)
            skill_id = str(frontmatter.get("name") or source.name).strip()
            if not skill_id or skill_id in inventory:
                continue
            inventory[skill_id] = {
                "source": source,
                "relative_path": relative.as_posix(),
                "enabled": skill_id not in disabled,
                "digest": self._skill_directory_digest(source),
            }
        return inventory

    @staticmethod
    def _skill_directory_digest(root: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
            relative = path.relative_to(root).as_posix()
            digest.update(relative.encode("utf-8"))
            if path.is_symlink():
                digest.update(b"L")
                digest.update(os.readlink(path).encode("utf-8"))
            elif path.is_file():
                digest.update(b"F")
                digest.update(path.read_bytes())
            elif path.is_dir():
                digest.update(b"D")
        return digest.hexdigest()

    @staticmethod
    def _skill_sync_revision(inventory: Mapping[str, Mapping[str, Any]]) -> str:
        digest = hashlib.sha256()
        for skill_id, item in sorted(inventory.items()):
            digest.update(json.dumps({
                "id": skill_id,
                "relative_path": item["relative_path"],
                "enabled": item["enabled"],
                "digest": item["digest"],
            }, sort_keys=True).encode("utf-8"))
        return digest.hexdigest()

    @staticmethod
    def _skill_sync_totals(plans: list[dict[str, Any]]) -> dict[str, int]:
        return {
            key: sum(len(plan[key]) for plan in plans)
            for key in ("added", "updated", "removed", "preserved", "unchanged")
        }

    async def install_skill(self, raw_name: Any, body: Mapping[str, Any]) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        if not isinstance(body, Mapping):
            raise AgentAPIError("request body must be an object", code="invalid_skill_request")
        target_profile = profile_dir
        enable = bool(body.get("enable", False))
        if "content" in body:
            skill_id = self._skill_id(body.get("skill_id") or body.get("name"))
            content = self._text_value(body["content"], field="content", max_chars=MAX_TEXT_CHARS)
            category = self._safe_category(body.get("category") or CUSTOM_SKILL_CATEGORY)
            skill_dir = target_profile / "skills" / category / skill_id
            skill_dir.mkdir(parents=True, exist_ok=True)
            self._write_text(skill_dir / "SKILL.md", content)
            self._set_skill_enabled(profile_dir, skill_id, enable)
            payload = self.list_skills(name)
            return payload

        before = self._skill_files_for_profile(profile_dir)
        source = self._nonempty_string(body.get("source"), "source")
        command = [self._hermes_binary(), "skills", "install", source, "--yes"]
        if body.get("name"):
            command.extend(["--name", self._skill_id(body["name"])])
        command.extend([
            "--category",
            self._safe_category(body.get("category") or CUSTOM_SKILL_CATEGORY),
        ])
        if bool(body.get("force", False)):
            command.append("--force")
        result = await self._run_hermes_command(
            target_profile,
            self._workspace_dir(name),
            command,
            timeout_seconds=int(body.get("timeout_seconds") or 180),
        )
        if result["exit_code"] != 0:
            raise AgentAPIError(
                "skill install failed",
                code="skill_install_failed",
                status=422,
            )
        changed_skill_ids = self._changed_skill_ids(profile_dir, before)
        if body.get("name"):
            changed_skill_ids.add(self._skill_id(body["name"]))
        for skill_id in changed_skill_ids:
            self._set_skill_enabled(profile_dir, skill_id, enable)
        payload = self.list_skills(name)
        payload["command"] = result
        return payload

    def set_skill_enabled(
        self,
        raw_name: Any,
        raw_skill_id: Any,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        if not isinstance(body, Mapping):
            raise AgentAPIError("request body must be an object", code="invalid_skill_request")
        skill_id = self._skill_id(raw_skill_id)
        if self._find_agent_skill(profile_dir, skill_id) is None:
            raise AgentAPIError(
                f"Skill not found: {skill_id}", code="skill_not_found", status=404
            )
        if "enabled" not in body:
            raise AgentAPIError("enabled is required", code="invalid_skill_request")
        self._set_skill_enabled(profile_dir, skill_id, self._coerce_bool(body["enabled"]))
        return self.list_skills(name)

    def remove_skill(self, raw_name: Any, raw_skill_id: Any) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        skill_id = self._skill_id(raw_skill_id)
        skills_root = profile_dir / "skills"
        removed = False
        if skills_root.is_dir():
            for skill_file in skills_root.rglob("SKILL.md"):
                frontmatter = self._read_skill_frontmatter(skill_file)
                candidates = {skill_file.parent.name, str(frontmatter.get("name") or "").strip()}
                if skill_id in candidates:
                    shutil.rmtree(skill_file.parent)
                    removed = True
                    break
        if not removed:
            raise AgentAPIError(
                f"Skill not found: {skill_id}", code="skill_not_found", status=404
            )
        self._set_skill_enabled(profile_dir, skill_id, True)
        return self.list_skills(name)

    async def chat(self, raw_name: Any, body: Mapping[str, Any]) -> dict[str, Any]:
        prepared = self._prepare_chat_command(raw_name, body, require_conversation=False)
        if prepared["model"] == NINE_ROUTER_DEFAULT_MODEL:
            await self.nine_router.ensure_auto_combo()
        name = prepared["name"]
        profile_dir = prepared["profile_dir"]
        before = self._latest_session_ids(profile_dir)

        started = time.time()
        result = await self._run_profile_command(
            name,
            prepared["command"],
            engine=str(prepared.get("engine") or "hermes"),
            timeout_seconds=prepared["timeout_seconds"],
        )
        provider_error = self._provider_error(result["stdout"])
        if result["exit_code"] != 0 or provider_error:
            raise AgentAPIError(
                provider_error or result["stderr"].strip() or "agent command failed",
                code="provider_request_failed",
                status=502,
            )
        after = self._latest_session_ids(profile_dir)
        session_id = self._detect_changed_session(before, after)
        session = self._session(profile_dir, session_id) if session_id else None
        return {
            "object": "hermes.agent_chat",
            "agent": name,
            "conversation_id": session_id,
            "session": session,
            "response": result["stdout"].strip(),
            "stderr": result["stderr"].strip(),
            "exit_code": result["exit_code"],
            "duration_seconds": round(time.time() - started, 3),
            "provider": prepared.get("provider") or "",
            "engine": prepared.get("engine") or "hermes",
            "workspace_path": str(self._workspace_dir(name)),
            "profile_path": str(profile_dir),
        }

    def chat_stream(self, raw_name: Any, body: Mapping[str, Any]):
        prepared = self._prepare_chat_command(raw_name, body, require_conversation=True)
        return self._chat_stream_events(prepared)

    def _prepare_chat_command(
        self,
        raw_name: Any,
        body: Mapping[str, Any],
        *,
        require_conversation: bool,
    ) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        self._ensure_router_profile(profile_dir, body.get("model"))
        message = self._text_value(body.get("message"), field="message", max_chars=MAX_TEXT_CHARS)
        conversation_id = ""
        if body.get("conversation_id"):
            conversation_id = self._session_id(body["conversation_id"])
        if require_conversation and not conversation_id:
            raise AgentAPIError(
                "conversation is required",
                code="conversation_required",
            )
        if require_conversation and self._session(profile_dir, conversation_id) is None:
            raise AgentAPIError(
                f"Conversation not found: {conversation_id}",
                code="conversation_not_found",
                status=404,
            )
        if conversation_id:
            self._override_stored_runtime_help_guidance(profile_dir, conversation_id)

        provider = self._conversation_provider(profile_dir, body)
        model = self._conversation_model(profile_dir, body)
        engine = "hermes"
        command = [self._hermes_binary()]
        if conversation_id:
            command.extend(["--resume", conversation_id])
        if body.get("model"):
            command.extend([
                "--model",
                route_nine_router_model(
                    self._nonempty_string(body["model"], "model")
                ),
            ])
        skills = body.get("skills")
        if skills is None:
            disabled = self._disabled_skills(self._read_config(profile_dir))
            skills = [
                item["skill_id"]
                for item in self.list_skills(name)["skills"]
                if item["skill_id"] not in disabled
            ] if disabled else []
        normalized_skills = self._normalize_skill_list(skills) if skills else []
        if normalized_skills:
            command.extend(["--skills", ",".join(normalized_skills)])
        toolsets = body.get("toolsets")
        if toolsets:
            normalized_toolsets = self._normalize_skill_list(toolsets)
            command.extend(["--toolsets", ",".join(normalized_toolsets)])
        if bool(body.get("yolo", False)):
            command.append("--yolo")
        if require_conversation:
            # Hermes' one-shot mode deliberately bypasses the CLI session
            # loader, even when --resume is present. The quiet query path
            # restores the selected session's SQLite transcript before the
            # turn and persists the new messages back to that same session.
            command.extend(["chat", "--quiet", "--query", message])
        else:
            command.extend(["-z", message])

        timeout_seconds = int(body.get("timeout_seconds") or DEFAULT_CHAT_TIMEOUT_SECONDS)
        timeout_seconds = max(1, min(timeout_seconds, MAX_CHAT_TIMEOUT_SECONDS))
        return {
            "name": name,
            "profile_dir": profile_dir,
            "workspace_dir": self._workspace_dir(name),
            "conversation_id": conversation_id,
            "message": message,
            "provider": provider,
            "model": model,
            "requested_model": (
                self._nonempty_string(body["model"], "model")
                if body.get("model")
                else ""
            ),
            "engine": engine,
            "command": command,
            "timeout_seconds": timeout_seconds,
        }

    async def _run_session_agent(
        self,
        prepared: Mapping[str, Any],
        *,
        run_id: str,
        stream_delta_callback,
        tool_progress_callback,
        approval_notify_callback,
        agent_ref: list[Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Run one turn through Hermes' native session and approval machinery."""
        from gateway.config import PlatformConfig
        from gateway.platforms.api_server import (
            APIServerAdapter,
            _api_request_profile,
        )
        from gateway.run import _profile_runtime_scope
        from tools.approval import register_gateway_notify, unregister_gateway_notify

        profile_dir = Path(prepared["profile_dir"])
        conversation_id = str(prepared["conversation_id"])
        manager = self

        class RunScopedAPIServerAdapter(APIServerAdapter):
            @staticmethod
            def _bind_api_server_session(
                *,
                chat_id: str = "",
                session_key: str = "",
                session_id: str = "",
            ) -> list:
                # Keep the conversation ID as the stable memory scope passed to
                # _create_agent(), while matching /v1/runs' isolated approval
                # namespace in the executor thread.
                return APIServerAdapter._bind_api_server_session(
                    chat_id=chat_id,
                    session_key=run_id,
                    session_id=session_id,
                )

            def _create_agent(self, *args: Any, **kwargs: Any) -> Any:
                agent = super()._create_agent(*args, **kwargs)
                manager._apply_runtime_help_guidance_override(agent, profile_dir)
                # Hermes exposes structured model thinking through its
                # reasoning callback. The stock API-server adapter does not
                # register one and instead reports assistant content through
                # tool_progress as `reasoning.available`, which duplicates the
                # visible answer. Bridge the real callback into our SSE stream.
                agent.reasoning_callback = lambda text: tool_progress_callback(
                    "reasoning.delta",
                    "_thinking",
                    str(text or ""),
                    None,
                )
                run_conversation = agent.run_conversation

                def run_with_memory_approval(*run_args: Any, **run_kwargs: Any) -> Any:
                    from tools import terminal_tool
                    from tools.approval import _await_gateway_decision

                    previous_callback = terminal_tool._get_approval_callback()

                    def approve_memory(
                        command: str,
                        description: str,
                        **_approval_kwargs: Any,
                    ) -> str:
                        decision = _await_gateway_decision(
                            run_id,
                            approval_notify_callback,
                            {
                                "command": command,
                                "description": description,
                                "pattern_key": "memory.write_approval",
                                "pattern_keys": ["memory.write_approval"],
                                "subsystem": "memory",
                                "allow_permanent": True,
                                "allow_session": False,
                            },
                            surface="api_server",
                        )
                        choice = str(decision.get("choice") or "deny")
                        return "once" if choice == "always" else choice

                    terminal_tool.set_approval_callback(approve_memory)
                    try:
                        return run_conversation(*run_args, **run_kwargs)
                    finally:
                        terminal_tool.set_approval_callback(previous_callback)

                agent.run_conversation = run_with_memory_approval
                return agent

        adapter = RunScopedAPIServerAdapter(PlatformConfig(enabled=True))
        session_db = self._session_db(profile_dir)
        adapter._session_db = session_db
        # Brain4All may also expose legacy agent directories. Pin the native
        # adapter to the already validated profile path instead of resolving
        # the profile name a second time.
        adapter._profile_scope = lambda _profile: _profile_runtime_scope(profile_dir)
        profile_token = _api_request_profile.set(str(prepared["name"]))
        register_gateway_notify(run_id, approval_notify_callback)
        try:
            history = await adapter._conversation_history_for_session(conversation_id)
            session = self._session(profile_dir, conversation_id) or {}
            selected_model = str(
                prepared.get("requested_model")
                or session.get("model")
                or prepared.get("model")
                or ""
            ).strip()
            result, usage = await adapter._run_agent(
                user_message=str(prepared["message"]),
                conversation_history=history,
                session_id=conversation_id,
                stream_delta_callback=stream_delta_callback,
                tool_progress_callback=tool_progress_callback,
                agent_ref=agent_ref,
                gateway_session_key=conversation_id,
                route={"model": selected_model} if selected_model else None,
            )
            agent = agent_ref[0]
            compressor = getattr(agent, "context_compressor", None) if agent is not None else None
            context_used = int(getattr(compressor, "last_prompt_tokens", 0) or 0)
            context_limit = int(getattr(compressor, "context_length", 0) or 0)
            actual_model = str(
                (result.get("model") if isinstance(result, Mapping) else "")
                or getattr(agent, "model", "")
                or prepared.get("model")
                or ""
            ).strip()
            # An auto/blend route can choose models with different windows.
            # Do not report Hermes' generic fallback as a model-specific limit.
            if actual_model.lower() in {"", "auto", NINE_ROUTER_DEFAULT_MODEL.lower()}:
                context_limit = 0
            context = {
                "used": max(0, context_used),
                "limit": max(0, context_limit),
                "model": actual_model,
            }
            usage.update({
                "context_used": context["used"],
                "context_limit": context["limit"],
            })
            self._persist_conversation_context(profile_dir, conversation_id, context)
            return result, usage
        finally:
            try:
                unregister_gateway_notify(run_id)
            finally:
                _api_request_profile.reset(profile_token)
                close = getattr(session_db, "close", None)
                if callable(close):
                    close()

    async def _chat_stream_events(self, prepared: Mapping[str, Any]):
        timeout_seconds = int(prepared["timeout_seconds"])
        conversation_id = str(prepared.get("conversation_id") or "")
        model = str(prepared.get("model") or "")
        profile_dir = Path(prepared["profile_dir"])
        skill_ids_before_run = self._skill_ids_for_profile(profile_dir)
        created = int(time.time())
        chat_id = "chatcmpl-" + (conversation_id or uuid.uuid4().hex)
        run_id = "run_" + uuid.uuid4().hex

        if model == NINE_ROUTER_DEFAULT_MODEL:
            try:
                await self.nine_router.ensure_auto_combo()
            except NineRouterAPIError as exc:
                yield self._chat_sse_error(str(exc))
                yield self._chat_sse_done(chat_id, created, model, conversation_id)
                return

        title_task: asyncio.Task[str] | None = None
        if self._conversation_has_default_title(profile_dir, conversation_id):
            # Run the tiny title request beside the chat so it adds no serial
            # model wait to the normal completion path.
            title_task = asyncio.create_task(
                self._summarize_conversation_title(prepared.get("message"), model)
            )

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        state: dict[str, Any] = {
            "agent_ref": None,
            "stop_requested": False,
            "approval_session": run_id,
            "event_loop": loop,
            "event_queue": queue,
        }

        class AgentRef(list):
            def __setitem__(self, index, value):
                super().__setitem__(index, value)
                if state["stop_requested"] and value is not None:
                    value.interrupt("run stopped by user")

        agent_ref: list[Any] = AgentRef([None])
        state["agent_ref"] = agent_ref
        output_chunks: list[str] = []

        def on_delta(delta: str | None) -> None:
            if not delta:
                return
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None
            if running_loop is loop:
                queue.put_nowait(("delta", delta))
            else:
                loop.call_soon_threadsafe(queue.put_nowait, ("delta", delta))

        def enqueue_event(event: Mapping[str, Any]) -> None:
            payload = dict(event)
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None
            if running_loop is loop:
                queue.put_nowait(("event", payload))
            else:
                loop.call_soon_threadsafe(queue.put_nowait, ("event", payload))

        def on_tool_progress(
            event_type: str,
            tool_name: str | None = None,
            preview: str | None = None,
            args: Any = None,
            **kwargs: Any,
        ) -> None:
            del args
            timestamp = time.time()
            if event_type == "tool.started":
                enqueue_event({
                    "event": "tool.started",
                    "run_id": run_id,
                    "timestamp": timestamp,
                    "tool": tool_name or "tool",
                    "preview": preview or "",
                })
            elif event_type == "tool.completed":
                write_status = resolve_staged_write(
                    tool_name or "tool",
                    kwargs.get("result"),
                )
                enqueue_event({
                    "event": "tool.completed",
                    "run_id": run_id,
                    "timestamp": timestamp,
                    "tool": tool_name or "tool",
                    "duration": round(float(kwargs.get("duration") or 0), 3),
                    "error": bool(kwargs.get("is_error", False))
                    or write_status in {"rejected", "failed"},
                })
            elif event_type == "tool.failed":
                enqueue_event({
                    "event": "tool.failed",
                    "run_id": run_id,
                    "timestamp": timestamp,
                    "tool": tool_name or "tool",
                    "duration": round(float(kwargs.get("duration") or 0), 3),
                    "error": True,
                })
            elif event_type == "reasoning.delta":
                enqueue_event({
                    "event": "reasoning.delta",
                    "run_id": run_id,
                    "timestamp": timestamp,
                    "delta": preview or "",
                })

        def on_approval(approval_data: Mapping[str, Any]) -> None:
            from gateway.platforms.api_server import _approval_event_choices
            from gateway.run import _redact_approval_command

            smart_denied = bool(approval_data.get("smart_denied"))
            allow_permanent = approval_data.get("allow_permanent") is not False
            event = {
                "event": "approval.request",
                "run_id": run_id,
                "timestamp": time.time(),
                "command": _redact_approval_command(approval_data.get("command")),
                "description": str(approval_data.get("description") or "Approval required"),
                "pattern_keys": list(approval_data.get("pattern_keys") or []),
                "smart_denied": smart_denied,
                "allow_permanent": allow_permanent,
                "choices": _approval_event_choices(
                    smart_denied=smart_denied,
                    allow_permanent=allow_permanent,
                ),
            }
            subsystem = str(approval_data.get("subsystem") or "")
            pending_id = str(approval_data.get("pending_id") or "")
            if subsystem in {"memory", "skills"}:
                event["subsystem"] = subsystem
            if pending_id:
                event["pending_id"] = pending_id
            enqueue_event(event)

        def resolve_staged_write(tool_name: str, raw_result: Any) -> str:
            """Turn Hermes' staged write result into a run-scoped approval."""
            if isinstance(raw_result, Mapping):
                result = dict(raw_result)
            elif isinstance(raw_result, str):
                try:
                    decoded = json.loads(raw_result)
                except (TypeError, ValueError):
                    return ""
                result = dict(decoded) if isinstance(decoded, Mapping) else {}
            else:
                return ""
            pending_id = str(result.get("pending_id") or "")
            if not result.get("staged") or not pending_id:
                return ""

            normalized_tool = tool_name.lower()
            if normalized_tool == "memory" or normalized_tool.endswith("__memory"):
                subsystem = "memory"
            elif "skill_manage" in normalized_tool:
                subsystem = "skills"
            else:
                return ""

            from tools import write_approval
            from tools.approval import _await_gateway_decision

            record = write_approval.get_pending(subsystem, pending_id)
            if record is None:
                enqueue_event({
                    "event": "write.failed",
                    "run_id": run_id,
                    "timestamp": time.time(),
                    "tool": tool_name,
                    "subsystem": subsystem,
                    "pending_id": pending_id,
                    "status": "missing",
                })
                return "failed"

            summary = str(record.get("summary") or f"Pending {subsystem} write")
            decision = _await_gateway_decision(
                run_id,
                on_approval,
                {
                    "command": summary,
                    "description": (
                        "Memory write requires approval"
                        if subsystem == "memory"
                        else "Skill write requires approval"
                    ),
                    "pattern_key": f"{subsystem}.write_approval",
                    "pattern_keys": [f"{subsystem}.write_approval"],
                    "subsystem": subsystem,
                    "pending_id": pending_id,
                    "allow_permanent": True,
                    "allow_session": False,
                },
                surface="api_server",
            )
            choice = str(decision.get("choice") or "")
            if not decision.get("resolved") or not choice:
                status = "pending"
            elif choice == "deny":
                write_approval.discard_pending(subsystem, pending_id)
                status = "rejected"
            else:
                try:
                    if subsystem == "memory":
                        from tools.memory_tool import apply_memory_pending, load_on_disk_store

                        applied = apply_memory_pending(
                            dict(record.get("payload") or {}),
                            load_on_disk_store(),
                        )
                        success = bool(applied.get("success"))
                    else:
                        from tools.skill_manager_tool import apply_skill_pending

                        applied = json.loads(
                            apply_skill_pending(dict(record.get("payload") or {}))
                        )
                        success = bool(applied.get("success"))
                except Exception:
                    success = False
                if success:
                    write_approval.discard_pending(subsystem, pending_id)
                    status = "applied"
                else:
                    status = "failed"

            enqueue_event({
                "event": f"write.{status}",
                "run_id": run_id,
                "timestamp": time.time(),
                "tool": tool_name,
                "subsystem": subsystem,
                "pending_id": pending_id,
                "status": status,
            })
            return status

        async def run_agent() -> None:
            try:
                result, usage = await self._run_session_agent(
                    prepared,
                    run_id=run_id,
                    stream_delta_callback=on_delta,
                    tool_progress_callback=on_tool_progress,
                    approval_notify_callback=on_approval,
                    agent_ref=agent_ref,
                )
                # Skills created or downloaded during chat are opt-in for the
                # next turn, regardless of which Hermes install path created them.
                self._disable_new_skills(profile_dir, skill_ids_before_run)
                # Flush callbacks already scheduled from the worker thread
                # before placing the terminal event behind them.
                await asyncio.sleep(0)
                await queue.put(("completed", (result, usage)))
            except Exception as exc:
                try:
                    self._disable_new_skills(profile_dir, skill_ids_before_run)
                except Exception:
                    pass
                await queue.put(("failed", exc))

        task = asyncio.create_task(run_agent())
        state["task"] = task
        self._active_runs[run_id] = state
        yield self._sse_data({
            "event": "run.started", "run_id": run_id,
            "session_id": conversation_id, "status": "started",
            "timestamp": time.time(), "model": model,
        })
        deadline = time.monotonic() + timeout_seconds
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                try:
                    kind, payload = await asyncio.wait_for(
                        queue.get(),
                        timeout=min(15.0, remaining),
                    )
                except asyncio.TimeoutError:
                    if time.monotonic() < deadline:
                        yield b": keepalive\n\n"
                        continue
                    raise
                if kind == "delta":
                    text = str(payload)
                    output_chunks.append(text)
                    yield self._sse_data({
                        "event": "message.delta",
                        "run_id": run_id,
                        "timestamp": time.time(),
                        "delta": text,
                    })
                    continue
                if kind == "event":
                    yield self._sse_data(payload)
                    continue
                if kind == "failed":
                    raise payload

                result, usage = payload
                output = str(result.get("final_response") or "")
                if not output_chunks and output:
                    # Some non-streaming-compatible providers can only return
                    # a final response. Preserve a usable fallback for them.
                    output_chunks.append(output)
                    yield self._sse_data({
                        "event": "message.delta",
                        "run_id": run_id,
                        "timestamp": time.time(),
                        "delta": output,
                    })
                if state["stop_requested"] or bool(result.get("interrupted")):
                    yield self._sse_data({
                        "event": "run.cancelled",
                        "run_id": run_id,
                        "timestamp": time.time(),
                    })
                elif result.get("failed") and not output:
                    yield self._sse_data({
                        "event": "run.failed",
                        "run_id": run_id,
                        "timestamp": time.time(),
                        "message": str(result.get("error") or "agent command failed"),
                    })
                else:
                    suggested_title = await title_task if title_task is not None else None
                    conversation_title = self._auto_title_conversation(
                        profile_dir,
                        conversation_id,
                        prepared.get("message"),
                        suggested_title=suggested_title,
                    )
                    yield self._sse_data({
                        "event": "run.completed",
                        "run_id": run_id,
                        "timestamp": time.time(),
                        "output": output,
                        "usage": {
                            "input_tokens": int(usage.get("input_tokens") or 0),
                            "output_tokens": int(usage.get("output_tokens") or 0),
                            "total_tokens": int(usage.get("total_tokens") or 0),
                        },
                        "conversation_title": conversation_title or "",
                    })
                yield b"data: [DONE]\n\n"
                return
        except asyncio.CancelledError:
            self._stopped_runs.add(run_id)
            state["stop_requested"] = True
            agent = agent_ref[0]
            if agent is not None:
                agent.interrupt("client disconnected")
            try:
                from tools.approval import unregister_gateway_notify

                unregister_gateway_notify(run_id)
            except ImportError:
                pass
            if not task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=5)
                except asyncio.TimeoutError:
                    pass
            raise
        except asyncio.TimeoutError:
            state["stop_requested"] = True
            agent = agent_ref[0]
            if agent is not None:
                agent.interrupt("agent command timed out")
            try:
                from tools.approval import unregister_gateway_notify

                unregister_gateway_notify(run_id)
            except ImportError:
                pass
            yield self._sse_data({
                "event": "run.failed",
                "run_id": run_id,
                "timestamp": time.time(),
                "message": "agent command timed out",
            })
            yield b"data: [DONE]\n\n"
        except Exception as exc:
            yield self._sse_data({
                "event": "run.failed",
                "run_id": run_id,
                "timestamp": time.time(),
                "message": str(exc) or "agent command failed",
            })
            yield b"data: [DONE]\n\n"
        finally:
            if title_task is not None and not title_task.done():
                title_task.cancel()
            self._active_runs.pop(run_id, None)
            self._stopped_runs.discard(run_id)

    async def stop_run(self, run_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"run_[0-9a-f]{32}", str(run_id)):
            raise AgentAPIError("invalid run id", code="invalid_run")
        state = self._active_runs.get(run_id)
        if state is None or state["task"].done():
            raise AgentAPIError("run not found", code="run_not_found", status=404)
        self._stopped_runs.add(run_id)
        state["stop_requested"] = True
        agent = state["agent_ref"][0]
        if agent is not None:
            await asyncio.to_thread(agent.interrupt, "run stopped by user")
        try:
            from tools.approval import unregister_gateway_notify

            unregister_gateway_notify(run_id)
        except ImportError:
            pass
        return {"run_id": run_id, "stopped": True, "status": "stopping"}

    def resolve_approval(self, run_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        choice = str(body.get("choice") or "").strip().lower()
        if choice not in {"once", "session", "always", "deny"}:
            raise AgentAPIError("invalid approval choice", code="invalid_approval_choice")
        try:
            from tools.approval import resolve_gateway_approval
            state = self._active_runs.get(run_id)
            approval_session = (
                str(state.get("approval_session") or run_id)
                if state is not None
                else run_id
            )
            resolved = resolve_gateway_approval(
                approval_session,
                choice,
                bool(body.get("resolve_all", False)),
            )
        except ImportError as error:
            raise AgentAPIError("Hermes approval core is unavailable", code="approval_unavailable", status=503) from error
        if resolved == 0:
            raise AgentAPIError("run has no pending approval", code="approval_not_pending", status=409)
        if state is not None:
            event_loop = state.get("event_loop")
            event_queue = state.get("event_queue")
            if event_loop is not None and event_queue is not None:
                event_loop.call_soon_threadsafe(
                    event_queue.put_nowait,
                    (
                        "event",
                        {
                            "event": "approval.responded",
                            "run_id": run_id,
                            "timestamp": time.time(),
                            "choice": choice,
                            "resolved": resolved,
                        },
                    ),
                )
        return {"run_id": run_id, "choice": choice, "resolved": resolved}

    def _chat_sse_chunk(
        self,
        chat_id: str,
        created: int,
        model: str,
        conversation_id: str,
        content: str,
        *,
        include_role: bool,
    ) -> bytes:
        delta: dict[str, Any] = {}
        if include_role:
            delta["role"] = "assistant"
        if content:
            delta["content"] = content
        return self._sse_data(
            {
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "conversation_id": conversation_id,
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            }
        )

    def _chat_sse_done(self, chat_id: str, created: int, model: str, conversation_id: str) -> bytes:
        return self._sse_data(
            {
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "conversation_id": conversation_id,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
        ) + b"data: [DONE]\n\n"

    def _chat_sse_error(self, message: str) -> bytes:
        payload = json.dumps({"error": {"message": message}}, separators=(",", ":"))
        return f"event: error\ndata: {payload}\n\n".encode("utf-8")

    @staticmethod
    def _provider_error(output: str) -> str:
        """Recognize the structured provider failure Hermes may print with exit 0."""
        normalized = str(output or "").strip()
        return normalized if PROVIDER_ERROR_OUTPUT_RE.match(normalized) else ""

    def _sse_data(self, payload: Mapping[str, Any]) -> bytes:
        raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
        return f"data: {raw}\n\n".encode("utf-8")

    @staticmethod
    def _upstream_runtime_help_guidance() -> str:
        try:
            from agent.prompt_builder import HERMES_AGENT_HELP_GUIDANCE
        except (ImportError, AttributeError):
            return ""
        return str(HERMES_AGENT_HELP_GUIDANCE or "").strip()

    def _replace_runtime_help_guidance(self, prompt: Any) -> str:
        rendered = str(prompt or "")
        upstream = self._upstream_runtime_help_guidance()
        if not upstream or upstream not in rendered:
            return rendered
        return rendered.replace(upstream, MANAGED_RUNTIME_HELP_GUIDANCE)

    def _apply_runtime_help_guidance_override(self, agent: Any, profile_dir: Path) -> None:
        """Replace Hermes' built-in product identity while preserving its prompt."""
        original_build = getattr(agent, "_build_system_prompt", None)
        if callable(original_build):
            def build_system_prompt(system_message: Any = None) -> str:
                return self._replace_runtime_help_guidance(
                    original_build(system_message),
                )

            agent._build_system_prompt = build_system_prompt
        for attribute in ("_cached_system_prompt", "_cached_system_prompt_static"):
            cached = getattr(agent, attribute, None)
            if isinstance(cached, str) and cached:
                setattr(
                    agent,
                    attribute,
                    self._replace_runtime_help_guidance(cached),
                )

    def _override_stored_runtime_help_guidance(
        self,
        profile_dir: Path,
        session_id: str,
    ) -> None:
        """Migrate a resumed session whose cached prompt contains upstream branding."""
        db_path = profile_dir / "state.db"
        upstream = self._upstream_runtime_help_guidance()
        if not db_path.is_file() or not upstream:
            return
        conn = sqlite3.connect(db_path, timeout=1.0)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT system_prompt FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            stored = str(row["system_prompt"] or "") if row else ""
            replacement = self._replace_runtime_help_guidance(stored)
            if row and replacement != stored:
                conn.execute(
                    "UPDATE sessions SET system_prompt = ? WHERE id = ?",
                    (replacement, session_id),
                )
                conn.commit()
        except sqlite3.Error:
            pass
        finally:
            conn.close()

    def list_conversations(self, raw_name: Any, body: Mapping[str, Any] | None = None) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        body = body or {}
        page = max(1, int(body.get("page") or 1))
        limit = max(1, min(int(body.get("limit") or 50), 1000))
        rows = self._sessions(profile_dir, limit=limit + 1, offset=(page - 1) * limit)
        return {
            "object": "hermes.agent_conversations",
            "agent": name,
            "conversations": rows[:limit],
            "pagination": {"page": page, "limit": limit, "has_more": len(rows) > limit},
        }

    def create_conversation(self, raw_name: Any, body: Mapping[str, Any]) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        if not isinstance(body, Mapping):
            raise AgentAPIError("request body must be an object", code="invalid_conversation_request")
        session_id = self._session_id(
            body.get("id")
            or body.get("session_id")
            or body.get("conversation_id")
            or _new_conversation_id()
        )
        title = self._conversation_title(body)
        model = self._conversation_model(profile_dir, body)
        default_title = title is None or title.casefold() == DEFAULT_CONVERSATION_TITLE.casefold()
        with self._conversation_lock:
            if self._session(profile_dir, session_id) is not None:
                raise AgentAPIError(
                    "conversation already exists",
                    code="conversation_exists",
                    status=409,
                )
            for _attempt in range(100):
                if default_title:
                    title = self._next_default_conversation_title(profile_dir)
                try:
                    self._create_session(profile_dir, session_id, model=model, title=title)
                    break
                except AgentAPIError as exc:
                    # Another process can claim the next numbered title between
                    # our read and insert. Re-read and continue the sequence;
                    # the insert is atomic, so no empty session is left behind.
                    if default_title and exc.code == "conversation_name_exists":
                        continue
                    raise
            else:
                raise AgentAPIError(
                    "could not allocate a conversation name",
                    code="conversation_name_conflict",
                    status=409,
                )
        return self.get_conversation(name, session_id)

    def update_conversation(
        self,
        raw_name: Any,
        raw_session_id: Any,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        if not isinstance(body, Mapping):
            raise AgentAPIError("request body must be an object", code="invalid_conversation_request")
        session_id = self._session_id(raw_session_id)
        if self._session(profile_dir, session_id) is None:
            raise AgentAPIError(
                f"Conversation not found: {session_id}",
                code="conversation_not_found",
                status=404,
            )
        title = self._conversation_title(body)
        if title is None:
            raise AgentAPIError("name is required", code="invalid_conversation_request")
        self._update_session_title(profile_dir, session_id, title)
        return self.get_conversation(name, session_id)

    def delete_conversation(self, raw_name: Any, raw_session_id: Any) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        session_id = self._session_id(raw_session_id)
        if self._session(profile_dir, session_id) is None:
            raise AgentAPIError(
                f"Conversation not found: {session_id}",
                code="conversation_not_found",
                status=404,
            )
        self._delete_session(profile_dir, session_id)
        return {
            "object": "hermes.agent_conversation_delete",
            "agent": name,
            "id": session_id,
            "deleted": True,
        }

    def get_conversation(self, raw_name: Any, raw_session_id: Any) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        session_id = self._session_id(raw_session_id)
        session = self._session(profile_dir, session_id)
        if session is None:
            raise AgentAPIError(
                f"Conversation not found: {session_id}",
                code="conversation_not_found",
                status=404,
            )
        return {
            "object": "hermes.agent_conversation",
            "agent": name,
            "conversation": session,
            "messages": self._messages(profile_dir, session_id),
        }

    def list_workspace(self, raw_name: Any, body: Mapping[str, Any] | None = None) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        self._require_profile(name)
        body = body or {}
        directory = self._workspace_path(name, body.get("path") or ".", require_file=False)
        if not directory.is_dir():
            raise AgentAPIError("path is not a directory", code="invalid_workspace_path")
        entries = []
        for child in sorted(directory.iterdir(), key=lambda item: item.name):
            stat = child.stat()
            entries.append(
                {
                    "name": child.name,
                    "path": str(child.relative_to(self._workspace_dir(name))),
                    "type": "directory" if child.is_dir() else "file",
                    "size_bytes": stat.st_size,
                    "updated_at": stat.st_mtime,
                }
            )
        return {
            "object": "hermes.agent_workspace",
            "agent": name,
            "path": str(directory.relative_to(self._workspace_dir(name))),
            "entries": entries,
        }

    def read_workspace_file(self, raw_name: Any, body: Mapping[str, Any]) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        self._require_profile(name)
        file_path = self._workspace_path(name, body.get("path"), require_file=True)
        if not file_path.is_file():
            raise AgentAPIError("path is not a file", code="invalid_workspace_path")
        size = file_path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise AgentAPIError(
                f"file is too large (max {MAX_FILE_BYTES} bytes)",
                code="workspace_file_too_large",
                status=413,
            )
        content = file_path.read_bytes()
        return {
            "object": "hermes.agent_workspace_file",
            "agent": name,
            "path": str(file_path.relative_to(self._workspace_dir(name))),
            "size_bytes": len(content),
            "content_base64": base64.b64encode(content).decode("ascii"),
        }

    def write_workspace_file(self, raw_name: Any, body: Mapping[str, Any]) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        self._require_profile(name)
        file_path = self._workspace_path(name, body.get("path"), require_file=True)
        if "content_base64" in body:
            try:
                content = base64.b64decode(str(body["content_base64"]), validate=True)
            except Exception as exc:
                raise AgentAPIError("content_base64 is invalid", code="invalid_content") from exc
        else:
            content = self._text_value(body.get("content"), field="content", max_chars=MAX_TEXT_CHARS).encode("utf-8")
        if len(content) > MAX_FILE_BYTES:
            raise AgentAPIError(
                f"content is too large (max {MAX_FILE_BYTES} bytes)",
                code="workspace_file_too_large",
                status=413,
            )
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(content)
        return {
            "object": "hermes.agent_workspace_file",
            "agent": name,
            "path": str(file_path.relative_to(self._workspace_dir(name))),
            "size_bytes": len(content),
        }

    def delete_workspace_path(self, raw_name: Any, body: Mapping[str, Any]) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        self._require_profile(name)
        target = self._workspace_path(name, body.get("path"), require_file=False)
        if target == self._workspace_dir(name):
            raise AgentAPIError("cannot delete workspace root", code="invalid_workspace_path")
        if not target.exists():
            raise AgentAPIError("path not found", code="workspace_path_not_found", status=404)
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return {"object": "hermes.agent_workspace_delete", "agent": name, "deleted": True}

    def read_memory(self, raw_name: Any) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        mem_dir = profile_dir / "memories"
        return {
            "object": "hermes.agent_memory",
            "agent": name,
            "memory": self._read_text(mem_dir / "MEMORY.md"),
            "user": self._read_text(mem_dir / "USER.md"),
        }

    def write_memory(self, raw_name: Any, body: Mapping[str, Any]) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        mem_dir = profile_dir / "memories"
        mem_dir.mkdir(parents=True, exist_ok=True)
        if "memory" in body:
            self._write_text(mem_dir / "MEMORY.md", body["memory"])
        if "user" in body:
            self._write_text(mem_dir / "USER.md", body["user"])
        return self.read_memory(name)

    def _agent_name(self, value: Any) -> str:
        name = str(value or "").strip()
        if name.casefold() in {
            BIG_BROTHER_AGENT_ID,
            "big brother",
            "default",
        }:
            return BIG_BROTHER_AGENT_ID
        if not AGENT_NAME_RE.match(name) or ".." in name:
            raise AgentAPIError("agent name is invalid", code="invalid_agent_name")
        return name

    def _new_agent_name(self) -> str:
        for _ in range(64):
            name = secrets.choice(GENERATED_AGENT_ID_FIRST_ALPHABET) + "".join(
                secrets.choice(GENERATED_AGENT_ID_ALPHABET)
                for _ in range(GENERATED_AGENT_ID_LENGTH - 1)
            )
            if GENERATED_AGENT_NAME_RE.fullmatch(name) and self._existing_profile_dir(name) is None:
                return name
        raise AgentAPIError(
            "failed to allocate agent name",
            code="agent_name_unavailable",
            status=409,
        )

    def _skill_id(self, value: Any) -> str:
        skill_id = str(value or "").strip()
        if not SKILL_ID_RE.match(skill_id) or ".." in skill_id:
            raise AgentAPIError("skill_id is invalid", code="invalid_skill_id")
        return skill_id

    def _safe_category(self, value: Any) -> str:
        category = str(value or "").strip().strip("/")
        if not category or any(part in {"", ".", ".."} for part in category.split("/")):
            raise AgentAPIError("category is invalid", code="invalid_skill_category")
        if not all(SKILL_ID_RE.match(part) for part in category.split("/")):
            raise AgentAPIError("category is invalid", code="invalid_skill_category")
        return category

    def _session_id(self, value: Any) -> str:
        session_id = str(value or "").strip()
        if not session_id or len(session_id) > 256 or re.search(r"[\r\n\x00/\\]", session_id):
            raise AgentAPIError("conversation_id is invalid", code="invalid_conversation_id")
        return session_id

    def _agent_dir(self, name: str) -> Path:
        if name == BIG_BROTHER_AGENT_ID:
            return self.root_profile
        profile_dir = self._existing_profile_dir(name)
        if profile_dir == self._legacy_profile_dir(name):
            return self._legacy_agent_dir(name)
        return self._native_profile_dir(name)

    def _profile_dir(self, name: str) -> Path:
        return self._existing_profile_dir(name) or self._native_profile_dir(name)

    def _workspace_dir(self, name: str) -> Path:
        return self._workspace_dir_for_profile(name, self._profile_dir(name))

    def workspace_dir(self, raw_name: Any) -> Path:
        """Return the existing agent's persistent workspace."""
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        workspace = self._workspace_dir_for_profile(name, profile_dir)
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace.resolve()

    def _require_profile(self, name: str) -> Path:
        profile_dir = self._profile_dir(name)
        if not profile_dir.is_dir():
            raise AgentAPIError(f"Agent not found: {name}", code="agent_not_found", status=404)
        return profile_dir

    def _native_profile_dir(self, name: str) -> Path:
        return self.profiles_root / name

    def _legacy_agent_dir(self, name: str) -> Path:
        return self.legacy_agents_root / name

    def _legacy_profile_dir(self, name: str) -> Path:
        return self._legacy_agent_dir(name) / ".profile"

    def _existing_profile_dir(self, name: str) -> Path | None:
        if name == BIG_BROTHER_AGENT_ID:
            return self.root_profile if self.root_profile.is_dir() else None
        native = self._native_profile_dir(name)
        if native.is_dir():
            return native
        legacy = self._legacy_profile_dir(name)
        if legacy.is_dir():
            return legacy
        return None

    def _workspace_dir_for_profile(self, name: str, profile_dir: Path) -> Path:
        if name == BIG_BROTHER_AGENT_ID and profile_dir == self.root_profile:
            return self.root_profile / "workspace"
        if profile_dir == self._legacy_profile_dir(name):
            return self._legacy_agent_dir(name) / "workspace"
        return profile_dir / "workspace"

    def _is_native_agent_profile(self, path: Path) -> bool:
        if not path.is_dir():
            return False
        if not AGENT_NAME_RE.match(path.name) or ".." in path.name:
            return False
        return (path / METADATA_FILE).is_file() or (path / "workspace").is_dir()

    def _workspace_path(self, name: str, raw_path: Any, *, require_file: bool) -> Path:
        raw = str(raw_path or "").strip()
        if not raw:
            raise AgentAPIError("path is required", code="invalid_workspace_path")
        if raw.startswith("/"):
            raise AgentAPIError("path must be relative", code="invalid_workspace_path")
        root = self._workspace_dir(name).resolve()
        path = (root / raw).resolve()
        if path != root and root not in path.parents:
            raise AgentAPIError("path must not escape the agent workspace", code="invalid_workspace_path")
        if require_file and path == root:
            raise AgentAPIError("path must refer to a file", code="invalid_workspace_path")
        return path

    def _copy_seed_profile(self, profile_dir: Path, *, copy_credentials: bool) -> None:
        for filename in TEMPLATE_FILES:
            src = self.profile_template / filename
            if src.is_file():
                shutil.copy2(src, profile_dir / filename)
        for dirname in TEMPLATE_DIRS:
            src = self.profile_template / dirname
            dst = profile_dir / dirname
            if src.is_dir():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
        if copy_credentials:
            for filename in CREDENTIAL_FILES:
                src = self.root_profile / filename
                if src.is_file():
                    shutil.copy2(src, profile_dir / filename)

    def _copy_root_skills(self, profile_dir: Path, *, overwrite: bool) -> list[str]:
        skills_root = self.root_profile / "skills"
        target_root = profile_dir / "skills"
        target_root.mkdir(parents=True, exist_ok=True)
        copied = []
        if not skills_root.is_dir():
            return copied
        disabled = self._disabled_skills(self._read_config(self.root_profile))
        for skill_file in sorted(skills_root.rglob("SKILL.md")):
            source = skill_file.parent
            rel_parent = source.relative_to(skills_root)
            if rel_parent.parts[:1] == (BIG_BROTHER_SKILL_CATEGORY,):
                continue
            frontmatter = self._read_skill_frontmatter(skill_file)
            skill_id = str(frontmatter.get("name") or source.name).strip()
            if skill_id in disabled:
                continue
            destination = target_root / rel_parent
            if destination.exists():
                if not overwrite:
                    continue
                shutil.rmtree(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, destination)
            copied.append(str(rel_parent))
        return copied

    @staticmethod
    def _skill_files_for_profile(profile_dir: Path) -> dict[str, bytes]:
        skills_root = profile_dir / "skills"
        if not skills_root.is_dir():
            return {}
        return {
            path.relative_to(skills_root).as_posix(): path.read_bytes()
            for path in skills_root.rglob("SKILL.md")
            if path.is_file()
        }

    def _changed_skill_ids(
        self,
        profile_dir: Path,
        before: Mapping[str, bytes],
    ) -> set[str]:
        skills_root = profile_dir / "skills"
        changed = set()
        for path in skills_root.rglob("SKILL.md"):
            relative = path.relative_to(skills_root).as_posix()
            if before.get(relative) == path.read_bytes():
                continue
            frontmatter = self._read_skill_frontmatter(path)
            changed.add(str(frontmatter.get("name") or path.parent.name).strip())
        return {item for item in changed if item}

    def _skill_ids_for_profile(self, profile_dir: Path) -> set[str]:
        skills_root = profile_dir / "skills"
        if not skills_root.is_dir():
            return set()
        skill_ids = set()
        for path in skills_root.rglob("SKILL.md"):
            if not path.is_file():
                continue
            frontmatter = self._read_skill_frontmatter(path)
            skill_id = str(frontmatter.get("name") or path.parent.name).strip()
            if skill_id:
                skill_ids.add(skill_id)
        return skill_ids

    def _disable_new_skills(
        self,
        profile_dir: Path,
        before: set[str],
    ) -> set[str]:
        new_skill_ids = self._skill_ids_for_profile(profile_dir) - before
        if not new_skill_ids:
            return set()
        config = self._read_config(profile_dir)
        disabled = self._disabled_skills(config)
        self._write_disabled_skills(profile_dir, config, disabled | new_skill_ids)
        return new_skill_ids

    def _clear_seeded_disabled_skills(self, profile_dir: Path) -> None:
        config = self._read_config(profile_dir)
        skills_config = config.get("skills")
        if isinstance(skills_config, dict):
            skills_config["disabled"] = []
            with (profile_dir / "config.yaml").open("w", encoding="utf-8") as file:
                yaml.safe_dump(config, file, sort_keys=False, allow_unicode=False)

    def _write_workspace_cwd(self, profile_dir: Path, workspace_dir: Path) -> None:
        config = self._read_config(profile_dir)
        self._set_nested(config, ("terminal", "backend"), self._get_nested(config, ("terminal", "backend"), "local"))
        self._set_nested(config, ("terminal", "cwd"), str(workspace_dir))
        self._normalize_agent_skill_config(config)
        normalize_nine_router_config(config)
        self._write_yaml_atomic(profile_dir / "config.yaml", config)

    def _initialize_state_db(self, profile_dir: Path) -> None:
        conn = sqlite3.connect(profile_dir / "state.db", timeout=1.0)
        try:
            self._ensure_session_schema(conn)
            conn.commit()
        finally:
            conn.close()

    def _write_profile_manifest(
        self,
        profile_dir: Path,
        metadata: Mapping[str, Any],
    ) -> None:
        path = profile_dir / "profile.yaml"
        if path.is_file():
            return
        payload = {
            "name": str(metadata.get("profile_name") or metadata.get("name") or profile_dir.name),
            "description": str(metadata.get("description") or "Office and knowledge-work assistant"),
        }
        with path.open("w", encoding="utf-8") as file:
            yaml.safe_dump(payload, file, sort_keys=False, allow_unicode=False)

    def _ensure_workspace_agents(self, profile_dir: Path, workspace_dir: Path) -> None:
        target = workspace_dir / "AGENTS.md"
        if target.is_file():
            return
        for source in (profile_dir / "AGENTS.md", self.profile_template / "AGENTS.md"):
            if source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                return

    def _normalize_agent_skill_config(self, config: dict[str, Any]) -> None:
        shared_dir = str(self.root_profile / "skills")
        skills_config = config.get("skills")
        if not isinstance(skills_config, dict):
            skills_config = {}
            config["skills"] = skills_config
        external_dirs = skills_config.get("external_dirs")
        if external_dirs is None:
            external_dirs = []
        elif isinstance(external_dirs, str):
            external_dirs = [external_dirs]
        elif not isinstance(external_dirs, list):
            external_dirs = []
        external_dirs = [str(item) for item in external_dirs if str(item) != shared_dir]
        if external_dirs:
            skills_config["external_dirs"] = external_dirs
        else:
            skills_config.pop("external_dirs", None)

    def _find_agent_skill(self, profile_dir: Path, skill_id: str) -> Path | None:
        skills_root = profile_dir / "skills"
        direct = skills_root / skill_id
        if (direct / "SKILL.md").is_file():
            return direct
        if not skills_root.is_dir():
            return None
        for skill_file in skills_root.rglob("SKILL.md"):
            frontmatter = self._read_skill_frontmatter(skill_file)
            candidates = {
                skill_file.parent.name,
                str(frontmatter.get("name") or "").strip(),
                str(skill_file.parent.relative_to(skills_root)).strip(),
            }
            if skill_id in candidates:
                return skill_file.parent
        return None

    def _append_skills_from_dir(
        self,
        skills: list[dict[str, Any]],
        seen: set[str],
        root: Path,
        disabled: set[str],
    ) -> None:
        if not root.is_dir():
            return
        for skill_file in sorted(root.rglob("SKILL.md")):
            rel_parent = skill_file.parent.relative_to(root)
            skill_id = skill_file.parent.name
            frontmatter = self._read_skill_frontmatter(skill_file)
            name_value = str(frontmatter.get("name") or skill_id).strip()
            if name_value:
                skill_id = name_value
            if skill_id in seen:
                continue
            seen.add(skill_id)
            skills.append(
                {
                    "skill_id": skill_id,
                    "name": skill_id,
                    "path": str(rel_parent),
                    "description": str(frontmatter.get("description") or ""),
                    "category": str(
                        frontmatter.get("category")
                        or (rel_parent.parts[0] if len(rel_parent.parts) > 1 else "skills")
                    ),
                    "installed": True,
                    "enabled": skill_id not in disabled,
                }
            )

    def _disabled_skills(self, config: Mapping[str, Any]) -> set[str]:
        raw = self._get_nested(config, ("skills", "disabled"), []) or []
        if isinstance(raw, str):
            raw = [item.strip() for item in raw.split(",") if item.strip()]
        if not isinstance(raw, list):
            return set()
        disabled = set()
        for item in raw:
            try:
                disabled.add(self._skill_id(item))
            except AgentAPIError:
                continue
        return disabled

    def _set_skill_enabled(self, profile_dir: Path, skill_id: str, enabled: bool) -> None:
        config = self._read_config(profile_dir)
        disabled = self._disabled_skills(config)
        if enabled:
            disabled.discard(skill_id)
        else:
            disabled.add(skill_id)
        self._write_disabled_skills(profile_dir, config, disabled)

    def _write_disabled_skills(
        self,
        profile_dir: Path,
        config: dict[str, Any],
        disabled: set[str],
    ) -> None:
        self._normalize_agent_skill_config(config)
        skills_config = config.get("skills")
        if not isinstance(skills_config, dict):
            skills_config = {}
            config["skills"] = skills_config
        skills_config["disabled"] = sorted(disabled)
        with (profile_dir / "config.yaml").open("w", encoding="utf-8") as file:
            yaml.safe_dump(config, file, sort_keys=False, allow_unicode=False)

    def _read_metadata(self, profile_dir: Path) -> dict[str, Any]:
        path = profile_dir / METADATA_FILE
        if not path.is_file():
            return {}
        try:
            with path.open("r", encoding="utf-8") as file:
                data = json.load(file)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_metadata(self, profile_dir: Path, metadata: Mapping[str, Any]) -> None:
        profile_dir.mkdir(parents=True, exist_ok=True)
        with (profile_dir / METADATA_FILE).open("w", encoding="utf-8") as file:
            json.dump(dict(metadata), file, indent=2, sort_keys=True)
            file.write("\n")

    def _read_config(self, profile_dir: Path) -> dict[str, Any]:
        path = profile_dir / "config.yaml"
        if not path.is_file():
            return {}
        with path.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
        return data if isinstance(data, dict) else {}

    def _ensure_router_profile(self, profile_dir: Path, model: Any = None) -> None:
        config = self._read_config(profile_dir)
        selected_model = None
        if model is not None:
            selected_model = self._nonempty_string(model, "model")
        normalize_nine_router_config(config, selected_model)
        approvals = config.get("approvals")
        if not isinstance(approvals, dict):
            approvals = {}
            config["approvals"] = approvals
        approvals.setdefault("mode", "off")
        for subsystem in ("skills", "memory"):
            section = config.get(subsystem)
            if not isinstance(section, dict):
                section = {}
                config[subsystem] = section
            section.setdefault("write_approval", False)
            if subsystem == "memory" and honcho_memory_enabled():
                section.setdefault("provider", "honcho")
        agent_config = config.get("agent")
        if not isinstance(agent_config, dict):
            agent_config = {}
            config["agent"] = agent_config
        current_prompt = str(agent_config.get("system_prompt") or "").strip()
        for legacy_guidance in LEGACY_MANAGED_AGENT_GUIDANCE:
            current_prompt = current_prompt.replace(legacy_guidance, "").strip()
        if current_prompt:
            agent_config["system_prompt"] = current_prompt
        else:
            agent_config.pop("system_prompt", None)
        managed_config = config.get("brain4all")
        if isinstance(managed_config, dict):
            managed_config.pop("runtime_help_guidance", None)
        prompt_caching = config.get("prompt_caching")
        if not isinstance(prompt_caching, dict):
            prompt_caching = {}
            config["prompt_caching"] = prompt_caching
        # Profiles managed by this service use the longest supported reusable
        # prefix window so stable identity/context is not re-billed every few
        # minutes. This is deployment policy rather than a per-chat preference.
        prompt_caching["cache_ttl"] = "1h"
        compression = config.get("compression")
        if not isinstance(compression, dict):
            compression = {}
            config["compression"] = compression
        compression.setdefault("enabled", True)
        compression.setdefault("proactive_prune_tokens", 48_000)
        compression.setdefault("proactive_prune_min_result_chars", 8_000)
        compression.setdefault("proactive_prune_min_reclaim_tokens", 4_096)
        self._write_yaml_atomic(profile_dir / "config.yaml", config)

    def _effective_config(self, config: Mapping[str, Any], metadata: Mapping[str, Any]) -> dict[str, Any]:
        effort = str(self._get_nested(config, ("agent", "reasoning_effort"), "medium") or "medium").lower()
        approval = self._get_nested(config, ("approvals", "mode"), "off")
        return {
            "provider": "nine-router",
            "model": display_nine_router_model(
                self._get_nested(
                    config,
                    ("model", "default"),
                    NINE_ROUTER_DEFAULT_MODEL,
                )
            ),
            "reasoning": effort != "none",
            "effort": effort,
            "approval_mode": "off" if approval is False or str(approval).lower() == "off" else "on",
            "skills_write_approval": self._coerce_bool(
                self._get_nested(config, ("skills", "write_approval"), False)
            ),
            "memory_write_approval": self._coerce_bool(
                self._get_nested(config, ("memory", "write_approval"), False)
            ),
            "system_prompt": self._get_nested(config, ("agent", "system_prompt"), ""),
        }

    @staticmethod
    def _write_yaml_atomic(path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = yaml.safe_dump(dict(value), sort_keys=False, allow_unicode=False).encode("utf-8")
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            os.fchmod(descriptor, 0o640)
            with os.fdopen(descriptor, "wb") as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    def _conversation_title(self, body: Mapping[str, Any]) -> str | None:
        if "name" in body:
            return self._nullable_text(body["name"], field="name", max_chars=256)
        if "title" in body:
            return self._nullable_text(body["title"], field="title", max_chars=256)
        return None

    def _title_from_first_message(self, message: Any) -> str:
        """Build a short, stable fallback title from the first message."""
        candidates = []
        for line in str(message or "").splitlines():
            text = re.sub(r"^\s*(?:[-*+#>]|[0-9]+[.)])\s*", "", line).strip()
            if not text or re.fullmatch(r"`[^`]+`", text):
                continue
            text = re.sub(r"`([^`]+)`", r"\1", text)
            text = re.sub(r"\s+", " ", text).strip(" \t\r\n\"'")
            if text:
                candidates.append(text)
        title = candidates[0] if candidates else "Conversation"
        if len(title) > 48:
            shortened = title[:45].rsplit(" ", 1)[0].rstrip(".,:;- ")
            title = (shortened or title[:45]).rstrip() + "…"
        return title[0].upper() + title[1:] if title else "Conversation"

    def _conversation_has_default_title(self, profile_dir: Path, session_id: str) -> bool:
        with self._conversation_lock:
            session = self._session(profile_dir, session_id)
            current = str((session or {}).get("title") or "").strip()
            return bool(DEFAULT_CONVERSATION_TITLE_RE.fullmatch(current))

    def _clean_generated_title(self, value: Any) -> str:
        title = re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n\"'`*_#")
        title = re.sub(r"^(?:title|conversation title)\s*:\s*", "", title, flags=re.I)
        title = title.strip(" \t\r\n\"'`*_#")
        title = title.rstrip(".!?:;, -")
        words = title.split()
        if len(words) > 7:
            title = " ".join(words[:7])
        if len(title) > 48:
            title = title[:48].rsplit(" ", 1)[0].rstrip(".,:;- ")
        return title

    async def _summarize_conversation_title(self, message: Any, model: str) -> str:
        try:
            generated = await asyncio.wait_for(
                self.nine_router.generate_conversation_title(str(message or ""), model),
                timeout=20,
            )
            return self._clean_generated_title(generated) or self._title_from_first_message(message)
        except Exception:
            return self._title_from_first_message(message)

    def _auto_title_conversation(
        self,
        profile_dir: Path,
        session_id: str,
        message: Any,
        *,
        suggested_title: str | None = None,
    ) -> str | None:
        """Rename a default session after its first successful chat turn."""
        with self._conversation_lock:
            session = self._session(profile_dir, session_id)
            current = str((session or {}).get("title") or "").strip()
            if not DEFAULT_CONVERSATION_TITLE_RE.fullmatch(current):
                return current or None
            base = self._clean_generated_title(suggested_title) or self._title_from_first_message(message)
            for index in range(1, 101):
                suffix = "" if index == 1 else f" {index}"
                candidate = base[: max(1, 256 - len(suffix))].rstrip() + suffix
                try:
                    self._update_session_title(profile_dir, session_id, candidate)
                    return candidate
                except AgentAPIError as exc:
                    if exc.code != "conversation_name_exists":
                        return None
            return None

    def _next_default_conversation_title(self, profile_dir: Path) -> str:
        db_path = profile_dir / "state.db"
        if not db_path.is_file():
            return DEFAULT_CONVERSATION_TITLE
        conn = self._open_readonly_db(db_path)
        try:
            titles = [
                str(row["title"] or "").strip()
                for row in conn.execute(
                    "SELECT title FROM sessions WHERE title IS NOT NULL"
                ).fetchall()
            ]
        except sqlite3.Error:
            titles = []
        finally:
            conn.close()

        highest = 0
        for title in titles:
            match = DEFAULT_CONVERSATION_TITLE_RE.fullmatch(title)
            if match is None:
                continue
            highest = max(highest, int(match.group(1) or 1))
        return (
            DEFAULT_CONVERSATION_TITLE
            if highest == 0
            else f"{DEFAULT_CONVERSATION_TITLE} {highest + 1}"
        )

    def _conversation_model(self, profile_dir: Path, body: Mapping[str, Any]) -> str:
        if body.get("model"):
            return route_nine_router_model(
                self._nonempty_string(body["model"], "model")
            )
        config = self._read_config(profile_dir)
        model = self._get_nested(config, ("model", "default"), None)
        if not model:
            model = self._get_nested(config, ("model", "model"), "")
        return str(model or "")

    def _session_db(self, profile_dir: Path):
        from hermes_state import SessionDB

        return SessionDB(db_path=profile_dir / "state.db")

    def _create_session(
        self,
        profile_dir: Path,
        session_id: str,
        *,
        model: str,
        title: str | None,
    ) -> None:
        # SessionDB creates the row and assigns its title in two separate
        # transactions. A title conflict therefore used to leave an untitled
        # session behind while the API returned 409. Initialize the native
        # schema first, then insert the row and title together atomically.
        try:
            db = self._session_db(profile_dir)
            close = getattr(db, "close", None)
            if callable(close):
                close()
        except Exception:
            # The SQLite fallback also initializes the small compatible schema
            # used by tests and degraded local installations.
            pass
        self._create_session_sqlite(profile_dir, session_id, model=model, title=title)

    def _create_session_sqlite(
        self,
        profile_dir: Path,
        session_id: str,
        *,
        model: str,
        title: str | None,
    ) -> None:
        db_path = profile_dir / "state.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=1.0)
        try:
            self._ensure_session_schema(conn)
            now = time.time()
            conn.execute(
                "INSERT INTO sessions (id, source, model, title, started_at) VALUES (?, ?, ?, ?, ?)",
                (session_id, "api", model, title, now),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            if "title" in str(exc).lower():
                raise AgentAPIError(
                    "conversation name already exists",
                    code="conversation_name_exists",
                    status=409,
                ) from exc
            raise AgentAPIError(
                "conversation already exists",
                code="conversation_exists",
                status=409,
            ) from exc
        finally:
            conn.close()

    def _update_session_title(self, profile_dir: Path, session_id: str, title: str) -> None:
        try:
            db = self._session_db(profile_dir)
            db.set_session_title(session_id, title)
            return
        except Exception as exc:
            if "UNIQUE" in str(exc).upper() or "already" in str(exc).lower():
                raise AgentAPIError("conversation name already exists", code="conversation_name_exists", status=409) from exc
            self._update_session_title_sqlite(profile_dir, session_id, title)

    def _update_session_title_sqlite(self, profile_dir: Path, session_id: str, title: str) -> None:
        conn = sqlite3.connect(profile_dir / "state.db", timeout=1.0)
        try:
            cursor = conn.execute("UPDATE sessions SET title = ? WHERE id = ?", (title, session_id))
            if cursor.rowcount == 0:
                raise AgentAPIError("conversation not found", code="conversation_not_found", status=404)
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise AgentAPIError("conversation name already exists", code="conversation_name_exists", status=409) from exc
        finally:
            conn.close()

    def _delete_session(self, profile_dir: Path, session_id: str) -> None:
        try:
            db = self._session_db(profile_dir)
            db.delete_session(session_id)
            return
        except Exception:
            self._delete_session_sqlite(profile_dir, session_id)

    def _delete_session_sqlite(self, profile_dir: Path, session_id: str) -> None:
        conn = sqlite3.connect(profile_dir / "state.db", timeout=1.0)
        try:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            cursor = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            if cursor.rowcount == 0:
                raise AgentAPIError("conversation not found", code="conversation_not_found", status=404)
            conn.commit()
        finally:
            conn.close()

    def _ensure_session_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                user_id TEXT,
                model TEXT,
                model_config TEXT,
                system_prompt TEXT,
                parent_session_id TEXT,
                started_at REAL NOT NULL,
                ended_at REAL,
                end_reason TEXT,
                message_count INTEGER DEFAULT 0,
                tool_call_count INTEGER DEFAULT 0,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                cache_read_tokens INTEGER DEFAULT 0,
                cache_write_tokens INTEGER DEFAULT 0,
                reasoning_tokens INTEGER DEFAULT 0,
                billing_provider TEXT,
                billing_base_url TEXT,
                billing_mode TEXT,
                estimated_cost_usd REAL,
                actual_cost_usd REAL,
                cost_status TEXT,
                cost_source TEXT,
                pricing_version TEXT,
                title TEXT,
                api_call_count INTEGER DEFAULT 0,
                FOREIGN KEY (parent_session_id) REFERENCES sessions(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES sessions(id),
                role TEXT NOT NULL,
                content TEXT,
                tool_call_id TEXT,
                tool_calls TEXT,
                tool_name TEXT,
                timestamp REAL NOT NULL,
                token_count INTEGER,
                finish_reason TEXT,
                reasoning TEXT,
                reasoning_content TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at DESC)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_title_unique ON sessions(title) WHERE title IS NOT NULL")

    def _read_skill_frontmatter(self, path: Path) -> dict[str, Any]:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            return {}
        if not text.startswith("---"):
            return {}
        parts = text.split("---", 2)
        if len(parts) < 3:
            return {}
        try:
            data = yaml.safe_load(parts[1]) or {}
            if isinstance(data, dict):
                metadata = data.get("metadata") or {}
                hermes = metadata.get("hermes") if isinstance(metadata, dict) else {}
                if isinstance(hermes, dict) and "category" in hermes and "category" not in data:
                    data["category"] = hermes["category"]
                return data
        except Exception:
            return {}
        return {}

    async def _run_profile_command(
        self,
        name: str,
        command: list[str],
        *,
        engine: str = "hermes",
        timeout_seconds: int,
    ) -> dict[str, Any]:
        return await self._run_hermes_command(
            self._profile_dir(name),
            self._workspace_dir(name),
            command,
            engine=engine,
            timeout_seconds=timeout_seconds,
        )

    async def _run_hermes_command(
        self,
        hermes_home: Path,
        cwd: Path,
        command: list[str],
        *,
        engine: str = "hermes",
        timeout_seconds: int,
    ) -> dict[str, Any]:
        env = self._command_env(hermes_home, engine)
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
        except asyncio.CancelledError:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
            raise
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise AgentAPIError(
                "agent command timed out",
                code="agent_command_timeout",
                status=504,
            )
        masked_command = list(command)
        if len(masked_command) >= 2 and masked_command[-2] == "-z":
            masked_command[-1] = "<message>"
        return {
            "command": masked_command,
            "exit_code": int(proc.returncode or 0),
            "stdout": stdout.decode("utf-8", "replace"),
            "stderr": stderr.decode("utf-8", "replace"),
        }

    def _command_env(self, hermes_home: Path, engine: str) -> dict[str, str]:
        env = os.environ.copy()
        env["HERMES_HOME"] = str(hermes_home)
        env.setdefault("HOME", str(Path.home()))
        env.setdefault("HERMES_ACCEPT_HOOKS", "1")
        self._load_agent_credentials(env)
        return env

    def _hermes_binary(self) -> str:
        return os.environ.get("HERMES_CLI", "hermes")

    def _agent_config_dir(self) -> Path:
        configured = os.environ.get("AGENT_CONFIG_DIR")
        if configured:
            return Path(configured)
        return Path(os.environ.get("HOME") or str(Path.home())) / ".config" / "sandbox-agent"

    def _agent_env_file(self) -> Path:
        configured = os.environ.get("AGENT_ENV_FILE")
        if configured:
            return Path(configured)
        return self._agent_config_dir() / "credentials.env"

    def _load_agent_credentials(self, env: dict[str, str]) -> None:
        env_file = self._agent_env_file()
        try:
            lines = env_file.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return
        allowed = set(AGENT_CREDENTIAL_ENV_KEYS)
        for line in lines:
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if key in allowed and value:
                env[key] = value

    def _conversation_provider(self, profile_dir: Path, body: Mapping[str, Any]) -> str:
        return NINE_ROUTER_PROVIDER

    def _latest_session_ids(self, profile_dir: Path) -> dict[str, float]:
        return {
            item["id"]: self._session_sort_value(item)
            for item in self._sessions(profile_dir, limit=500)
            if item.get("id")
        }

    def _detect_changed_session(self, before: Mapping[str, float], after: Mapping[str, float]) -> str | None:
        new_ids = [session_id for session_id in after if session_id not in before]
        if new_ids:
            return max(new_ids, key=lambda session_id: after[session_id])
        changed = [
            session_id
            for session_id, started_at in after.items()
            if before.get(session_id) != started_at
        ]
        if changed:
            return max(changed, key=lambda session_id: after[session_id])
        return max(after, key=lambda session_id: after[session_id]) if after else None

    def _sessions(self, profile_dir: Path, *, limit: int, offset: int = 0) -> list[dict[str, Any]]:
        db_path = profile_dir / "state.db"
        if not db_path.is_file():
            return []
        conn = self._open_readonly_db(db_path)
        try:
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(sessions)").fetchall()
            }
            activity_columns = [
                column
                for column in ("last_active_at", "updated_at", "ended_at", "started_at", "created_at")
                if column in columns
            ]
            sql = "SELECT * FROM sessions"
            if activity_columns:
                activity_values = [f"COALESCE({column}, 0)" for column in activity_columns]
                activity = activity_values[0] if len(activity_values) == 1 else f"MAX({', '.join(activity_values)})"
                sql += f" ORDER BY {activity} DESC, id DESC"
            else:
                sql += " ORDER BY id DESC"
            sql += " LIMIT ? OFFSET ?"
            rows = conn.execute(sql, (limit, max(0, offset))).fetchall()
            return [self._row_dict(row) for row in rows]
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def _session(self, profile_dir: Path, session_id: str) -> dict[str, Any] | None:
        db_path = profile_dir / "state.db"
        if not db_path.is_file():
            return None
        conn = self._open_readonly_db(db_path)
        try:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            return self._row_dict(row) if row else None
        except sqlite3.Error:
            return None
        finally:
            conn.close()

    def _messages(self, profile_dir: Path, session_id: str) -> list[dict[str, Any]]:
        db_path = profile_dir / "state.db"
        if not db_path.is_file():
            return []
        conn = self._open_readonly_db(db_path)
        try:
            order_by = self._preferred_order_column(conn, "messages", descending=False)
            sql = "SELECT * FROM messages WHERE session_id = ?"
            if order_by:
                sql += f" ORDER BY {order_by} ASC"
            rows = conn.execute(sql, (session_id,)).fetchall()
            return [self._row_dict(row) for row in rows]
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def _persist_conversation_context(
        self,
        profile_dir: Path,
        session_id: str,
        context: Mapping[str, Any],
    ) -> None:
        """Persist the last real prompt occupancy without changing Hermes' schema."""
        if not int(context.get("used") or 0) and not int(context.get("limit") or 0):
            return
        conn = sqlite3.connect(profile_dir / "state.db", timeout=1.0)
        try:
            row = conn.execute(
                "SELECT model_config FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                return
            try:
                model_config = json.loads(row[0]) if row[0] else {}
            except (TypeError, ValueError, json.JSONDecodeError):
                model_config = {}
            if not isinstance(model_config, dict):
                model_config = {}
            model_config["brain4all_context"] = {
                "used": max(0, int(context.get("used") or 0)),
                "limit": max(0, int(context.get("limit") or 0)),
                "model": str(context.get("model") or ""),
            }
            conn.execute(
                "UPDATE sessions SET model_config = ? WHERE id = ?",
                (json.dumps(model_config, separators=(",", ":")), session_id),
            )
            conn.commit()
        except sqlite3.Error:
            # Context telemetry must never make a successful chat turn fail.
            pass
        finally:
            conn.close()

    def _open_readonly_db(self, path: Path) -> sqlite3.Connection:
        uri = f"file:{path.resolve()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=1.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _preferred_order_column(
        self,
        conn: sqlite3.Connection,
        table: str,
        *,
        descending: bool = True,
    ) -> str | None:
        try:
            columns = {
                row["name"]
                for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
        except sqlite3.Error:
            return None
        candidates = (
            ("started_at", "updated_at", "created_at", "timestamp", "id")
            if descending
            else ("timestamp", "created_at", "id")
        )
        for column in candidates:
            if column in columns:
                return column
        return None

    def _row_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        result = {}
        for key in row.keys():
            value = row[key]
            if key.endswith("_config") and isinstance(value, str) and value:
                try:
                    result[key] = json.loads(value)
                    continue
                except Exception:
                    pass
            result[key] = value
        return result

    def _session_sort_value(self, item: Mapping[str, Any]) -> float:
        for field in ("started_at", "updated_at", "created_at", "timestamp"):
            value = item.get(field)
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return 0.0

    def _normalize_skill_list(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [item.strip() for item in value.split(",") if item.strip()]
        if not isinstance(value, list):
            raise AgentAPIError("skills must be an array", code="invalid_skills")
        result = []
        seen = set()
        for item in value:
            skill_id = self._skill_id(item)
            if skill_id not in seen:
                seen.add(skill_id)
                result.append(skill_id)
        return result

    def _read_text(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def _write_text(self, path: Path, value: Any) -> None:
        text = self._text_value(value, field=path.name, max_chars=MAX_TEXT_CHARS)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _text_value(self, value: Any, *, field: str, max_chars: int) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            raise AgentAPIError(f"{field} must be a string", code="invalid_text")
        if "\x00" in value:
            raise AgentAPIError(f"{field} must not contain NUL bytes", code="invalid_text")
        if len(value) > max_chars:
            raise AgentAPIError(f"{field} is too long", code="invalid_text", status=413)
        return value

    def _nonempty_string(self, value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise AgentAPIError(f"{field} must be a non-empty string", code="invalid_text")
        result = value.strip()
        if len(result) > 256:
            raise AgentAPIError(f"{field} is too long", code="invalid_text")
        return result

    def _coerce_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        raise AgentAPIError("reasoning must be boolean", code="invalid_agent_config")

    def _nullable_text(self, value: Any, *, field: str, max_chars: int) -> str | None:
        if value is None:
            return None
        text = self._text_value(value, field=field, max_chars=max_chars).strip()
        return text or None

    def _approval_mode(self, value: Any) -> str:
        if isinstance(value, bool):
            return "on" if value else "off"
        mode = str(value or "").strip().lower()
        if mode in {"on", "manual", "true", "1", "yes"}:
            return "on"
        if mode in {"off", "false", "0", "no"}:
            return "off"
        raise AgentAPIError("approval_mode must be on or off", code="invalid_agent_config")

    def _get_nested(self, data: Mapping[str, Any], keys: tuple[str, ...], default: Any = None) -> Any:
        current: Any = data
        for key in keys:
            if not isinstance(current, Mapping) or key not in current:
                return default
            current = current[key]
        return current

    def _set_nested(self, data: dict[str, Any], keys: tuple[str, ...], value: Any) -> None:
        current = data
        for key in keys[:-1]:
            child = current.get(key)
            if not isinstance(child, dict):
                child = {}
                current[key] = child
            current = child
        current[keys[-1]] = value

    def _deep_merge(self, target: dict[str, Any], update: Mapping[str, Any]) -> None:
        for key, value in update.items():
            if isinstance(value, Mapping) and isinstance(target.get(key), dict):
                self._deep_merge(target[key], value)
                continue
            target[key] = value
