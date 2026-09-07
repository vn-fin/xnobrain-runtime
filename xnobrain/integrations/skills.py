"""CommonSkills behavior for the global Hermes profile."""

from .config_support import (
    CUSTOM_SKILL_CATEGORY,
    DEFAULT_INSTALL_TIMEOUT_SECONDS,
    MAX_INSTALL_TIMEOUT_SECONDS,
    MAX_TEXT_CHARS,
    SKILL_ID_RE,
    Any,
    ConfigAPIError,
    Mapping,
    Path,
    asyncio,
    hashlib,
    os,
    shutil,
    time,
    yaml,
)


class CommonSkillsMixin:
    async def install_skill(self, body: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(body, Mapping):
            raise ConfigAPIError("request body must be an object")

        self.root_profile.mkdir(parents=True, exist_ok=True)
        before = self._skill_files()
        enable = bool(body.get("enable", False))
        if "content" in body:
            skill_id = self._skill_id(body.get("skill_id") or body.get("name"))
            content = self._text_value(body["content"], field="content", max_chars=MAX_TEXT_CHARS)
            category = self._safe_category(body.get("category") or CUSTOM_SKILL_CATEGORY)
            skill_dir = self.root_profile / "skills"
            if category:
                skill_dir /= category
            skill_dir /= skill_id
            skill_dir.mkdir(parents=True, exist_ok=True)
            self._write_text(skill_dir / "SKILL.md", content, field="content")
            after = self._skill_files()
            self._snapshot_skill_changes(before, after)
            self._set_skills_enabled({skill_id}, enable)
            return self.list_skills()

        source = self._nonempty_string(body.get("source"), "source")
        command = [self._hermes_binary(), "skills", "install", source, "--yes"]
        if body.get("name"):
            command.extend(["--name", self._skill_id(body["name"])])
        command.extend(
            [
                "--category",
                self._safe_category(body.get("category") or CUSTOM_SKILL_CATEGORY),
            ]
        )
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
        after = self._skill_files()
        self._snapshot_skill_changes(before, after)
        changed = self._changed_skill_ids(before, after)
        if body.get("name"):
            changed.add(self._skill_id(body["name"]))
        self._set_skills_enabled(changed, enable)
        return self.list_skills()

    def list_skills(self) -> dict[str, Any]:
        config = self._read_config()
        skills = self._scan_skills(config)
        disabled = self._disabled_skills(config)
        for skill in skills:
            skill["enabled"] = skill["skill_id"] not in disabled
        return {
            "object": "xnobrain.global_skills",
            "root_profile": str(self.root_profile),
            "skills": skills,
        }

    def set_skill_enabled(
        self,
        raw_skill_id: Any,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(body, Mapping):
            raise ConfigAPIError("request body must be an object", code="invalid_skill_request")
        if "enabled" not in body:
            raise ConfigAPIError("enabled is required", code="invalid_skill_request")

        skill_id = self._skill_id(raw_skill_id)
        config = self._read_config()
        if (
            self._find_owned_skill_dir(skill_id) is None
            and self._find_external_skill_dir(skill_id, config) is None
        ):
            raise ConfigAPIError("skill not found", code="skill_not_found", status=404)

        disabled = self._disabled_skills(config)
        if self._coerce_bool(body["enabled"], field="enabled"):
            disabled.discard(skill_id)
        else:
            disabled.add(skill_id)
        self._snapshot_config()
        self._set_nested(config, ("skills", "disabled"), sorted(disabled))
        self._write_config(config)
        return self.list_skills()

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

    def _skill_files(self) -> dict[str, bytes]:
        root = self.root_profile / "skills"
        if not root.is_dir():
            return {}
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("SKILL.md")
            if path.is_file()
        }

    def _snapshot_skill_changes(
        self, before: Mapping[str, bytes], after: Mapping[str, bytes]
    ) -> None:
        for relative, current in after.items():
            previous = before.get(relative)
            if previous == current:
                continue
            payload = previous if previous is not None else current
            digest = hashlib.sha256(payload).hexdigest()
            target = self.root_profile / "snapshots" / "skills" / Path(relative).parent
            snapshot = target / f"{time.time_ns()}-{digest[:12]}.md"
            self._atomic_write(snapshot, payload, mode=0o440)

    def _changed_skill_ids(
        self,
        before: Mapping[str, bytes],
        after: Mapping[str, bytes],
    ) -> set[str]:
        changed = set()
        skills_root = self.root_profile / "skills"
        for relative, payload in after.items():
            if before.get(relative) == payload:
                continue
            path = skills_root / relative
            frontmatter = self._read_skill_frontmatter(path)
            changed.add(str(frontmatter.get("name") or path.parent.name).strip())
        return {item for item in changed if item}

    def _set_skills_enabled(self, skill_ids: set[str], enabled: bool) -> None:
        if not skill_ids:
            return
        config = self._read_config()
        disabled = self._disabled_skills(config)
        previous = set(disabled)
        if enabled:
            disabled.difference_update(skill_ids)
        else:
            disabled.update(skill_ids)
        if disabled == previous:
            return
        self._snapshot_config()
        self._set_nested(config, ("skills", "disabled"), sorted(disabled))
        self._write_config(config)

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
                        or (
                            relative_parent.parts[0] if len(relative_parent.parts) > 1 else "skills"
                        )
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
        # Packaged Hermes skills are opt-in for API catalog projection. The
        # Runtime image sets HERMES_INSTALL_DIR for execution, but tests and
        # isolated profiles must not silently inherit the installation-wide
        # catalog. Production profile templates explicitly enable it.
        include_packaged = str(
            os.environ.get("RUNTIME_INCLUDE_PACKAGED_SKILLS") or ""
        ).strip().lower() in {"1", "true", "yes", "on"}
        install_root = Path(str(os.environ.get("HERMES_INSTALL_DIR") or "").strip())
        packaged_skills = (
            install_root / "skills"
            if include_packaged and str(install_root) not in {"", "."}
            else None
        )
        if packaged_skills is not None and packaged_skills.is_dir():
            result.append(packaged_skills)
        for item in raw:
            expanded = os.path.expandvars(os.path.expanduser(str(item)))
            path = Path(expanded)
            if not path.is_absolute():
                path = self.root_profile / path
            if path not in result:
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
