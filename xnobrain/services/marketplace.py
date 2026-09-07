"""Safe marketplace profile export and installation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

import yaml

from .base import ServiceError

_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b(?:ghp_|github_pat_|sk-)[A-Za-z0-9_-]{16,}\b"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)"
        r"\s*[:=]\s*['\"]?[A-Za-z0-9/+_.-]{12,}"
    ),
)

MAX_EXPORT_FILES = 202
MAX_EXPORT_FILE_BYTES = 1_000_000
MAX_EXPORT_TOTAL_BYTES = 10_000_000
MAX_EXPORT_PATH_DEPTH = 12
MAX_SKILLS = 100
MAX_ASSETS = 100
_ALLOWED_SKILL_TREES = {
    "references": {".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".csv"},
    "scripts": {".py", ".sh", ".js", ".mjs", ".cjs", ".ts", ".ps1"},
    "assets": {".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".csv", ".j2", ".tmpl"},
}
_EXCLUDED_PARTS = {
    ".cache",
    ".env",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "cache",
    "caches",
    "conversations",
    "credentials",
    "history",
    "logs",
    "memories",
    "memory",
    "node_modules",
    "sessions",
}
_EXCLUSION_CATEGORIES = [
    "credentials",
    "conversations_history",
    "personal_memory",
    "environment_files",
    "caches",
    "private_workspace",
    "runtime_state",
]


class MarketplaceService:
    def __init__(self, repository, agents):
        self.repository = repository
        self.agents = agents

    @staticmethod
    def digest(package: Mapping[str, Any]) -> str:
        value = {
            "definition": package["definition"],
            "permissions": package.get("requested_permissions", []),
            "compatibility": package.get("compatibility", {}),
            "license": package.get("license", ""),
        }
        payload = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(payload).hexdigest()

    def export(self, agent_id: str, license_name: str) -> dict[str, Any]:
        """Build a bounded public package from one existing local profile."""
        profile = self._owned_profile(agent_id)
        config = self._read_yaml_object(profile / "config.yaml", required=False)
        metadata = self._read_json_object(profile / "agent.json")
        soul = self._read_public_text(profile, Path("SOUL.md"), required=True, max_bytes=100_000)
        instructions = self._read_public_text(
            profile,
            Path("workspace") / "AGENTS.md",
            required=True,
        )
        credential_values = self._credential_values(profile, config)
        self._reject_secret_content(soul, credential_values)
        self._reject_secret_content(instructions, credential_values)

        files = [
            self._manifest("SOUL.md", soul, "soul"),
            self._manifest("workspace/AGENTS.md", instructions, "instructions"),
        ]
        skills, assets, skill_files = self._export_skills(profile, config, credential_values)
        files.extend(skill_files)
        self._enforce_export_limits(files)

        model_config = config.get("model")
        model = self._bounded_scalar(
            model_config.get("default") if isinstance(model_config, Mapping) else None,
            "auto",
            256,
        )
        agent_config = config.get("agent")
        effort = self._bounded_scalar(
            agent_config.get("reasoning_effort") if isinstance(agent_config, Mapping) else None,
            "medium",
            32,
        )
        public_config = {
            "display_name": self._bounded_scalar(
                metadata.get("display_name") or metadata.get("title"),
                agent_id,
                200,
            ),
            "description": self._bounded_scalar(metadata.get("description"), "", 4_000),
            "model": model,
            "reasoning_effort": effort,
        }
        tool_requirements = self._safe_references(config.get("toolsets"), limit=100)
        mcp = config.get("mcp_servers")
        mcp_requirements = self._safe_references(
            sorted(mcp) if isinstance(mcp, Mapping) else [],
            limit=100,
        )
        definition = {
            "soul": soul,
            "public_config": public_config,
            "prompts": {"AGENTS.md": instructions},
            "skills": skills,
            "assets": assets,
            "tool_requirements": tool_requirements,
            "mcp_requirements": mcp_requirements,
            "model_slots": [] if model == "auto" else [model],
        }
        package = {
            "schema_version": 1,
            "source_agent_id": agent_id,
            "definition": definition,
            # Requirements are declarations only. Runtime never grants permissions
            # while exporting a publisher-owned profile.
            "requested_permissions": [],
            "compatibility": {
                "runtime_api": "v1",
                "marketplace_package_schema": 1,
            },
            "license": license_name,
            "files": files,
            "limits": {
                "max_files": MAX_EXPORT_FILES,
                "max_file_bytes": MAX_EXPORT_FILE_BYTES,
                "max_total_bytes": MAX_EXPORT_TOTAL_BYTES,
                "file_count": len(files),
                "total_bytes": sum(item["size"] for item in files),
            },
            "exclusions": {
                "categories": _EXCLUSION_CATEGORIES,
                "omitted_file_count": self._omitted_file_count(profile, files),
            },
        }
        package["digest"] = self.digest(package)
        return package

    def _owned_profile(self, agent_id: str) -> Path:
        candidate_id = self.repository._id(agent_id, "agent id")
        native = self.repository.profiles_root / candidate_id
        if native.is_symlink():
            raise ServiceError(
                "marketplace export rejected an unsafe profile",
                status=422,
                code="marketplace_export_rejected",
            )
        legacy_value = getattr(self.agents, "legacy_agents_root", None)
        legacy_root = Path(legacy_value) if legacy_value else None
        if legacy_root is not None and (
            (legacy_root / candidate_id).is_symlink()
            or (legacy_root / candidate_id / ".profile").is_symlink()
        ):
            raise ServiceError(
                "marketplace export rejected an unsafe profile",
                status=422,
                code="marketplace_export_rejected",
            )
        try:
            profile = Path(self.agents.profile_path(candidate_id))
        except (OSError, ValueError) as exc:
            raise ServiceError("agent not found", status=404, code="not_found") from exc
        if profile.is_symlink() or not profile.is_dir():
            raise ServiceError("agent not found", status=404, code="not_found")
        return profile.resolve()

    def _export_skills(
        self,
        profile: Path,
        config: Mapping[str, Any],
        credential_values: set[str],
    ) -> tuple[dict[str, str], dict[str, str], list[dict[str, Any]]]:
        root = profile / "skills"
        if not root.exists():
            return {}, {}, []
        if root.is_symlink() or not root.is_dir():
            self._reject_export()
        self._reject_tree_symlinks(root)
        disabled_config = config.get("skills")
        disabled = {
            str(item)
            for item in (
                disabled_config.get("disabled", []) if isinstance(disabled_config, Mapping) else []
            )
        }
        skill_paths = sorted(
            (path for path in root.rglob("SKILL.md") if path.is_file()),
            key=lambda item: item.as_posix(),
        )
        if len(skill_paths) > MAX_SKILLS:
            self._reject_limit()
        skill_directories = {path.parent for path in skill_paths}
        for path in root.rglob("*"):
            if path.is_symlink() or not path.is_file() or path.name == "SKILL.md":
                continue
            if not any(skill_dir in path.parents for skill_dir in skill_directories):
                self._reject_export()
        skills: dict[str, str] = {}
        assets: dict[str, str] = {}
        files: list[dict[str, Any]] = []
        folded: set[str] = set()
        for skill_file in skill_paths:
            skill_dir = skill_file.parent
            relative_skill = skill_dir.relative_to(root)
            skill_ref = self._safe_relative(relative_skill)
            if skill_dir.name in disabled or skill_ref in disabled:
                continue
            for parent in skill_dir.parents:
                if parent == root:
                    break
                if (parent / "SKILL.md").is_file():
                    self._reject_export()
            content = self._read_public_text(root, relative_skill / "SKILL.md", required=True)
            self._reject_secret_content(content, credential_values)
            self._add_unique(folded, f"skills/{skill_ref}/SKILL.md")
            skills[skill_ref] = content
            files.append(self._manifest(f"skills/{skill_ref}/SKILL.md", content, "skill"))
            for path in sorted(skill_dir.rglob("*"), key=lambda item: item.as_posix()):
                if not path.is_file() or path == skill_file:
                    continue
                relative = path.relative_to(skill_dir)
                parts = relative.parts
                if self._excluded(relative) or not parts or parts[0] not in _ALLOWED_SKILL_TREES:
                    continue
                if path.suffix.lower() not in _ALLOWED_SKILL_TREES[parts[0]]:
                    continue
                safe_relative = self._safe_relative(relative)
                package_path = f"skills/{skill_ref}/{safe_relative}"
                self._add_unique(folded, package_path)
                text = self._read_public_text(skill_dir, relative, required=True)
                self._reject_secret_content(text, credential_values)
                assets[package_path] = text
                kind = {"references": "reference", "scripts": "script", "assets": "asset"}[parts[0]]
                files.append(self._manifest(package_path, text, kind))
                if len(assets) > MAX_ASSETS:
                    self._reject_limit()
        return skills, assets, files

    @staticmethod
    def _reject_tree_symlinks(root: Path) -> None:
        for directory, directories, filenames in os.walk(root, followlinks=False):
            current = Path(directory)
            for name in [*directories, *filenames]:
                if (current / name).is_symlink():
                    MarketplaceService._reject_export()

    @staticmethod
    def _safe_relative(path: Path) -> str:
        value = PurePosixPath(path.as_posix())
        if (
            value.is_absolute()
            or not value.parts
            or len(value.parts) > MAX_EXPORT_PATH_DEPTH
            or any(not _SAFE.fullmatch(part) or part in {".", ".."} for part in value.parts)
        ):
            MarketplaceService._reject_export()
        return value.as_posix()

    @staticmethod
    def _excluded(path: Path) -> bool:
        return any(
            part.casefold() in _EXCLUDED_PARTS or part.startswith(".") for part in path.parts
        )

    @staticmethod
    def _add_unique(folded: set[str], path: str) -> None:
        key = path.casefold()
        if key in folded:
            MarketplaceService._reject_export()
        folded.add(key)

    @staticmethod
    def _manifest(path: str, content: str, kind: str) -> dict[str, Any]:
        payload = content.encode("utf-8")
        return {
            "path": path,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
            "kind": kind,
        }

    @staticmethod
    def _enforce_export_limits(files: list[Mapping[str, Any]]) -> None:
        if len(files) > MAX_EXPORT_FILES:
            MarketplaceService._reject_limit()
        total = 0
        for item in files:
            size = int(item["size"])
            if size > MAX_EXPORT_FILE_BYTES:
                MarketplaceService._reject_limit()
            total += size
            if total > MAX_EXPORT_TOTAL_BYTES:
                MarketplaceService._reject_limit()

    @staticmethod
    def _read_public_text(
        root: Path,
        relative: Path,
        *,
        required: bool,
        max_bytes: int = MAX_EXPORT_FILE_BYTES,
    ) -> str:
        path = root / relative
        if path.is_symlink():
            MarketplaceService._reject_export()
        try:
            resolved = path.resolve(strict=True)
        except FileNotFoundError:
            if required:
                raise ServiceError(
                    "marketplace export requires a complete agent definition",
                    status=422,
                    code="marketplace_export_incomplete",
                )
            return ""
        resolved_root = root.resolve()
        if resolved_root not in resolved.parents or not resolved.is_file():
            MarketplaceService._reject_export()
        descriptor = None
        try:
            descriptor = os.open(resolved, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            details = os.fstat(descriptor)
            if not stat.S_ISREG(details.st_mode) or details.st_size > max_bytes:
                MarketplaceService._reject_limit()
            chunks = []
            size = 0
            while chunk := os.read(descriptor, min(64 * 1024, max_bytes + 1 - size)):
                chunks.append(chunk)
                size += len(chunk)
                if size > max_bytes:
                    MarketplaceService._reject_limit()
            payload = b"".join(chunks)
        except OSError as exc:
            raise ServiceError(
                "marketplace export could not read public profile content",
                status=422,
                code="marketplace_export_rejected",
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
        if len(payload) > max_bytes:
            MarketplaceService._reject_limit()
        try:
            return payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ServiceError(
                "marketplace export supports UTF-8 public files only",
                status=422,
                code="marketplace_export_rejected",
            ) from exc

    @classmethod
    def _read_yaml_object(cls, path: Path, *, required: bool) -> dict[str, Any]:
        if not path.exists() and not required:
            return {}
        text = cls._read_public_text(path.parent, Path(path.name), required=required)
        try:
            value = yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            raise ServiceError(
                "marketplace export rejected invalid public configuration",
                status=422,
                code="marketplace_export_rejected",
            ) from exc
        if not isinstance(value, Mapping):
            cls._reject_export()
        return dict(value)

    @classmethod
    def _read_json_object(cls, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        text = cls._read_public_text(path.parent, Path(path.name), required=False)
        try:
            value = json.loads(text)
        except (TypeError, ValueError) as exc:
            raise ServiceError(
                "marketplace export rejected invalid agent metadata",
                status=422,
                code="marketplace_export_rejected",
            ) from exc
        if not isinstance(value, Mapping):
            cls._reject_export()
        return dict(value)

    @staticmethod
    def _bounded_scalar(value: Any, default: str, limit: int) -> str:
        if value is None:
            return default
        if not isinstance(value, (str, int, float, bool)):
            MarketplaceService._reject_export()
        result = str(value).strip()
        if len(result) > limit or "\x00" in result:
            MarketplaceService._reject_export()
        return result or default

    @staticmethod
    def _safe_references(value: Any, *, limit: int) -> list[str]:
        if value is None:
            return []
        if isinstance(value, Mapping):
            if any(not isinstance(key, str) for key in value):
                MarketplaceService._reject_export()
            value = [key for key, enabled in value.items() if enabled]
        if not isinstance(value, (list, tuple, set)) or len(value) > limit:
            MarketplaceService._reject_export()
        if any(not isinstance(item, str) for item in value):
            MarketplaceService._reject_export()
        result = sorted({item.strip() for item in value})
        if any(not _SAFE_REFERENCE.fullmatch(item) for item in result):
            MarketplaceService._reject_export()
        return result

    @classmethod
    def _credential_values(cls, profile: Path, config: Mapping[str, Any]) -> set[str]:
        values: set[str] = set()

        def visit(value: Any, sensitive: bool = False) -> None:
            if isinstance(value, Mapping):
                for key, item in value.items():
                    key_sensitive = sensitive or any(
                        marker in str(key).casefold()
                        for marker in (
                            "authorization",
                            "cookie",
                            "credential",
                            "password",
                            "secret",
                            "token",
                            "api_key",
                            "apikey",
                        )
                    )
                    visit(item, key_sensitive)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    visit(item, sensitive)
            elif sensitive and isinstance(value, str) and len(value.strip()) >= 8:
                values.add(value.strip())

        visit(config)
        for filename in (
            ".env",
            "auth.json",
            "credentials.env",
            "oauth.json",
            "secrets.json",
            "tokens.json",
        ):
            path = profile / filename
            if path.is_symlink() or not path.is_file():
                continue
            try:
                if path.stat().st_size > MAX_EXPORT_FILE_BYTES:
                    continue
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if path.suffix == ".json":
                try:
                    visit(json.loads(text), sensitive=True)
                except (TypeError, ValueError):
                    continue
                continue
            for line in text.splitlines():
                if "=" not in line or line.lstrip().startswith("#"):
                    continue
                value = line.split("=", 1)[1].strip().strip("'\"")
                if len(value) >= 8:
                    values.add(value)
        return values

    @staticmethod
    def _omitted_file_count(profile: Path, files: list[Mapping[str, Any]]) -> int:
        included = {str(item["path"]).casefold() for item in files}
        count = 0
        for directory, directories, filenames in os.walk(profile, followlinks=False):
            current = Path(directory)
            count += sum((current / name).is_symlink() for name in directories)
            for name in filenames:
                path = current / name
                try:
                    relative = path.relative_to(profile).as_posix().casefold()
                except ValueError:
                    count += 1
                    continue
                if relative not in included:
                    count += 1
        return count

    @staticmethod
    def _reject_secret_content(content: str, credential_values: set[str]) -> None:
        if any(pattern.search(content) for pattern in _SECRET_PATTERNS) or any(
            value in content for value in credential_values
        ):
            raise ServiceError(
                "marketplace export rejected credential-like public content",
                status=422,
                code="marketplace_export_credentials_detected",
            )

    @staticmethod
    def _reject_export() -> None:
        raise ServiceError(
            "marketplace export rejected unsafe profile content",
            status=422,
            code="marketplace_export_rejected",
        )

    @staticmethod
    def _reject_limit() -> None:
        raise ServiceError(
            "marketplace export exceeds package limits",
            status=413,
            code="marketplace_export_too_large",
        )

    def install(self, package: Mapping[str, Any]) -> dict[str, Any]:
        definition = dict(package.get("definition") or {})
        expected = str(package.get("digest") or "")
        if self.digest(package) != expected:
            raise ServiceError(
                "marketplace package digest mismatch", status=422, code="package_digest_mismatch"
            )
        if package.get("status") not in {"pending", "installed"}:
            raise ServiceError(
                "installation is unavailable", status=409, code="installation_unavailable"
            )
        soul = str(definition.get("soul") or "")
        skills = dict(definition.get("skills") or {})
        assets = dict(definition.get("assets") or {})
        if not soul or any(
            not _SAFE.fullmatch(str(k)) or len(str(v)) > 1_000_000
            for k, v in {**skills, **assets}.items()
        ):
            raise ServiceError("marketplace package is unsafe", status=422, code="package_rejected")
        profile_id = "market-" + str(package["id"]).replace("inst_", "")[:24]
        final = self.repository.profile_path(profile_id)
        if final.exists():
            raise ServiceError(
                "installation profile already exists", status=409, code="installation_exists"
            )
        stage = Path(tempfile.mkdtemp(prefix=".market-", dir=self.repository.profiles_root))
        installed = False
        try:
            (stage / "workspace").mkdir()
            (stage / "skills" / "custom").mkdir(parents=True)
            (stage / "SOUL.md").write_text(soul, encoding="utf-8")
            public = dict(definition.get("public_config") or {})
            allowed = {"model", "reasoning", "display_name", "description"}
            config = {k: v for k, v in public.items() if k in allowed}
            config["xnobrain"] = {
                "marketplace_installation_id": package["id"],
                "package_digest": expected,
                "update_policy": package.get("update_policy", "pinned"),
            }
            self.repository.atomic_yaml(stage / "config.yaml", config)
            for name, content in skills.items():
                d = stage / "skills" / "custom" / name
                d.mkdir()
                (d / "SKILL.md").write_text(str(content), encoding="utf-8")
            for name, content in assets.items():
                d = stage / "workspace" / "assets"
                d.mkdir(exist_ok=True)
                (d / name).write_text(str(content), encoding="utf-8")
            # Mutable customer state starts empty and is never sourced from publisher bytes.
            (stage / "memories").mkdir()
            os.replace(stage, final)
            installed = True
        finally:
            if not installed and stage.exists():
                __import__("shutil").rmtree(stage)
        self.agents.sync_profiles_registry()
        return {
            "installation_id": package["id"],
            "local_profile_id": profile_id,
            "digest": expected,
            "ownership": "customer",
            "execution_mode": "package_visible",
        }

    def update(self, package: Mapping[str, Any], local_profile_id: str) -> dict[str, Any]:
        profile = self.repository.profile_path(local_profile_id)
        if not profile.is_dir():
            raise ServiceError("installation profile not found", status=404, code="not_found")
        old_config = self.repository._read_yaml(profile / "config.yaml") or {}
        memory = profile / "memories"
        workspace = profile / "workspace"
        snapshots = {
            p.relative_to(profile): p.read_bytes()
            for root in (memory, workspace)
            if root.exists()
            for p in root.rglob("*")
            if p.is_file() and not p.is_symlink()
        }
        if self.digest(package) != package.get("digest"):
            raise ServiceError(
                "marketplace package digest mismatch", status=422, code="package_digest_mismatch"
            )
        definition = dict(package["definition"])
        (profile / "SOUL.md").write_text(str(definition["soul"]), encoding="utf-8")
        public = {
            k: v
            for k, v in dict(definition.get("public_config") or {}).items()
            if k in {"model", "reasoning", "display_name", "description"}
        }
        public["xnobrain"] = dict(old_config.get("xnobrain") or {})
        public["xnobrain"]["package_digest"] = package["digest"]
        self.repository.atomic_yaml(profile / "config.yaml", public)
        for relative, payload in snapshots.items():
            target = profile / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            self.repository.atomic_write(target, payload)
        return {
            "installation_id": package["id"],
            "local_profile_id": local_profile_id,
            "digest": package["digest"],
            "updated": True,
        }

    def uninstall(self, local_profile_id: str) -> dict[str, Any]:
        target = self.repository.soft_delete_profile(local_profile_id)
        self.agents.sync_profiles_registry()
        return {
            "local_profile_id": local_profile_id,
            "status": "uninstalled",
            "recoverable_path": target.name,
        }
