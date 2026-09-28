"""Publication policy for reviewed, reusable example profiles.

This validates bytes only. It never installs skills, extracts files, or creates
profiles. Free-form starter content still requires the publisher's review.
"""

import hashlib
import json
import re
import shutil
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any
from zipfile import BadZipFile, ZipFile

import yaml

from ..repositories.base import StoreError

MAX_FILES = 10_000
CAPABILITY = "profile-example-v1"
ROOT_FILES = {"config.yaml", "agent.json", "AGENTS.md", "SOUL.md", "SYSTEM.md"}
CONTENT_DIRECTORIES = {"prompts", "skills", "workspace"}
PRIVATE_PARTS = {
    ".git",
    ".ssh",
    ".aws",
    ".kube",
    ".gnupg",
    ".config",
    ".cache",
    "__pycache__",
    "node_modules",
    "credentials",
    "sessions",
    "conversations",
    "logs",
    "cache",
    "backups",
    "snapshots",
    "transfers",
    "imports",
    "exports",
    "memories",
    "history",
    "staging",
}
PRIVATE_FILES = {
    ".env",
    "auth.json",
    "credentials.env",
    "secrets.json",
    "tokens.json",
    "oauth.json",
    "user.md",
    "memory.md",
    ".portability-owner.json",
    ".community-clone-receipt.json",
    ".community-profile-owner.json",
}
SENSITIVE_KEY = re.compile(
    r"(?:^|[_-])(?:api[_-]?key|secret|access[_-]?token|refresh[_-]?token|"
    r"auth[_-]?token|token|password|credentials?|authorization|cookies?|oauth)$",
    re.I,
)
REFERENCE = re.compile(r"\$\{[A-Z][A-Z0-9_]*\}")


def start_example_upload(service: Any, body: Mapping[str, Any]) -> dict[str, Any]:
    """Reserve conservative staging space before accepting example bytes.

    Reservations coordinate example uploads. The workspace filesystem quota
    and the ordinary import worker still enforce actual writes and free space.
    """
    size = int(body.get("size") or 0)
    if size <= 0:
        _reject("example ZIP must not be empty")
    reserve = 2 * size
    with service.task_store.publication_lock():
        held = 0
        for path in service.upload_root.glob("*/metadata.json"):
            metadata = service._read_metadata(path.parent)
            allocation = int(metadata.get("example_reserved_bytes", 0))
            if not allocation:
                continue
            age = datetime.now(UTC) - datetime.fromisoformat(metadata["created_at"])
            if age.days >= 7 and not service.task_store.pinned(path.parent.name):
                service._delete_transfer("upload", path.parent.name)
                continue
            held += allocation
        if shutil.disk_usage(service.upload_root).free < held + reserve + 256 * 1024 * 1024:
            raise StoreError(
                "insufficient example staging space", status=507, code="insufficient_storage"
            )
        metadata = service.start_upload(body, example=True)
        metadata["example_reserved_bytes"] = reserve
        service.repository.atomic_json(
            service.upload_root / metadata["upload_id"] / "metadata.json", metadata
        )
        return metadata


def _reject(message: str) -> None:
    raise StoreError(message, code="invalid_example_profile")


def _check_settings(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if SENSITIVE_KEY.search(str(key)) and child not in (None, "", "[REDACTED]"):
                if not isinstance(child, str) or not REFERENCE.fullmatch(child):
                    _reject("example settings contain embedded credentials")
            _check_settings(child)
    elif isinstance(value, list):
        for child in value:
            _check_settings(child)


def validate_example_upload(service: Any, upload_id: str) -> dict[str, Any]:
    """Validate a completed staged ZIP while holding its publication lease."""
    with service.task_store.publication_lock():
        directory = service._transfer_dir(service.upload_root, upload_id)
        metadata = service._read_metadata(directory)
        path = directory / "bundle.zip"
        if not metadata.get("complete") or not path.is_file():
            raise StoreError("upload is not complete", status=409, code="upload_incomplete")
        try:
            with ZipFile(path) as archive:
                if any(info.flag_bits & 1 for info in archive.infolist()):
                    _reject("encrypted example archives are unsupported")
        except BadZipFile as error:
            raise StoreError("invalid example ZIP", code="invalid_bundle") from error
        archive, files, expanded = service._validated_archive_file(
            path, max_files=MAX_FILES, example=True
        )
        with archive:
            manifest = json.loads(archive.read("manifest.json"))
            # The existing importer uses this provenance field in its report.
            service.repository._id(manifest.get("export_id"), "export id")
            agents = manifest["agents"]
            if len(agents) != 1 or manifest.get("teams"):
                _reject("example must contain exactly one named profile and no teams")
            profile_id = agents[0]["id"]
            if profile_id.casefold() in {"default", "root", "big-brother"}:
                _reject("example must use a dedicated named profile")
            if manifest.get("credentials_included"):
                _reject("example must not include credentials")
            prefix = f"profiles/{profile_id}/"
            skill_roots = {
                PurePosixPath(name.removeprefix(prefix)).parent
                for name in files
                if name.startswith(f"{prefix}skills/")
                and name.endswith("/SKILL.md")
                and len(PurePosixPath(name.removeprefix(prefix)).parts) >= 3
            }
            skills: set[str] = set()
            for name in files:
                if name in {"manifest.json", "checksums.json"}:
                    continue
                if not name.startswith(prefix):
                    _reject("example contains content outside its named profile")
                relative = PurePosixPath(name.removeprefix(prefix))
                parts = relative.parts
                folded = tuple(part.casefold() for part in parts)
                if (
                    any(part in PRIVATE_PARTS for part in folded)
                    or folded[-1] in PRIVATE_FILES
                    or folded[-1].startswith(".env.")
                    or any(
                        folded[-1].endswith(suffix)
                        for suffix in (
                            ".db",
                            ".db-wal",
                            ".db-shm",
                            ".sqlite",
                            ".sqlite3",
                            ".log",
                            ".bak",
                        )
                    )
                    or (len(parts) == 1 and parts[0] not in ROOT_FILES)
                    or (len(parts) > 1 and parts[0] not in CONTENT_DIRECTORIES)
                ):
                    _reject(f"example contains a private or unsupported path: {relative}")
                if parts[0] == "skills":
                    # Runtime discovers skills recursively, including category
                    # folders. Every file must still belong to an actual skill;
                    # a sibling SKILL.md cannot authorize an unrelated file.
                    owner = next(
                        (parent for parent in relative.parents if parent in skill_roots), None
                    )
                    if owner is None:
                        _reject("example skills must contain SKILL.md")
                    skills.add(owner.relative_to("skills").as_posix())
                if str(relative) in {"config.yaml", "agent.json"}:
                    content = archive.read(name)
                    try:
                        parsed = (
                            yaml.safe_load(content)
                            if name.endswith(".yaml")
                            else json.loads(content)
                        )
                    except (ValueError, yaml.YAMLError) as error:
                        raise StoreError(
                            "invalid example settings", code="invalid_example_profile"
                        ) from error
                    if not isinstance(parsed, Mapping):
                        _reject("example settings must be objects")
                    _check_settings(parsed)
                    if name.endswith("agent.json") and set(parsed) - {
                        "name",
                        "profile_name",
                        "display_name",
                        "title",
                        "description",
                    }:
                        _reject("example metadata contains nonportable ownership or state")
            required_environment = manifest.get("required_environment", [])
            if not isinstance(required_environment, list) or any(
                not isinstance(item, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", item)
                for item in required_environment
            ):
                _reject("invalid example environment requirements")
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        if digest != metadata["sha256"]:
            _reject("completed example upload changed")
        return {
            "capability": CAPABILITY,
            "sha256": digest,
            "size": path.stat().st_size,
            "expanded_size": expanded,
            "file_count": len(files),
            "profile_id": profile_id,
            "skills": sorted(skills),
            "required_environment": required_environment,
            "required_capabilities": manifest.get("required_capabilities", []),
            "content_review_required": True,
        }
