"""AgentSkills methods for the Hermes runtime adapter."""

from .hermes_support import (
    AgentAPIError,
    Any,
    BIG_BROTHER_SKILL_CATEGORY,
    CUSTOM_SKILL_CATEGORY,
    MAX_TEXT_CHARS,
    Mapping,
    Path,
    hashlib,
    json,
    os,
    shutil,
    uuid,
    yaml,
)


class AgentSkillsMixin:
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
