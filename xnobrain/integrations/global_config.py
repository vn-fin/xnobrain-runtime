"""GlobalConfig behavior for the global Hermes profile."""

from .config_support import (
    Any,
    ConfigAPIError,
    DEFAULT_SOUL,
    MAX_CONFIG_STRING_CHARS,
    MAX_TEXT_CHARS,
    Mapping,
    LLM_ROUTER_API_BASE_URL,
    LLM_ROUTER_DEFAULT_MODEL,
    LLM_ROUTER_PROVIDER,
    LLM_ROUTER_PROVIDER_KEY,
    Path,
    _MISSING,
    _SAFE_ID_RE,
    display_llm_model,
    hashlib,
    normalize_llm_router_config,
    os,
    tempfile,
    time,
    yaml,
)


class GlobalConfigMixin:
    def get_config(self) -> dict[str, Any]:
        return self._describe(self._read_config())


    def ensure_write_approval_defaults(self) -> dict[str, Any]:
        """Persist XNOBrain's automatic execution defaults when unspecified."""
        config = self._read_config()
        changed = False
        approvals = config.get("approvals")
        if not isinstance(approvals, dict):
            approvals = {}
            config["approvals"] = approvals
            changed = True
        if "mode" not in approvals:
            approvals["mode"] = "off"
            changed = True
        for subsystem in ("skills", "memory"):
            section = config.get(subsystem)
            if not isinstance(section, dict):
                section = {}
                config[subsystem] = section
                changed = True
            if "write_approval" not in section:
                section["write_approval"] = False
                changed = True
        if changed:
            self._write_config(config)
        return self._describe(config)


    def update_config(self, body: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(body, Mapping):
            raise ConfigAPIError("request body must be an object")

        allowed = {
            "provider",
            "model",
            "assignment_id",
            "reasoning",
            "effort",
            "reasoning_effort",
            "approval_mode",
            "skills_write_approval",
            "memory_write_approval",
            "goal_max_turns",
            "system_prompt",
            "soul",
            "config",
        }
        unknown = sorted(set(body) - allowed)
        if unknown:
            raise ConfigAPIError(
                f"Unsupported config fields: {', '.join(unknown)}",
                code="unsupported_config_fields",
            )

        config = self._read_config()
        touched = False

        if "config" in body:
            patch = body["config"]
            if not isinstance(patch, Mapping):
                raise ConfigAPIError("config must be an object")
            self._deep_merge(config, self._sanitize_config_value(patch, "config"))
            touched = True

        selection_provider = None
        if "provider" in body:
            selection_provider = self._nonempty_string(body["provider"], "provider").lower()
            if not _SAFE_ID_RE.fullmatch(selection_provider):
                raise ConfigAPIError("provider is invalid", code="unsupported_provider")
            touched = True
        if "model" in body:
            self._set_nested(config, ("model", "default"), self._nonempty_string(body["model"], "model"))
            touched = True
        if "assignment_id" in body:
            assignment_id = str(body.get("assignment_id") or "").strip()
            if assignment_id and not _SAFE_ID_RE.fullmatch(assignment_id):
                raise ConfigAPIError("assignment_id is invalid", code="invalid_agent_config")
            if assignment_id:
                self._set_nested(config, ("model", "assignment_id"), assignment_id)
            else:
                model_config = config.get("model")
                if isinstance(model_config, dict):
                    model_config.pop("assignment_id", None)
            touched = True
        if "reasoning" in body or "effort" in body or "reasoning_effort" in body:
            effort = body.get("effort", body.get("reasoning_effort", _MISSING))
            if effort is _MISSING:
                effort = self._get_nested(config, ("agent", "reasoning_effort"), "medium")
            effort = str(effort or "medium").strip().lower()
            if "reasoning" in body and not self._coerce_bool(body["reasoning"]):
                effort = "none"
            elif effort == "none":
                effort = "medium"
            self._set_reasoning_effort(config, effort)
            touched = True
        if "approval_mode" in body:
            mode = self._approval_mode(body["approval_mode"])
            self._set_nested(config, ("approvals", "mode"), "manual" if mode == "on" else "off")
            touched = True
        if "skills_write_approval" in body:
            self._set_nested(
                config,
                ("skills", "write_approval"),
                self._coerce_bool(body["skills_write_approval"], field="skills_write_approval"),
            )
            touched = True
        if "memory_write_approval" in body:
            self._set_nested(
                config,
                ("memory", "write_approval"),
                self._coerce_bool(body["memory_write_approval"], field="memory_write_approval"),
            )
            touched = True
        if "goal_max_turns" in body:
            try:
                goal_max_turns = int(body["goal_max_turns"])
            except (TypeError, ValueError) as error:
                raise ConfigAPIError("goal_max_turns must be an integer") from error
            if goal_max_turns not in {10, 15, 20, 25, 30}:
                raise ConfigAPIError("goal_max_turns must be one of: 10, 15, 20, 25, 30")
            self._set_nested(config, ("goals", "max_turns"), goal_max_turns)
            touched = True
        soul = body.get("soul", body.get("system_prompt", _MISSING))
        if soul is not _MISSING:
            self._write_text(self.root_profile / "SOUL.md", soul, field="soul")

        selected_model = body.get("model") if "model" in body else None
        normalize_llm_router_config(config, selected_model, selection_provider=selection_provider)
        if touched or soul is not _MISSING:
            if (self.root_profile / "config.yaml").is_file():
                self._snapshot_config()
            self._write_config(config)
        return self._describe(config)


    def ensure_default_soul(self, *, overwrite: bool = False) -> None:
        path = self.root_profile / "SOUL.md"
        if overwrite or not path.is_file() or not path.read_text(encoding="utf-8").strip():
            self._write_text(path, DEFAULT_SOUL, field="soul")


    def _describe(self, config: Mapping[str, Any]) -> dict[str, Any]:
        normalized = self._sanitize_config_value(config, "config")
        normalize_llm_router_config(normalized)
        public_model = display_llm_model(
            self._get_nested(
                normalized,
                ("model", "default"),
                LLM_ROUTER_DEFAULT_MODEL,
            )
        )
        self._set_nested(normalized, ("model", "default"), public_model)
        provider_config = normalized.get("providers", {}).get(LLM_ROUTER_PROVIDER_KEY)
        if isinstance(provider_config, dict):
            for field in ("default_model", "model"):
                if field in provider_config:
                    provider_config[field] = display_llm_model(
                        provider_config[field]
                    )
        effort = str(self._get_nested(config, ("agent", "reasoning_effort"), "medium") or "medium").lower()
        approval = self._get_nested(config, ("approvals", "mode"), "off")
        soul = self._read_text(self.root_profile / "SOUL.md")
        return {
            "object": "xnobrain.global_config",
            "root_profile": str(self.root_profile),
            "config_path": str(self.root_profile / "config.yaml"),
            "soul_path": str(self.root_profile / "SOUL.md"),
            "provider": str(
                self._get_nested(normalized, ("model", "selection_provider"), "")
                or LLM_ROUTER_PROVIDER_KEY
            ),
            "model": public_model,
            "assignment_id": str(
                self._get_nested(normalized, ("model", "assignment_id"), "") or ""
            ),
            "base_url": LLM_ROUTER_API_BASE_URL,
            "reasoning": effort != "none",
            "effort": effort,
            "reasoning_effort": effort,
            "approval_mode": "off" if approval is False or str(approval).lower() == "off" else "on",
            "skills_write_approval": self._coerce_bool(
                self._get_nested(config, ("skills", "write_approval"), False),
                field="skills.write_approval",
            ),
            "memory_write_approval": self._coerce_bool(
                self._get_nested(config, ("memory", "write_approval"), False),
                field="memory.write_approval",
            ),
            "goal_max_turns": int(
                self._get_nested(config, ("goals", "max_turns"), 20) or 20
            ),
            "system_prompt": soul,
            "soul": soul,
            "router": {
                "provider": LLM_ROUTER_PROVIDER,
                "base_url": LLM_ROUTER_API_BASE_URL,
                "default_model": LLM_ROUTER_DEFAULT_MODEL,
            },
            "config": normalized,
            "updated_at": time.time(),
        }


    def _read_config(self) -> dict[str, Any]:
        path = self.root_profile / "config.yaml"
        if not path.is_file():
            return {}
        with path.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
        return data if isinstance(data, dict) else {}


    def _write_config(self, config: Mapping[str, Any]) -> None:
        self.root_profile.mkdir(parents=True, exist_ok=True)
        path = self.root_profile / "config.yaml"
        payload = yaml.safe_dump(dict(config), sort_keys=False, allow_unicode=False).encode("utf-8")
        self._atomic_write(path, payload)


    def _atomic_write(self, path: Path, payload: bytes, *, mode: int = 0o640) -> None:
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


    def _snapshot_config(self) -> None:
        path = self.root_profile / "config.yaml"
        payload = path.read_bytes() if path.is_file() else b"{}\n"
        digest = hashlib.sha256(payload).hexdigest()
        snapshot = self.root_profile / "snapshots" / "config" / f"{time.time_ns()}-{digest[:12]}.yaml"
        self._atomic_write(snapshot, payload, mode=0o440)


    def _sanitize_config_value(self, value: Any, path: str) -> Any:
        if isinstance(value, Mapping):
            return {
                str(key): self._sanitize_config_value(item, f"{path}.{key}")
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._sanitize_config_value(item, path) for item in value]
        if isinstance(value, str):
            if "\x00" in value:
                raise ConfigAPIError(f"{path} must not contain NUL bytes")
            if len(value) > MAX_CONFIG_STRING_CHARS:
                raise ConfigAPIError(f"{path} is too long", status=413)
            return value
        if value is None or isinstance(value, (bool, int, float)):
            return value
        raise ConfigAPIError(f"{path} contains an unsupported value")


    def _deep_merge(self, target: dict[str, Any], patch: Mapping[str, Any]) -> None:
        for key, value in patch.items():
            if isinstance(value, Mapping) and isinstance(target.get(key), dict):
                self._deep_merge(target[key], value)
            else:
                target[key] = value


    def _set_reasoning_effort(self, config: dict[str, Any], effort: str) -> None:
        if effort not in {"auto", "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}:
            raise ConfigAPIError(
                "effort must be one of: auto, none, minimal, low, medium, high, xhigh, max, ultra",
                code="invalid_reasoning_effort",
            )
        self._set_nested(config, ("agent", "reasoning_effort"), effort)


    def _normalize_skill_list(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [item.strip() for item in value.split(",") if item.strip()]
        if not isinstance(value, list):
            raise ConfigAPIError("skills must be an array", code="invalid_skills")
        result = []
        seen = set()
        for item in value:
            skill_id = self._skill_id(item)
            if skill_id not in seen:
                seen.add(skill_id)
                result.append(skill_id)
        return result


    def _text_value(self, value: Any, *, field: str, max_chars: int) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ConfigAPIError(f"{field} must be a string", code="invalid_text")
        if "\x00" in value:
            raise ConfigAPIError(f"{field} must not contain NUL bytes", code="invalid_text")
        if len(value) > max_chars:
            raise ConfigAPIError(f"{field} is too long", code="invalid_text", status=413)
        return value


    def _write_text(self, path: Path, value: Any, *, field: str) -> None:
        text = self._text_value(value, field=field, max_chars=MAX_TEXT_CHARS)
        self._atomic_write(path, text.encode("utf-8"))


    def _read_text(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""


    def _nonempty_string(self, value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ConfigAPIError(f"{field} must be a non-empty string", code="invalid_text")
        result = value.strip()
        if len(result) > 512:
            raise ConfigAPIError(f"{field} is too long", code="invalid_text")
        return result


    def _coerce_bool(self, value: Any, *, field: str = "reasoning") -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        raise ConfigAPIError(f"{field} must be boolean", code="invalid_config")


    def _approval_mode(self, value: Any) -> str:
        if isinstance(value, bool):
            return "on" if value else "off"
        mode = str(value or "").strip().lower()
        if mode in {"on", "manual", "true", "1", "yes"}:
            return "on"
        if mode in {"off", "false", "0", "no"}:
            return "off"
        raise ConfigAPIError("approval_mode must be on or off", code="invalid_config")


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


    def _hermes_binary(self) -> str:
        return os.environ.get("HERMES_CLI", "hermes")
