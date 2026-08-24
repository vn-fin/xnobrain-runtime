"""AgentProfiles methods for the Hermes runtime adapter."""

from .hermes_support import (
    AGENT_NAME_RE,
    AgentAPIError,
    Any,
    BIG_BROTHER_AGENT_ID,
    BIG_BROTHER_SKILL_CATEGORY,
    BIG_BROTHER_SKILL_ID,
    CREDENTIAL_FILES,
    GENERATED_AGENT_ID_ALPHABET,
    GENERATED_AGENT_ID_FIRST_ALPHABET,
    GENERATED_AGENT_ID_LENGTH,
    GENERATED_AGENT_NAME_RE,
    LEGACY_MANAGED_AGENT_GUIDANCE,
    MAX_TEXT_CHARS,
    METADATA_FILE,
    Mapping,
    LLM_ROUTER_DEFAULT_MODEL,
    LLM_ROUTER_PROVIDER_KEY,
    Path,
    SKILL_ID_RE,
    TEMPLATE_DIRS,
    TEMPLATE_FILES,
    display_llm_model,
    json,
    normalize_llm_router_config,
    os,
    re,
    secrets,
    shutil,
    sqlite3,
    tempfile,
    yaml,
)


class AgentProfilesMixin:
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
            frontmatter = self._read_skill_frontmatter(skill_file)
            skill_id = str(frontmatter.get("name") or source.name).strip()
            if (
                skill_id == BIG_BROTHER_SKILL_ID
                or rel_parent.parts[:1] == (BIG_BROTHER_SKILL_CATEGORY,)
            ):
                continue
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


    def _write_workspace_cwd(self, profile_dir: Path, workspace_dir: Path) -> None:
        config = self._read_config(profile_dir)
        self._set_nested(config, ("terminal", "backend"), self._get_nested(config, ("terminal", "backend"), "local"))
        self._set_nested(config, ("terminal", "cwd"), str(workspace_dir))
        self._normalize_agent_skill_config(config)
        normalize_llm_router_config(config)
        self._write_yaml_atomic(profile_dir / "config.yaml", config)


    def _ensure_agent_workspace(self, name: str, profile_dir: Path) -> Path | None:
        """Prepare the private workspace used by an ordinary agent session.

        Big Brother intentionally uses the unrestricted root Hermes profile and
        must not inherit the per-agent workspace policy.
        """
        if name == BIG_BROTHER_AGENT_ID:
            return None
        workspace_dir = self._workspace_dir_for_profile(name, profile_dir)
        workspace_dir.mkdir(parents=True, exist_ok=True)
        config = self._read_config(profile_dir)
        configured_cwd = str(self._get_nested(config, ("terminal", "cwd"), "") or "")
        if configured_cwd != str(workspace_dir):
            self._write_workspace_cwd(profile_dir, workspace_dir)
        self._ensure_workspace_agents(profile_dir, workspace_dir)
        return workspace_dir.resolve()


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
        self._apply_default_skills_policy(profile_dir)
        config = self._read_config(profile_dir)
        selected_model = None
        if model is not None:
            selected_model = self._nonempty_string(model, "model")
        normalize_llm_router_config(config, selected_model)
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
        managed_config = config.get("xnobrain")
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
            "provider": LLM_ROUTER_PROVIDER_KEY,
            "model": display_llm_model(
                self._get_nested(
                    config,
                    ("model", "default"),
                    LLM_ROUTER_DEFAULT_MODEL,
                )
            ),
            "assignment_id": str(
                self._get_nested(config, ("model", "assignment_id"), "") or ""
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
            "checkpoints_enabled": self._coerce_bool(
                self._get_nested(config, ("checkpoints", "enabled"), False)
            ),
            "goal_max_turns": int(
                self._get_nested(config, ("goals", "max_turns"), 20) or 20
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
