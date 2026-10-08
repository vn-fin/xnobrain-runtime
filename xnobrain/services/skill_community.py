"""Admitted Skill delivery with durable, recipient-bound receipts."""

import base64
import fcntl
import hashlib
import json
import os
from pathlib import PurePosixPath

import yaml

from ..repositories.base import StoreError
from .portability import PortabilityService


class SkillCommunityService:
    def __init__(self, repository):
        self.repository = repository
        self.root = repository.data_dir / "community-skill-deliveries"
        self.root.mkdir(exist_ok=True, mode=0o700)

    def _paths(self, context, operation):
        self.repository._id(operation, "operation id")
        if not context.subject:
            raise StoreError("Verified recipient required", status=403, code="permission_denied")
        recipient = json.dumps(PortabilityService.owner_record(context), sort_keys=True)
        key = hashlib.sha256((recipient + "\x00" + operation).encode()).hexdigest()
        return self.root / key

    def receipt(self, context, operation):
        root = self._paths(context, operation)
        receipt = root / "receipt.json"
        if root.is_symlink() or receipt.is_symlink():
            raise StoreError("Unsafe receipt", status=409, code="delivery_conflict")
        if not receipt.is_file():
            # Rename can commit before the central receipt write. The prepared
            # target and its marker allow read-only recovery without new bytes.
            intent = root / "intent.json"
            if not intent.is_file() or intent.is_symlink():
                raise StoreError("Receipt not found", status=404, code="not_found")
            saved = json.loads(intent.read_text())
            profile = self._profile(context, saved["profile"])
            marker = profile / "skills" / saved["skill"] / ".community-receipt.json"
            self._safe_path(profile, marker)
            if not marker.is_file():
                raise StoreError("Receipt not found", status=404, code="not_found")
            committed = json.loads(marker.read_text())
            if committed != saved:
                raise StoreError("Receipt not found", status=404, code="not_found")
            return saved["result"]
        return json.loads(receipt.read_text())["result"]

    @staticmethod
    def _safe_path(root, path):
        if not path.is_relative_to(root):
            raise StoreError("Invalid skill path", code="invalid_path")
        for node in (path, *path.parents):
            if node.is_symlink():
                raise StoreError("Unsafe skill path", code="invalid_path")
            if node == root:
                break

    def _profile(self, context, profile_id):
        profile = self.repository.profile_path(profile_id)
        marker = profile / ".community-profile-owner.json"
        self._safe_path(self.repository.profiles_root, marker)
        try:
            owner = json.loads(marker.read_text())
        except (OSError, ValueError):
            owner = None
        if owner != PortabilityService.owner_record(context):
            raise StoreError("Profile is unavailable", status=403, code="permission_denied")
        return profile

    def install(self, context, payload):
        operation = payload["operation_id"]
        root = self._paths(context, operation)
        if root.is_symlink():
            raise StoreError("Unsafe delivery", code="invalid_path")
        root.mkdir(exist_ok=True, mode=0o700)
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        descriptor = os.open(self.root / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            intent = root / "intent.json"
            if intent.exists():
                previous = json.loads(intent.read_text())
                if previous["fingerprint"] != fingerprint:
                    raise StoreError("Operation changed", status=409, code="delivery_conflict")
                try:
                    return self.receipt(context, operation)
                except StoreError as error:
                    if error.status != 404:
                        raise
            profile = self._profile(context, payload["target_profile_id"])
            skill = self.repository._id(
                payload["rename_to"]
                if payload["collision_resolution"] == "rename"
                else payload["skill_id"],
                "skill id",
            )
            target = profile / "skills" / skill
            self._safe_path(profile, target)
            self._safe_path(profile, profile / "config.yaml")
            if target.exists() and payload["collision_resolution"] != "replace":
                raise StoreError("Skill already exists", status=409, code="delivery_conflict")
            files = self._files(payload["files"])
            stage = root / "stage"
            self._safe_path(root, stage)
            stage.mkdir(exist_ok=True)
            for name, content in files.items():
                self.repository.atomic_write(stage / name, content)
            result = {
                "installed_skill_id": skill,
                "receipt": operation,
                "digest": payload["digest"],
            }
            saved = {
                "fingerprint": fingerprint,
                "profile": payload["target_profile_id"],
                "skill": skill,
                "result": result,
            }
            self.repository.atomic_json(intent, saved)
            self.repository.atomic_json(stage / ".community-receipt.json", saved)
            config_path = profile / "config.yaml"
            try:
                config = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
                text = files["SKILL.md"].decode("utf-8")
                parts = text.split("---", 2)
                frontmatter = (
                    yaml.safe_load(parts[1]) if text.startswith("---") and len(parts) == 3 else {}
                )
            except (yaml.YAMLError, UnicodeError):
                raise StoreError("Invalid skill metadata", code="invalid_skill") from None
            config = config or {}
            frontmatter = frontmatter or {}
            if not isinstance(config, dict) or not isinstance(frontmatter, dict):
                raise StoreError("Invalid skill metadata", code="invalid_skill")
            settings = config.setdefault("skills", {})
            if not isinstance(settings, dict) or not isinstance(settings.get("disabled", []), list):
                raise StoreError("Invalid skill configuration", code="invalid_skill")
            disabled = set(settings.get("disabled", []))
            # Hermes indexes frontmatter names, which can differ from the path
            # after a rename. Disable both before making any bytes visible.
            disabled.update((skill, str((frontmatter or {}).get("name", skill))))
            settings["disabled"] = sorted(disabled)
            if config_path.exists():
                self.repository.snapshot(
                    payload["target_profile_id"],
                    "config",
                    "community-install",
                    config_path.read_bytes(),
                )
            self.repository.atomic_yaml(config_path, config)
            target.parent.mkdir(exist_ok=True)
            if target.exists():
                # Retain the complete previous tree for recovery, including
                # references and scripts, before replacing the installed copy.
                os.replace(target, root / "previous")
                self.repository._sync_dir(target.parent)
            os.replace(stage, target)
            self.repository._sync_dir(target.parent)
            self.repository.atomic_json(root / "receipt.json", saved)
            return result
        finally:
            os.close(descriptor)

    @staticmethod
    def _files(items):
        files = {}
        total = 0
        for item in items:
            name = item["path"]
            path = PurePosixPath(name)
            if (
                path.is_absolute()
                or str(path) != name
                or any(char in name for char in "\\\x00\r\n:")
                or any(p in {"..", "."} for p in path.parts)
            ):
                raise StoreError("Invalid skill path", code="invalid_path")
            forbidden = {
                ".env",
                "state.db",
                "auth.json",
                "credentials",
                "sessions",
                "memory",
                ".git",
                ".community-receipt.json",
            }
            if (
                name.casefold() in {item.casefold() for item in files}
                or len(path.parts) > 12
                or any(
                    part.lower() in forbidden or part.lower().startswith(".env.")
                    for part in path.parts
                )
            ):
                raise StoreError("Invalid skill file", code="invalid_path")
            content = (
                base64.b64decode(item["content"], validate=True)
                if item["encoding"] == "base64"
                else item["content"].encode()
            )
            total += len(content)
            if (
                total > 10_000_000
                or len(content) != item["size"]
                or "sha256:" + hashlib.sha256(content).hexdigest() != item["digest"]
            ):
                raise StoreError("Skill checksum mismatch", code="invalid_checksum")
            files[name] = content
        if "SKILL.md" not in files:
            raise StoreError("SKILL.md required", code="invalid_skill")
        return files
