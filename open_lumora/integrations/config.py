"""Adapter for Hermes root-profile configuration APIs and files."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

import yaml

from .nine_router import (
    NINE_ROUTER_API_BASE_URL,
    NINE_ROUTER_DEFAULT_MODEL,
    NINE_ROUTER_PROVIDER,
    normalize_nine_router_config,
)


SKILL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_TEXT_CHARS = 200_000
MAX_CONFIG_STRING_CHARS = 100_000
MAX_INSTALL_TIMEOUT_SECONDS = 600
DEFAULT_INSTALL_TIMEOUT_SECONDS = 180
DEFAULT_ROOT_PROFILE = str(Path.home() / ".hermes")
DEFAULT_SOUL = (
    "You are a trusted personal assistant for one person. Be useful, discreet, "
    "and clear. Help with planning, writing, research, analysis, decisions, "
    "everyday operations, and technical work when needed.\n\n"
    "Work with practical judgment. Ask a focused question when the task is "
    "ambiguous, make reasonable assumptions when the risk is low, and explain "
    "uncertainty plainly. Keep responses compact by default, but give enough "
    "detail for the user to act confidently.\n"
)

_MISSING = object()


class ConfigAPIError(ValueError):
    """Expected API error for root profile config operations."""

    def __init__(self, message: str, *, code: str = "invalid_config", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


class GlobalConfigManager:
    """Filesystem-backed manager for the root profile in the runtime container."""

    def __init__(self, *, root_profile: str | Path | None = None):
        self.root_profile = Path(
            root_profile
            or os.environ.get("HERMES_ROOT_PROFILE")
            or DEFAULT_ROOT_PROFILE
        )

    def get_config(self) -> dict[str, Any]:
        return self._describe(self._read_config())

    def ensure_write_approval_defaults(self) -> dict[str, Any]:
        """Persist Open Lumora's safe default for new skill and memory writes."""
        config = self._read_config()
        changed = False
        for subsystem in ("skills", "memory"):
            section = config.get(subsystem)
            if not isinstance(section, dict):
                section = {}
                config[subsystem] = section
                changed = True
            if "write_approval" not in section:
                section["write_approval"] = True
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
            "reasoning",
            "effort",
            "reasoning_effort",
            "approval_mode",
            "skills_write_approval",
            "memory_write_approval",
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

        if "provider" in body:
            provider = self._nonempty_string(body["provider"], "provider").lower()
            if provider not in {"9router", "nine-router", "auto", NINE_ROUTER_PROVIDER}:
                raise ConfigAPIError(
                    "provider must be nine-router",
                    code="unsupported_provider",
                )
            touched = True
        if "model" in body:
            self._set_nested(config, ("model", "default"), self._nonempty_string(body["model"], "model"))
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
        soul = body.get("soul", body.get("system_prompt", _MISSING))
        if soul is not _MISSING:
            self._write_text(self.root_profile / "SOUL.md", soul, field="soul")

        selected_model = body.get("model") if "model" in body else None
        normalize_nine_router_config(config, selected_model)
        if touched or soul is not _MISSING:
            self._write_config(config)
        return self._describe(config)

    async def install_skill(self, body: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(body, Mapping):
            raise ConfigAPIError("request body must be an object")

        self.root_profile.mkdir(parents=True, exist_ok=True)
        if "content" in body:
            skill_id = self._skill_id(body.get("skill_id") or body.get("name"))
            content = self._text_value(body["content"], field="content", max_chars=MAX_TEXT_CHARS)
            skill_dir = self.root_profile / "skills" / skill_id
            skill_dir.mkdir(parents=True, exist_ok=True)
            self._write_text(skill_dir / "SKILL.md", content, field="content")
            return self.list_skills()

        source = self._nonempty_string(body.get("source"), "source")
        command = [self._hermes_binary(), "skills", "install", source, "--yes"]
        if body.get("name"):
            command.extend(["--name", self._skill_id(body["name"])])
        if body.get("category"):
            command.extend(["--category", self._safe_category(body["category"])])
        if bool(body.get("force", False)):
            command.append("--force")
        result = await self._run_command(
            command,
            timeout_seconds=int(body.get("timeout_seconds") or DEFAULT_INSTALL_TIMEOUT_SECONDS),
        )
        if result["exit_code"] != 0:
            raise ConfigAPIError(
                "skill install failed",
                code="skill_install_failed",
                status=422,
            )
        payload = self.list_skills()
        payload["command"] = result
        return payload

    def list_skills(self) -> dict[str, Any]:
        config = self._read_config()
        skills = self._scan_skills(config)
        disabled = self._disabled_skills(config)
        for skill in skills:
            skill["enabled"] = skill["skill_id"] not in disabled
        return {
            "object": "hermes.global_skills",
            "root_profile": str(self.root_profile),
            "skills": skills,
        }

    def delete_skill(self, raw_skill_id: Any) -> dict[str, Any]:
        skill_id = self._skill_id(raw_skill_id)
        config = self._read_config()
        target = self._find_owned_skill_dir(skill_id)
        if target is None:
            external = self._find_external_skill_dir(skill_id, config)
            if external is not None:
                raise ConfigAPIError(
                    "external skills cannot be deleted from the shared profile",
                    code="external_skill_delete_forbidden",
                    status=403,
                )
            raise ConfigAPIError("skill not found", code="skill_not_found", status=404)

        shutil.rmtree(target)
        disabled = self._get_nested(config, ("skills", "disabled"), []) or []
        if isinstance(disabled, list):
            next_disabled = [item for item in disabled if str(item).strip() != skill_id]
            if len(next_disabled) != len(disabled):
                self._set_nested(config, ("skills", "disabled"), next_disabled)
                self._write_config(config)
        return self.list_skills()

    def ensure_default_soul(self, *, overwrite: bool = False) -> None:
        path = self.root_profile / "SOUL.md"
        if overwrite or not path.is_file() or not path.read_text(encoding="utf-8").strip():
            self._write_text(path, DEFAULT_SOUL, field="soul")

    def _describe(self, config: Mapping[str, Any]) -> dict[str, Any]:
        normalized = self._sanitize_config_value(config, "config")
        normalize_nine_router_config(normalized)
        effort = str(self._get_nested(config, ("agent", "reasoning_effort"), "medium") or "medium").lower()
        approval = self._get_nested(config, ("approvals", "mode"), "manual")
        soul = self._read_text(self.root_profile / "SOUL.md")
        return {
            "object": "hermes.global_config",
            "root_profile": str(self.root_profile),
            "config_path": str(self.root_profile / "config.yaml"),
            "soul_path": str(self.root_profile / "SOUL.md"),
            "provider": "nine-router",
            "model": self._get_nested(normalized, ("model", "default"), NINE_ROUTER_DEFAULT_MODEL),
            "base_url": NINE_ROUTER_API_BASE_URL,
            "reasoning": effort != "none",
            "effort": effort,
            "reasoning_effort": effort,
            "approval_mode": "off" if approval is False or str(approval).lower() == "off" else "on",
            "skills_write_approval": self._coerce_bool(
                self._get_nested(config, ("skills", "write_approval"), True),
                field="skills.write_approval",
            ),
            "memory_write_approval": self._coerce_bool(
                self._get_nested(config, ("memory", "write_approval"), True),
                field="memory.write_approval",
            ),
            "system_prompt": soul,
            "soul": soul,
            "router": {
                "provider": NINE_ROUTER_PROVIDER,
                "base_url": NINE_ROUTER_API_BASE_URL,
                "default_model": NINE_ROUTER_DEFAULT_MODEL,
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

    def _scan_skills(self, config: Mapping[str, Any]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        skills: list[dict[str, Any]] = []
        self._scan_skill_dir(self.root_profile / "skills", skills, seen)
        for directory in self._external_skill_dirs(config):
            self._scan_skill_dir(directory, skills, seen)
        return sorted(skills, key=lambda item: (item["category"], item["name"]))

    def _scan_skill_dir(
        self,
        root: Path,
        skills: list[dict[str, Any]],
        seen: set[str],
    ) -> None:
        if not root.is_dir():
            return
        for skill_file in sorted(root.rglob("SKILL.md")):
            frontmatter = self._read_skill_frontmatter(skill_file)
            relative_parent = skill_file.parent.relative_to(root)
            skill_id = str(frontmatter.get("name") or skill_file.parent.name).strip()
            if not skill_id or skill_id in seen:
                continue
            seen.add(skill_id)
            skills.append(
                {
                    "skill_id": skill_id,
                    "name": skill_id,
                    "path": str(skill_file.parent),
                    "relative_path": str(relative_parent),
                    "description": str(frontmatter.get("description") or ""),
                    "category": str(
                        frontmatter.get("category")
                        or (relative_parent.parts[0] if len(relative_parent.parts) > 1 else "skills")
                    ),
                    "installed": True,
                    "enabled": True,
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
            except ConfigAPIError:
                continue
        return disabled

    def _find_owned_skill_dir(self, skill_id: str) -> Path | None:
        return self._find_skill_dir(self.root_profile / "skills", skill_id)

    def _find_external_skill_dir(self, skill_id: str, config: Mapping[str, Any]) -> Path | None:
        for directory in self._external_skill_dirs(config):
            found = self._find_skill_dir(directory, skill_id)
            if found is not None:
                return found
        return None

    def _find_skill_dir(self, root: Path, skill_id: str) -> Path | None:
        if not root.is_dir():
            return None
        for skill_file in sorted(root.rglob("SKILL.md")):
            frontmatter = self._read_skill_frontmatter(skill_file)
            candidates = {
                str(frontmatter.get("name") or "").strip(),
                str(skill_file.parent.name).strip(),
                str(skill_file.parent.relative_to(root)).strip(),
            }
            if skill_id in candidates:
                return skill_file.parent
        return None

    def _external_skill_dirs(self, config: Mapping[str, Any]) -> list[Path]:
        raw = self._get_nested(config, ("skills", "external_dirs"), []) or []
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            return []
        result = []
        for item in raw:
            expanded = os.path.expandvars(os.path.expanduser(str(item)))
            path = Path(expanded)
            if not path.is_absolute():
                path = self.root_profile / path
            result.append(path)
        return result

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

    async def _run_command(self, command: list[str], *, timeout_seconds: int) -> dict[str, Any]:
        timeout_seconds = max(1, min(timeout_seconds, MAX_INSTALL_TIMEOUT_SECONDS))
        env = os.environ.copy()
        env["HERMES_HOME"] = str(self.root_profile)
        env.setdefault("HOME", str(self.root_profile.parent))
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(self.root_profile.parent),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise ConfigAPIError(
                "skill install timed out",
                code="skill_install_timeout",
                status=504,
            )
        return {
            "command": list(command),
            "exit_code": int(proc.returncode or 0),
            "stdout": stdout.decode("utf-8", "replace"),
            "stderr": stderr.decode("utf-8", "replace"),
        }

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
        if effort not in {"none", "minimal", "low", "medium", "high", "xhigh"}:
            raise ConfigAPIError(
                "effort must be one of: minimal, low, medium, high, xhigh",
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

    def _skill_id(self, value: Any) -> str:
        skill_id = str(value or "").strip()
        if not SKILL_ID_RE.match(skill_id) or ".." in skill_id:
            raise ConfigAPIError("skill_id is invalid", code="invalid_skill_id")
        return skill_id

    def _safe_category(self, value: Any) -> str:
        category = str(value or "").strip().strip("/")
        if not category or any(part in {"", ".", ".."} for part in category.split("/")):
            raise ConfigAPIError("category is invalid", code="invalid_skill_category")
        if not all(SKILL_ID_RE.match(part) for part in category.split("/")):
            raise ConfigAPIError("category is invalid", code="invalid_skill_category")
        return category

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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

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
