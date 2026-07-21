"""Versioned, credential-free profile bundle service."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
from typing import Any, Mapping
import uuid
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile, ZipInfo

import yaml

from ..repositories import FileRepository, StoreError


BUNDLE_FORMAT = "open-lumora-bundle"
BUNDLE_VERSION = 1
MAX_COMPRESSED = 128 * 1024 * 1024
MAX_EXPANDED = 512 * 1024 * 1024
MAX_FILES = 20_000
EXCLUDED_PARTS = {".env", "credentials", "logs", "cache", "__pycache__", ".git", "node_modules"}
CODE_SUFFIXES = {".py", ".sh", ".js", ".ts", ".so", ".dll", ".dylib"}


class PortabilityService:
    """Export, validate, preview, and atomically install local profiles."""

    def __init__(self, repository: FileRepository):
        self.repository = repository

    def export(self, body: Mapping[str, Any]) -> tuple[bytes, str]:
        agent_ids = list(dict.fromkeys(str(item).strip() for item in body.get("agent_ids", []) if str(item).strip()))
        if not agent_ids:
            raise StoreError("at least one agent is required")
        include_conversations = bool(body.get("include_conversations", False))
        export_id = uuid.uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()
        agents = []
        files: dict[str, bytes] = {}
        for agent_id in agent_ids:
            profile = self.repository.profile_path(agent_id)
            if not profile.is_dir():
                raise StoreError(f"agent not found: {agent_id}", status=404, code="not_found")
            metadata = self._metadata(profile, agent_id)
            agents.append(metadata)
            for path in profile.rglob("*"):
                if not path.is_file() or path.is_symlink():
                    continue
                relative = path.relative_to(profile)
                if not self._portable(relative, include_conversations):
                    continue
                files[f"profiles/{agent_id}/{relative.as_posix()}"] = path.read_bytes()

        selected = set(agent_ids)
        teams = []
        for team in self.repository.list_teams():
            if str(team.get("orchestrator_id") or "") not in selected:
                continue
            team_id = str(team.get("id") or "")
            if not team_id:
                continue
            teams.append({"id": team_id, "name": str(team.get("name") or team_id)})
            files[f"teams/{team_id}.yaml"] = yaml.safe_dump(team, sort_keys=False).encode()

        manifest = {
            "format": BUNDLE_FORMAT, "version": BUNDLE_VERSION,
            "source_version": "0.2.0", "created_at": created_at,
            "export_id": export_id, "agents": agents, "teams": teams,
            "included": ["config", "memory", "skills", "workspace", "snapshots", "crons"] + (["conversations"] if include_conversations else []),
            "required_capabilities": [],
        }
        files["manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
        checksums = {name: {"sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload)} for name, payload in files.items()}
        output = BytesIO()
        with ZipFile(output, "w", ZIP_DEFLATED, compresslevel=6) as archive:
            for name, payload in sorted(files.items()):
                archive.writestr(name, payload)
            archive.writestr("checksums.json", json.dumps(checksums, indent=2) + "\n")
        return output.getvalue(), f"open-lumora-{export_id}.lumora"

    def inspect(self, payload: bytes) -> dict[str, Any]:
        archive, files, expanded = self._validated_archive(payload)
        with archive:
            manifest = json.loads(archive.read("manifest.json"))
        return {"manifest": manifest, "files": len(files), "expanded_bytes": expanded, "warnings": []}

    def dry_run(self, payload: bytes) -> dict[str, Any]:
        inspection = self.inspect(payload)
        manifest = inspection["manifest"]
        collisions = [item["id"] for item in manifest.get("agents", []) if self.repository.profile_path(item["id"]).exists()]
        team_collisions = []
        for item in manifest.get("teams", []):
            try:
                self.repository.get_team(item["id"])
                team_collisions.append(item["id"])
            except StoreError as error:
                if error.status != 404:
                    raise
        with ZipFile(BytesIO(payload)) as archive:
            names = archive.namelist()
        return {
            "inspection": inspection, "collisions": collisions,
            "approval_resets": len(manifest.get("agents", [])),
            "paused_cron_jobs": sum(1 for name in names if "/cron/jobs/" in name and name.endswith((".yaml", ".yml"))),
            "providers_reset": len(manifest.get("agents", [])),
            "quarantined_code": [name for name in names if PurePosixPath(name).suffix.lower() in CODE_SUFFIXES],
            "storage_required": inspection["expanded_bytes"], "team_collisions": team_collisions,
        }

    def apply(self, payload: bytes) -> dict[str, Any]:
        preview = self.dry_run(payload)
        manifest = preview["inspection"]["manifest"]
        imports_root = self.repository.data_dir / "imports"
        imports_root.mkdir(parents=True, exist_ok=True, mode=0o750)
        stage = Path(tempfile.mkdtemp(prefix=".bundle-", dir=imports_root))
        installed: list[Path] = []
        mappings: dict[str, str] = {}
        team_mappings: dict[str, str] = {}
        disabled_members = 0
        try:
            with ZipFile(BytesIO(payload)) as archive:
                for source in manifest.get("agents", []):
                    source_id = self.repository._id(source.get("id"), "agent id")
                    target_id = self._available_profile_id(source_id)
                    mappings[source_id] = target_id
                    target_stage = stage / "profiles" / target_id
                    prefix = f"profiles/{source_id}/"
                    for info in archive.infolist():
                        if not info.filename.startswith(prefix) or info.is_dir():
                            continue
                        relative = PurePosixPath(info.filename[len(prefix):])
                        destination = target_stage.joinpath(*relative.parts)
                        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
                        destination.write_bytes(archive.read(info))
                    self._reset_imported_profile(target_stage)
                    final = self.repository.profile_path(target_id)
                    final.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
                    os.replace(target_stage, final)
                    installed.append(final)

                for source in manifest.get("teams", []):
                    source_id = self.repository._id(source.get("id"), "team id")
                    name = f"teams/{source_id}.yaml"
                    if name not in archive.namelist():
                        continue
                    team = yaml.safe_load(archive.read(name)) or {}
                    target_id = self._available_team_id(source_id)
                    team_mappings[source_id] = target_id
                    team["id"] = target_id
                    team["orchestrator_id"] = mappings.get(str(team.get("orchestrator_id") or ""), str(team.get("orchestrator_id") or ""))
                    for member in team.get("members", []):
                        old = str(member.get("agent_id") or "")
                        if old in mappings:
                            member["agent_id"] = mappings[old]
                        else:
                            member["enabled"] = False
                            disabled_members += 1
                    self.repository.put_team(team)
            return {
                "export_id": manifest["export_id"], "agent_id_mappings": mappings,
                "team_id_mappings": team_mappings,
                "disabled_team_members": disabled_members,
                "paused_cron_jobs": preview["paused_cron_jobs"],
                "approval_resets": preview["approval_resets"],
                "providers_reset": preview["providers_reset"],
                "quarantined_code": preview["quarantined_code"], "warnings": [],
            }
        except Exception:
            for path in reversed(installed):
                if path.is_dir() and path.parent == self.repository.profiles_root:
                    shutil.rmtree(path)
            raise
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    def _validated_archive(self, payload: bytes) -> tuple[ZipFile, dict[str, ZipInfo], int]:
        if not payload or len(payload) > MAX_COMPRESSED:
            raise StoreError("bundle size is invalid", code="invalid_bundle")
        try:
            archive = ZipFile(BytesIO(payload))
        except BadZipFile as error:
            raise StoreError("bundle is not a valid zip archive", code="invalid_bundle") from error
        files: dict[str, ZipInfo] = {}
        expanded = 0
        try:
            for info in archive.infolist():
                path = PurePosixPath(info.filename)
                if path.is_absolute() or ".." in path.parts or "\\" in info.filename or "\x00" in info.filename:
                    raise StoreError("bundle contains an unsafe path", code="invalid_bundle")
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise StoreError("bundle symlinks are forbidden", code="invalid_bundle")
                if info.is_dir():
                    continue
                if info.filename in files or len(files) >= MAX_FILES:
                    raise StoreError("bundle file count or uniqueness is invalid", code="invalid_bundle")
                expanded += info.file_size
                if expanded > MAX_EXPANDED or (info.compress_size and info.file_size / info.compress_size > 200):
                    raise StoreError("bundle expansion limits exceeded", code="invalid_bundle")
                files[info.filename] = info
            if "manifest.json" not in files or "checksums.json" not in files:
                raise StoreError("bundle manifest and checksums are required", code="invalid_bundle")
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("format") != BUNDLE_FORMAT or manifest.get("version") != BUNDLE_VERSION:
                raise StoreError("unsupported bundle format or version", code="invalid_bundle")
            if manifest.get("required_capabilities"):
                raise StoreError("bundle requires unsupported capabilities", code="invalid_bundle")
            checksums = json.loads(archive.read("checksums.json"))
            for name, info in files.items():
                if name == "checksums.json":
                    continue
                expected = checksums.get(name)
                content = archive.read(info)
                if not expected or expected.get("size") != len(content) or expected.get("sha256") != hashlib.sha256(content).hexdigest():
                    raise StoreError(f"bundle checksum mismatch: {name}", code="invalid_bundle")
            if set(checksums) != set(files) - {"checksums.json"}:
                raise StoreError("bundle checksum inventory is invalid", code="invalid_bundle")
            declared = {self.repository._id(item.get("id"), "agent id") for item in manifest.get("agents", [])}
            for name in files:
                parts = PurePosixPath(name).parts
                if parts and parts[0] == "profiles" and (len(parts) < 3 or parts[1] not in declared):
                    raise StoreError("bundle references an undeclared profile", code="invalid_bundle")
            return archive, files, expanded
        except Exception:
            archive.close()
            raise

    @staticmethod
    def _portable(relative: Path, include_conversations: bool) -> bool:
        lowered = {part.lower() for part in relative.parts}
        if lowered & EXCLUDED_PARTS or relative.name.endswith((".sock", ".pid", ".log")):
            return False
        if relative.name == "state.db" and not include_conversations:
            return False
        return True

    @staticmethod
    def _metadata(profile: Path, agent_id: str) -> dict[str, Any]:
        metadata = {}
        try:
            metadata = json.loads((profile / "agent.json").read_text())
        except (OSError, json.JSONDecodeError):
            pass
        timestamp = datetime.fromtimestamp(profile.stat().st_mtime, timezone.utc).isoformat()
        return {
            "id": agent_id, "name": str(metadata.get("name") or agent_id),
            "title": str(metadata.get("title") or agent_id),
            "description": str(metadata.get("description") or ""),
            "created_at": timestamp, "updated_at": timestamp,
        }

    def _available_profile_id(self, source: str) -> str:
        if not self.repository.profile_path(source).exists():
            return source
        for _ in range(100):
            candidate = f"{source[:110]}-{uuid.uuid4().hex[:8]}"
            if not self.repository.profile_path(candidate).exists():
                return candidate
        raise StoreError("could not allocate an imported profile id", status=409, code="collision")

    def _available_team_id(self, source: str) -> str:
        try:
            self.repository.get_team(source)
        except StoreError as error:
            if error.status == 404:
                return source
            raise
        return f"{source[:110]}-{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _reset_imported_profile(profile: Path) -> None:
        config_path = profile / "config.yaml"
        config = {}
        if config_path.is_file():
            config = yaml.safe_load(config_path.read_text()) or {}
        config["approval_mode"] = "manual"
        approvals = config.setdefault("approvals", {})
        if isinstance(approvals, dict):
            approvals["mode"] = "manual"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        cron_root = profile / "cron" / "jobs"
        if cron_root.is_dir():
            for path in cron_root.glob("*.y*ml"):
                job = yaml.safe_load(path.read_text()) or {}
                job["enabled"] = False
                job["state"] = "stopped"
                path.write_text(yaml.safe_dump(job, sort_keys=False), encoding="utf-8")
