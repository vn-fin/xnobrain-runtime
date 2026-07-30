"""Versioned, credential-free profile bundles with chunked transfer storage."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
from typing import Any, BinaryIO, Iterable, Mapping
import uuid
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile, ZipInfo

import yaml

from ..repositories import FileRepository, StoreError
from ..defaults import BIG_BROTHER_AGENT_ID


BUNDLE_FORMAT = "brain4all-bundle"
BUNDLE_VERSION = 1
CHUNK_SIZE = 4 * 1024 * 1024
MAX_COMPRESSED = 2 * 1024 * 1024 * 1024
MAX_EXPANDED = 8 * 1024 * 1024 * 1024
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_FILES = 20_000
MAX_PATH_DEPTH = 32
TRANSFER_TTL_SECONDS = 24 * 60 * 60
EXCLUDED_PARTS = {"credentials", "logs", "cache", "__pycache__", ".git", "node_modules", "tmp", "temp"}
ROOT_EXCLUDED_PARTS = {"profiles"}
ROOT_PROFILE_PARTS = {"workspace", "skills", "memories", "snapshots", "cron", "state.db"}
ROOT_PROFILE_FILES = {"agent.json", "config.yaml", "agents.md", "soul.md"}
SECRET_FILES = {".env", "auth.json", "credentials.env", "secrets.json", "tokens.json", "oauth.json"}
CODE_SUFFIXES = {".py", ".sh", ".js", ".ts", ".so", ".dll", ".dylib"}
SENSITIVE_KEY = re.compile(r"(?:api[_-]?key|secret|token|password|credential|authorization|cookie|oauth)", re.I)
ENDPOINT_KEY = re.compile(r"^(?:api|base_url|endpoint|host|url)$", re.I)
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
ENV_REFERENCE = re.compile(r"\$\{([A-Z][A-Z0-9_]{1,127})\}")
REDACTED = b"[REDACTED]"


class PortabilityService:
    """Export, validate, preview, and atomically install local profiles."""

    def __init__(self, repository: FileRepository, root_profile: str | Path | None = None):
        self.repository = repository
        self.root_profile = Path(root_profile or os.environ.get("HERMES_ROOT_PROFILE") or Path.home() / ".hermes")
        self.transfer_root = repository.data_dir / "transfers"
        self.upload_root = self.transfer_root / "uploads"
        self.export_root = self.transfer_root / "exports"
        for path in (self.upload_root, self.export_root):
            path.mkdir(parents=True, exist_ok=True, mode=0o750)
        self._cleanup_transfers()

    # Legacy whole-response endpoints stay compatible. New UI/API clients use
    # the transfer-session methods below so archive bytes are always chunked.
    def export(self, body: Mapping[str, Any]) -> tuple[bytes, str]:
        descriptor, name = tempfile.mkstemp(prefix=".bundle-", suffix=".zip", dir=self.transfer_root)
        os.close(descriptor)
        temporary = Path(name)
        try:
            metadata = self._export_to_path(body, temporary)
            return temporary.read_bytes(), metadata["filename"]
        finally:
            temporary.unlink(missing_ok=True)

    def inspect(self, payload: bytes) -> dict[str, Any]:
        return self._with_payload(payload, self.inspect_file)

    def dry_run(self, payload: bytes) -> dict[str, Any]:
        return self._with_payload(payload, self.dry_run_file)

    def apply(self, payload: bytes, environment: Mapping[str, str] | None = None) -> dict[str, Any]:
        return self._with_payload(payload, lambda path: self.apply_file(path, environment or {}))

    def start_export(self, body: Mapping[str, Any]) -> dict[str, Any]:
        transfer_id = uuid.uuid4().hex
        directory = self.export_root / transfer_id
        directory.mkdir(mode=0o750)
        try:
            metadata = self._export_to_path(body, directory / "bundle.zip")
            metadata.update({"export_id": transfer_id, "chunk_size": CHUNK_SIZE})
            metadata["total_parts"] = self._part_count(metadata["size"])
            self.repository.atomic_json(directory / "metadata.json", metadata)
            return metadata
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise

    def read_export_part(self, transfer_id: Any, part_number: Any) -> tuple[bytes, dict[str, Any]]:
        directory = self._transfer_dir(self.export_root, transfer_id)
        metadata = self._read_metadata(directory)
        number = self._part_number(part_number, metadata["total_parts"])
        with (directory / "bundle.zip").open("rb") as file:
            file.seek(number * CHUNK_SIZE)
            payload = file.read(CHUNK_SIZE)
        return payload, metadata

    def start_upload(self, body: Mapping[str, Any]) -> dict[str, Any]:
        filename = Path(str(body.get("filename") or "profile.zip")).name
        size = int(body.get("size") or 0)
        if size <= 0 or size > MAX_COMPRESSED:
            raise StoreError("bundle size is invalid", code="invalid_bundle")
        expected_sha = str(body.get("sha256") or "").strip().lower()
        if expected_sha and not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
            raise StoreError("bundle checksum is invalid", code="invalid_bundle")
        transfer_id = uuid.uuid4().hex
        directory = self.upload_root / transfer_id
        (directory / "parts").mkdir(parents=True, mode=0o750)
        metadata = {
            "upload_id": transfer_id,
            "filename": filename,
            "size": size,
            "sha256": expected_sha,
            "chunk_size": CHUNK_SIZE,
            "total_parts": self._part_count(size),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "complete": False,
        }
        self.repository.atomic_json(directory / "metadata.json", metadata)
        return metadata

    def put_upload_part(self, transfer_id: Any, part_number: Any, payload: bytes) -> dict[str, Any]:
        directory = self._transfer_dir(self.upload_root, transfer_id)
        metadata = self._read_metadata(directory)
        if metadata.get("complete"):
            raise StoreError("upload is already complete", status=409, code="upload_complete")
        number = self._part_number(part_number, metadata["total_parts"])
        expected = CHUNK_SIZE
        if number == metadata["total_parts"] - 1:
            expected = metadata["size"] - number * CHUNK_SIZE
        if len(payload) != expected:
            raise StoreError("upload part size is invalid", code="invalid_upload_part")
        path = directory / "parts" / f"{number:08d}.part"
        self.repository.atomic_write(path, payload)
        return {"upload_id": metadata["upload_id"], "part_number": number, "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}

    def complete_upload(self, transfer_id: Any, body: Mapping[str, Any]) -> dict[str, Any]:
        directory = self._transfer_dir(self.upload_root, transfer_id)
        metadata = self._read_metadata(directory)
        if metadata.get("complete") and (directory / "bundle.zip").is_file():
            return metadata
        target = directory / "bundle.zip"
        digest = hashlib.sha256()
        total = 0
        descriptor, temporary = tempfile.mkstemp(prefix=".bundle.", dir=directory)
        try:
            with os.fdopen(descriptor, "wb") as output:
                for number in range(metadata["total_parts"]):
                    part = directory / "parts" / f"{number:08d}.part"
                    if not part.is_file():
                        raise StoreError(f"upload part {number} is missing", code="missing_upload_part")
                    with part.open("rb") as source:
                        while chunk := source.read(1024 * 1024):
                            output.write(chunk)
                            digest.update(chunk)
                            total += len(chunk)
                output.flush()
                os.fsync(output.fileno())
            requested = str(body.get("sha256") or metadata.get("sha256") or "").strip().lower()
            actual = digest.hexdigest()
            if total != metadata["size"] or (requested and requested != actual):
                raise StoreError("merged bundle checksum is invalid", code="invalid_bundle_checksum")
            os.replace(temporary, target)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise
        preview = self.dry_run_file(target)
        metadata.update({"complete": True, "sha256": digest.hexdigest(), "preview": preview})
        self.repository.atomic_json(directory / "metadata.json", metadata)
        shutil.rmtree(directory / "parts", ignore_errors=True)
        return metadata

    def apply_upload(self, transfer_id: Any, body: Mapping[str, Any]) -> dict[str, Any]:
        directory = self._transfer_dir(self.upload_root, transfer_id)
        metadata = self._read_metadata(directory)
        if not metadata.get("complete") or not (directory / "bundle.zip").is_file():
            raise StoreError("upload is not complete", status=409, code="upload_incomplete")
        environment = body.get("environment") or {}
        if not isinstance(environment, Mapping):
            raise StoreError("environment must be an object", code="invalid_environment")
        report = self.apply_file(directory / "bundle.zip", environment)
        shutil.rmtree(directory, ignore_errors=True)
        return report

    def delete_transfer(self, kind: str, transfer_id: Any) -> dict[str, Any]:
        root = self.upload_root if kind == "upload" else self.export_root
        directory = self._transfer_dir(root, transfer_id)
        shutil.rmtree(directory)
        return {"deleted": True, "transfer_id": directory.name}

    def inspect_file(self, path: Path) -> dict[str, Any]:
        archive, files, expanded = self._validated_archive_file(path)
        with archive:
            manifest = json.loads(archive.read("manifest.json"))
        return {"manifest": manifest, "files": len(files), "expanded_bytes": expanded, "warnings": []}

    def dry_run_file(self, path: Path) -> dict[str, Any]:
        inspection = self.inspect_file(path)
        manifest = inspection["manifest"]
        collisions = [
            item["id"]
            for item in manifest.get("agents", [])
            if (
                self.root_profile.exists()
                if item["id"] == BIG_BROTHER_AGENT_ID
                else self.repository.profile_path(item["id"]).exists()
            )
        ]
        team_collisions = []
        for item in manifest.get("teams", []):
            try:
                self.repository.get_team(item["id"])
                team_collisions.append(item["id"])
            except StoreError as error:
                if error.status != 404:
                    raise
        with ZipFile(path) as archive:
            names = archive.namelist()
        required = sorted({str(item) for item in manifest.get("required_environment", []) if ENV_NAME.fullmatch(str(item))})
        available = self._default_environment()
        return {
            "inspection": inspection,
            "collisions": collisions,
            "approval_resets": len(manifest.get("agents", [])),
            "paused_cron_jobs": sum(1 for name in names if "/cron/jobs/" in name and name.endswith((".yaml", ".yml"))),
            "providers_reset": len(manifest.get("agents", [])),
            "quarantined_code": [name for name in names if PurePosixPath(name).suffix.lower() in CODE_SUFFIXES],
            "storage_required": inspection["expanded_bytes"],
            "team_collisions": team_collisions,
            "missing_environment": [name for name in required if not available.get(name)],
            "credentials_source": "default_profile",
        }

    def apply_file(self, path: Path, environment: Mapping[str, str]) -> dict[str, Any]:
        preview = self.dry_run_file(path)
        manifest = preview["inspection"]["manifest"]
        allowed_environment = set(preview["missing_environment"])
        supplied = self._validate_environment(environment, allowed_environment)
        imports_root = self.repository.data_dir / "imports"
        imports_root.mkdir(parents=True, exist_ok=True, mode=0o750)
        stage = Path(tempfile.mkdtemp(prefix=".bundle-", dir=imports_root))
        installed: list[Path] = []
        mappings: dict[str, str] = {}
        team_mappings: dict[str, str] = {}
        disabled_members = 0
        try:
            with ZipFile(path) as archive:
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
                        with archive.open(info) as input_file, destination.open("wb") as output_file:
                            shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
                    self._reset_imported_profile(target_stage, supplied)
                    self._reset_imported_identity(
                        target_stage,
                        source_id=source_id,
                        target_id=target_id,
                    )
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
                    team["synthesis_agent_id"] = mappings.get(
                        str(team.get("synthesis_agent_id") or ""),
                        str(team.get("synthesis_agent_id") or team.get("orchestrator_id") or ""),
                    )
                    for member in team.get("members", []):
                        old = str(member.get("agent_id") or "")
                        if old in mappings:
                            member["agent_id"] = mappings[old]
                        else:
                            member["enabled"] = False
                            member["diagnostic"] = f"Profile {old or 'unknown'} was not included in the imported snapshot."
                            disabled_members += 1
                    for step in team.get("workflow", []):
                        old = str(step.get("agent_id") or "")
                        if old in mappings:
                            step["agent_id"] = mappings[old]
                    self.repository.put_team(team)
            return {
                "export_id": manifest["export_id"],
                "agent_id_mappings": mappings,
                "team_id_mappings": team_mappings,
                "disabled_team_members": disabled_members,
                "paused_cron_jobs": preview["paused_cron_jobs"],
                "approval_resets": preview["approval_resets"],
                "providers_reset": preview["providers_reset"],
                "quarantined_code": preview["quarantined_code"],
                "credentials_source": "default_profile",
                "environment_filled": sorted(supplied),
                "warnings": [],
            }
        except Exception:
            for installed_path in reversed(installed):
                if installed_path.is_dir() and installed_path.parent == self.repository.profiles_root:
                    shutil.rmtree(installed_path)
            raise
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    def _export_to_path(self, body: Mapping[str, Any], target: Path) -> dict[str, Any]:
        requested_team_ids = list(dict.fromkeys(
            str(item).strip()
            for item in body.get("team_ids", [])
            if str(item).strip()
        ))
        selected_teams: list[dict[str, Any]] = []
        team_agents: list[str] = []
        for team_id in requested_team_ids:
            team = self.repository.get_team(team_id)
            selected_teams.append(team)
            team_agents.extend([
                str(team.get("orchestrator_id") or ""),
                str(team.get("synthesis_agent_id") or ""),
                *(str(member.get("agent_id") or "") for member in team.get("members", [])),
                *(str(step.get("agent_id") or "") for step in team.get("workflow", [])),
            ])
        agent_ids = list(dict.fromkeys(
            str(item).strip()
            for item in [*(body.get("agent_ids", []) or []), *team_agents]
            if str(item).strip()
        ))
        if not agent_ids and not requested_team_ids:
            raise StoreError("at least one agent or team is required")
        include_conversations = bool(body.get("include_conversations", False))
        export_id = uuid.uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()
        agents: list[dict[str, Any]] = []
        entries: list[tuple[str, Path]] = []
        required_environment: set[str] = set()
        secrets = self._known_secret_values()

        for agent_id in agent_ids:
            profile = (
                self.root_profile
                if agent_id == BIG_BROTHER_AGENT_ID
                else self.repository.profile_path(agent_id)
            )
            if not profile.is_dir():
                raise StoreError(f"agent not found: {agent_id}", status=404, code="not_found")
            agents.append(self._metadata(profile, agent_id))
            config_path = profile / "config.yaml"
            if config_path.is_file():
                required_environment.update(self._required_environment(config_path))
            secrets.update(self._credential_values(profile))
            managed_subtrees = self._managed_subtrees(profile)
            for item in profile.rglob("*"):
                if not item.is_file() or item.is_symlink():
                    continue
                relative = item.relative_to(profile)
                if self._portable(
                    relative,
                    include_conversations,
                    root_profile=agent_id == BIG_BROTHER_AGENT_ID,
                    managed_subtrees=managed_subtrees,
                ):
                    entries.append((f"profiles/{agent_id}/{relative.as_posix()}", item))

        selected = set(agent_ids)
        teams: list[dict[str, str]] = []
        team_payloads: list[tuple[str, bytes]] = []
        team_rows = selected_teams if requested_team_ids else self.repository.list_teams()
        for team in team_rows:
            if not requested_team_ids and str(team.get("orchestrator_id") or "") not in selected:
                continue
            team_id = str(team.get("id") or "")
            if team_id:
                teams.append({"id": team_id, "name": str(team.get("name") or team_id)})
                team_payloads.append((f"teams/{team_id}.yaml", yaml.safe_dump(team, sort_keys=False).encode()))

        manifest = {
            "format": BUNDLE_FORMAT,
            "version": BUNDLE_VERSION,
            "source_version": "0.2.0",
            "created_at": created_at,
            "export_id": export_id,
            "agents": agents,
            "teams": teams,
            "included": ["config", "memory", "skills", "workspace", "snapshots", "crons"] + (["conversations"] if include_conversations else []),
            "required_capabilities": [],
            "required_environment": sorted(required_environment),
            "credentials_included": False,
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        checksums: dict[str, dict[str, Any]] = {}
        with ZipFile(target, "w", ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
            manifest_payload = (json.dumps(manifest, indent=2) + "\n").encode()
            checksums["manifest.json"] = self._write_zip_payload(archive, "manifest.json", manifest_payload, secrets)
            for name, source in sorted(entries):
                if source.name == "config.yaml":
                    payload = self._sanitized_config(source)
                    checksums[name] = self._write_zip_payload(archive, name, payload, secrets)
                else:
                    checksums[name] = self._write_zip_file(archive, name, source, secrets)
            for name, payload in sorted(team_payloads):
                checksums[name] = self._write_zip_payload(archive, name, payload, secrets)
            archive.writestr("checksums.json", json.dumps(checksums, indent=2) + "\n")
        size = target.stat().st_size
        if size > MAX_COMPRESSED:
            target.unlink(missing_ok=True)
            raise StoreError("bundle size is invalid", code="invalid_bundle")
        filename = f"brain4all-{export_id}.zip"
        if len(selected_teams) == 1:
            team_name = re.sub(
                r"[^a-z0-9]+",
                "-",
                str(selected_teams[0].get("name") or "team").strip().lower(),
            ).strip("-")[:80] or "team"
            filename = f"brain4all-team-{team_name}-{export_id[:8]}.zip"
        return {
            "bundle_export_id": export_id,
            "filename": filename,
            "size": size,
            "sha256": self._hash_file(target),
            "created_at": created_at,
        }

    def _validated_archive_file(self, path: Path) -> tuple[ZipFile, dict[str, ZipInfo], int]:
        if not path.is_file() or path.stat().st_size <= 0 or path.stat().st_size > MAX_COMPRESSED:
            raise StoreError("bundle size is invalid", code="invalid_bundle")
        try:
            archive = ZipFile(path)
        except BadZipFile as error:
            raise StoreError("bundle is not a valid zip archive", code="invalid_bundle") from error
        files: dict[str, ZipInfo] = {}
        folded: set[str] = set()
        expanded = 0
        try:
            for info in archive.infolist():
                raw = info.filename
                path_value = PurePosixPath(raw)
                raw_parts = raw.split("/")
                control = any(ord(character) < 32 for character in raw)
                drive = bool(re.match(r"^[A-Za-z]:", raw))
                if path_value.is_absolute() or any(part in {"", ".", ".."} for part in raw_parts) or "\\" in raw or control or drive or len(path_value.parts) > MAX_PATH_DEPTH:
                    raise StoreError("bundle contains an unsafe path", code="invalid_bundle")
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise StoreError("bundle symlinks are forbidden", code="invalid_bundle")
                if info.is_dir():
                    continue
                key = raw.casefold()
                if raw in files or key in folded or len(files) >= MAX_FILES:
                    raise StoreError("bundle file count or uniqueness is invalid", code="invalid_bundle")
                if info.file_size > MAX_FILE_BYTES or (info.file_size and not info.compress_size):
                    raise StoreError("bundle contains an oversized file", code="invalid_bundle")
                expanded += info.file_size
                if expanded > MAX_EXPANDED or (info.compress_size and info.file_size / info.compress_size > 200):
                    raise StoreError("bundle expansion limits exceeded", code="invalid_bundle")
                files[raw] = info
                folded.add(key)
            if "manifest.json" not in files or "checksums.json" not in files:
                raise StoreError("bundle manifest and checksums are required", code="invalid_bundle")
            if files["manifest.json"].file_size > 4 * 1024 * 1024 or files["checksums.json"].file_size > 16 * 1024 * 1024:
                raise StoreError("bundle metadata is too large", code="invalid_bundle")
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
                digest, size = self._hash_member(archive, info)
                if not expected or expected.get("size") != size or expected.get("sha256") != digest:
                    raise StoreError(f"bundle checksum mismatch: {name}", code="invalid_bundle")
            if set(checksums) != set(files) - {"checksums.json"}:
                raise StoreError("bundle checksum inventory is invalid", code="invalid_bundle")
            declared = {self.repository._id(item.get("id"), "agent id") for item in manifest.get("agents", [])}
            for name in files:
                parts = PurePosixPath(name).parts
                if parts and parts[0] == "profiles":
                    relative_parts = {part.lower() for part in parts[2:]}
                    if len(parts) < 3 or parts[1] not in declared or relative_parts & EXCLUDED_PARTS or parts[-1].lower() in SECRET_FILES:
                        raise StoreError("bundle references unsafe profile content", code="invalid_bundle")
            return archive, files, expanded
        except Exception:
            archive.close()
            raise

    @staticmethod
    def _portable(
        relative: Path,
        include_conversations: bool,
        *,
        root_profile: bool = False,
        managed_subtrees: tuple[Path, ...] = (),
    ) -> bool:
        lowered = {part.lower() for part in relative.parts}
        if lowered & EXCLUDED_PARTS or relative.name.lower() in SECRET_FILES or relative.name.endswith((".sock", ".pid", ".log", ".db-shm", ".db-wal")):
            return False
        if root_profile and relative.parts and relative.parts[0].lower() in ROOT_EXCLUDED_PARTS:
            return False
        if root_profile:
            top_level = relative.parts[0].lower()
            if top_level not in ROOT_PROFILE_PARTS and top_level not in ROOT_PROFILE_FILES:
                return False
            if top_level == "state.db" and not include_conversations:
                return False
        if any(relative == subtree or subtree in relative.parents for subtree in managed_subtrees):
            return False
        if relative.name == "state.db" and not include_conversations:
            return False
        return True

    def _managed_subtrees(self, profile: Path) -> tuple[Path, ...]:
        subtrees: list[Path] = []
        for managed in (self.repository.profiles_root, self.repository.data_dir):
            try:
                relative = managed.relative_to(profile)
            except ValueError:
                continue
            if relative.parts:
                subtrees.append(relative)
        return tuple(subtrees)

    @staticmethod
    def _metadata(profile: Path, agent_id: str) -> dict[str, Any]:
        metadata = {}
        try:
            metadata = json.loads((profile / "agent.json").read_text())
        except (OSError, json.JSONDecodeError):
            pass
        timestamp = datetime.fromtimestamp(profile.stat().st_mtime, timezone.utc).isoformat()
        display_name = str(metadata.get("display_name") or metadata.get("title") or agent_id)
        return {"id": agent_id, "name": str(metadata.get("name") or agent_id), "display_name": display_name, "title": display_name, "description": str(metadata.get("description") or ""), "created_at": timestamp, "updated_at": timestamp}

    def _reset_imported_profile(self, profile: Path, supplied_environment: Mapping[str, str]) -> None:
        config_path = profile / "config.yaml"
        config = {}
        if config_path.is_file():
            config = yaml.safe_load(config_path.read_text()) or {}
        if not isinstance(config, dict):
            config = {}
        root_config = {}
        root_config_path = self.root_profile / "config.yaml"
        if root_config_path.is_file():
            root_config = yaml.safe_load(root_config_path.read_text()) or {}
        if isinstance(root_config, dict):
            if isinstance(root_config.get("providers"), dict):
                config["providers"] = deepcopy(root_config["providers"])
            root_model = root_config.get("model")
            model = config.setdefault("model", {})
            if isinstance(root_model, dict) and isinstance(model, dict):
                for key in ("provider", "base_url"):
                    if key in root_model:
                        model[key] = deepcopy(root_model[key])
        config["approval_mode"] = "manual"
        approvals = config.setdefault("approvals", {})
        if isinstance(approvals, dict):
            approvals["mode"] = "manual"
        for subsystem in ("skills", "memory"):
            section = config.setdefault(subsystem, {})
            if not isinstance(section, dict):
                section = {}
                config[subsystem] = section
            section["write_approval"] = True
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

        for filename in SECRET_FILES:
            source = self.root_profile / filename
            if source.is_file():
                shutil.copy2(source, profile / filename)
        if supplied_environment:
            env_path = profile / ".env"
            existing = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
            suffix = "" if not existing or existing.endswith("\n") else "\n"
            additions = "".join(f"{key}={value}\n" for key, value in sorted(supplied_environment.items()))
            env_path.write_text(existing + suffix + additions, encoding="utf-8")

        cron_root = profile / "cron" / "jobs"
        if cron_root.is_dir():
            for job_path in cron_root.glob("*.y*ml"):
                job = yaml.safe_load(job_path.read_text()) or {}
                job["enabled"] = False
                job["state"] = "stopped"
                job_path.write_text(yaml.safe_dump(job, sort_keys=False), encoding="utf-8")

    def _reset_imported_identity(
        self,
        profile: Path,
        *,
        source_id: str,
        target_id: str,
    ) -> None:
        metadata_path = profile / "agent.json"
        metadata: dict[str, Any] = {}
        if metadata_path.is_file():
            try:
                loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    metadata = loaded
            except (OSError, json.JSONDecodeError):
                metadata = {}
        metadata["name"] = target_id
        metadata["profile_name"] = target_id
        if source_id == BIG_BROTHER_AGENT_ID:
            metadata["display_name"] = "Big Brother (Imported)"
            metadata["title"] = metadata["display_name"]
        metadata["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.repository.atomic_json(metadata_path, metadata)

    def _sanitized_config(self, path: Path) -> bytes:
        try:
            config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            config = {}
        return yaml.safe_dump(self._sanitize_value(config), sort_keys=False, allow_unicode=True).encode()

    def _sanitize_value(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            sanitized = {}
            for key, child in value.items():
                text = str(key)
                if SENSITIVE_KEY.search(text) or ENDPOINT_KEY.fullmatch(text):
                    continue
                sanitized[text] = self._sanitize_value(child)
            return sanitized
        if isinstance(value, list):
            return [self._sanitize_value(item) for item in value]
        return value

    def _required_environment(self, path: Path) -> set[str]:
        try:
            value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return set()
        found: set[str] = set()

        def visit(current: Any, key: str = "") -> None:
            if isinstance(current, Mapping):
                for child_key, child in current.items():
                    visit(child, str(child_key))
            elif isinstance(current, list):
                for child in current:
                    visit(child, key)
            elif isinstance(current, str):
                if key.lower() in {"key_env", "env", "environment_variable"} and ENV_NAME.fullmatch(current):
                    found.add(current)
                found.update(ENV_REFERENCE.findall(current))

        visit(value)
        return found

    def _known_secret_values(self) -> set[bytes]:
        values = self._credential_values(self.root_profile)
        for key, value in os.environ.items():
            if SENSITIVE_KEY.search(key) and len(value) >= 6:
                values.add(value.encode())
        return values

    @staticmethod
    def _credential_values(profile: Path) -> set[bytes]:
        values: set[bytes] = set()
        for filename in SECRET_FILES:
            path = profile / filename
            if not path.is_file() or path.stat().st_size > 1024 * 1024:
                continue
            try:
                raw = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if path.suffix.lower() == ".json":
                try:
                    loaded = json.loads(raw)
                except json.JSONDecodeError:
                    loaded = None

                def collect(current: Any) -> None:
                    if isinstance(current, Mapping):
                        for child in current.values():
                            collect(child)
                    elif isinstance(current, list):
                        for child in current:
                            collect(child)
                    elif isinstance(current, str) and len(current) >= 6:
                        values.add(current.encode())

                collect(loaded)
                continue
            for line in raw.splitlines():
                if "=" not in line or line.lstrip().startswith("#"):
                    continue
                candidate = line.split("=", 1)[1].strip().strip('"\'')
                if len(candidate) >= 6:
                    values.add(candidate.encode())
        config_path = profile / "config.yaml"
        if config_path.is_file() and config_path.stat().st_size <= 4 * 1024 * 1024:
            try:
                config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError):
                config = {}

            def collect_config(current: Any, sensitive: bool = False) -> None:
                if isinstance(current, Mapping):
                    for key, child in current.items():
                        collect_config(child, sensitive or bool(SENSITIVE_KEY.search(str(key))))
                elif isinstance(current, list):
                    for child in current:
                        collect_config(child, sensitive)
                elif sensitive and isinstance(current, str) and len(current) >= 6:
                    values.add(current.encode())

            collect_config(config)
        return values

    def _default_environment(self) -> dict[str, str]:
        values = {key: value for key, value in os.environ.items() if ENV_NAME.fullmatch(key) and value}
        for filename in (".env", "credentials.env"):
            path = self.root_profile / filename
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if "=" not in line or line.lstrip().startswith("#"):
                    continue
                key, value = line.split("=", 1)
                if ENV_NAME.fullmatch(key.strip()) and value.strip():
                    values[key.strip()] = value.strip()
        return values

    @staticmethod
    def _validate_environment(environment: Mapping[str, Any], allowed: set[str]) -> dict[str, str]:
        result: dict[str, str] = {}
        for raw_key, raw_value in environment.items():
            key = str(raw_key).strip()
            value = str(raw_value).strip()
            if key not in allowed or not ENV_NAME.fullmatch(key):
                raise StoreError(f"environment value is not requested: {key}", code="invalid_environment")
            if not value or len(value) > 8192 or "\n" in value or "\r" in value:
                raise StoreError(f"environment value is invalid: {key}", code="invalid_environment")
            result[key] = value
        return result

    def _write_zip_file(self, archive: ZipFile, name: str, source: Path, secrets: set[bytes]) -> dict[str, Any]:
        if source.stat().st_size > MAX_FILE_BYTES:
            raise StoreError(f"profile file is too large: {name}", code="oversized_profile_file")
        with source.open("rb") as file:
            return self._write_zip_stream(archive, name, file, secrets)

    def _write_zip_payload(self, archive: ZipFile, name: str, payload: bytes, secrets: set[bytes]) -> dict[str, Any]:
        return self._write_zip_stream(archive, name, BytesIO(payload), secrets)

    def _write_zip_stream(self, archive: ZipFile, name: str, source: BinaryIO, secrets: set[bytes]) -> dict[str, Any]:
        digest = hashlib.sha256()
        size = 0
        info = ZipInfo(name)
        info.compress_type = ZIP_DEFLATED
        info.external_attr = 0o640 << 16
        with archive.open(info, "w", force_zip64=True) as output:
            for chunk in self._redacted_chunks(source, secrets):
                output.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        return {"sha256": digest.hexdigest(), "size": size}

    @staticmethod
    def _redacted_chunks(source: BinaryIO, secrets: set[bytes]) -> Iterable[bytes]:
        candidates = sorted((value for value in secrets if len(value) >= 6), key=len, reverse=True)
        if not candidates:
            while chunk := source.read(1024 * 1024):
                yield chunk
            return
        maximum = max(map(len, candidates))
        buffer = b""
        while chunk := source.read(1024 * 1024):
            buffer += chunk
            while len(buffer) >= maximum:
                safe = len(buffer) - maximum + 1
                matches = [(buffer.find(secret), secret) for secret in candidates]
                matches = [(position, secret) for position, secret in matches if 0 <= position < safe]
                if not matches:
                    yield buffer[:safe]
                    buffer = buffer[safe:]
                    continue
                position, secret = min(matches, key=lambda item: item[0])
                if position:
                    yield buffer[:position]
                yield REDACTED
                buffer = buffer[position + len(secret):]
        while buffer:
            matches = [(buffer.find(secret), secret) for secret in candidates]
            matches = [(position, secret) for position, secret in matches if position >= 0]
            if not matches:
                yield buffer
                break
            position, secret = min(matches, key=lambda item: item[0])
            if position:
                yield buffer[:position]
            yield REDACTED
            buffer = buffer[position + len(secret):]

    @staticmethod
    def _hash_member(archive: ZipFile, info: ZipInfo) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        with archive.open(info) as file:
            while chunk := file.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        return digest.hexdigest(), size

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as file:
            while chunk := file.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    def _available_profile_id(self, source: str) -> str:
        if source == BIG_BROTHER_AGENT_ID:
            for _ in range(100):
                candidate = f"big-brother-import-{uuid.uuid4().hex[:8]}"
                if not self.repository.profile_path(candidate).exists():
                    return candidate
            raise StoreError("could not allocate an imported Big Brother profile id", status=409, code="collision")
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
    def _part_count(size: int) -> int:
        return (size + CHUNK_SIZE - 1) // CHUNK_SIZE

    @staticmethod
    def _part_number(value: Any, total: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError) as error:
            raise StoreError("invalid part number", code="invalid_upload_part") from error
        if number < 0 or number >= total:
            raise StoreError("invalid part number", code="invalid_upload_part")
        return number

    def _transfer_dir(self, root: Path, value: Any) -> Path:
        transfer_id = self.repository._id(value, "transfer id")
        directory = (root / transfer_id).resolve()
        if directory.parent != root.resolve() or not directory.is_dir():
            raise StoreError("transfer not found", status=404, code="not_found")
        return directory

    @staticmethod
    def _read_metadata(directory: Path) -> dict[str, Any]:
        try:
            metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise StoreError("transfer metadata is invalid", status=500, code="invalid_transfer") from error
        return metadata

    def _with_payload(self, payload: bytes, operation):
        if not payload or len(payload) > MAX_COMPRESSED:
            raise StoreError("bundle size is invalid", code="invalid_bundle")
        descriptor, name = tempfile.mkstemp(prefix=".legacy-bundle-", suffix=".zip", dir=self.transfer_root)
        path = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as file:
                file.write(payload)
            return operation(path)
        finally:
            path.unlink(missing_ok=True)

    def _cleanup_transfers(self) -> None:
        cutoff = datetime.now(timezone.utc).timestamp() - TRANSFER_TTL_SECONDS
        for root in (self.upload_root, self.export_root):
            for directory in root.iterdir():
                try:
                    if directory.is_dir() and directory.stat().st_mtime < cutoff:
                        shutil.rmtree(directory)
                except OSError:
                    continue
