"""Agent operations methods for the Hermes runtime adapter."""

from .hermes_support import (
    AgentAPIError,
    Any,
    BIG_BROTHER_AGENT_ID,
    BIG_BROTHER_DESCRIPTION,
    BIG_BROTHER_DISPLAY_NAME,
    CUSTOM_SKILL_CATEGORY,
    Mapping,
    NINE_ROUTER_PROVIDER,
    PROFILES_REGISTRY_FILE,
    PROFILE_STATE_DIRS,
    Path,
    datetime,
    normalize_nine_router_config,
    os,
    shutil,
    sqlite3,
    tempfile,
    time,
    timezone,
    uuid,
    yaml,
)


class AgentOperationsMixin:
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
