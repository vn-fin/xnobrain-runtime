"""Crash recovery for interrupted plugin artifact publication."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import zipfile
from pathlib import Path
from typing import Any

from . import extension_engine, extension_policy

_PLUGIN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_OPERATION_ID = re.compile(r"^[a-f0-9]{32}$")
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_PUBLICATION_PHASES = frozenset({"prepared", "old_moved", "published", "state_saved"})


class ExtensionRecoveryError(RuntimeError):
    """An interrupted publication could not be safely reconciled."""

    code = "extension_recovery_required"


def publication_path(profile: Path) -> Path:
    return extension_policy.safe_child(profile, ".xnobrain", "extensions", "publication.json")


def write_publication(profile: Path, intent: dict[str, Any]) -> None:
    _validate_intent(intent)
    path = publication_path(profile)
    if path.is_symlink():
        raise ExtensionRecoveryError("Publication intent storage is unsafe")
    extension_policy.atomic_json(path, intent)


def clear_publication(profile: Path) -> None:
    path = publication_path(profile)
    if path.is_symlink():
        raise ExtensionRecoveryError("Publication intent is unsafe")
    path.unlink(missing_ok=True)
    if path.parent.is_dir():
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def recover_publication(profile: Path, *, staging_root: Path | None = None) -> bool:
    """Recover one intent. Caller must hold the profile lifecycle lock."""
    path = publication_path(profile)
    if not path.exists():
        return False
    if path.is_symlink() or not path.is_file():
        raise ExtensionRecoveryError("Publication intent is unsafe")
    try:
        intent = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ExtensionRecoveryError("Publication intent is invalid") from error
    _validate_intent(intent)

    operation_id = intent["operation_id"]
    plugin_id = intent["plugin_id"]
    plugins_root = extension_policy.safe_child(profile, "plugins")
    target = extension_policy.safe_child(profile, "plugins", plugin_id)
    rollback = extension_policy.safe_child(
        profile, "plugins", f".{plugin_id}.{operation_id}.rollback"
    )
    replacement = extension_policy.safe_child(
        profile, "plugins", f".{plugin_id}.{operation_id}.stage"
    )
    had_old = intent["had_old"]
    old_digest = intent["old_artifact_digest"]
    new_digest = intent.get("new_artifact_digest", "")

    rollback_exists = rollback.exists()
    target_exists = target.exists()
    if rollback_exists and (rollback.is_symlink() or not rollback.is_dir()):
        raise ExtensionRecoveryError("Plugin rollback artifact is unsafe")
    if target_exists and (target.is_symlink() or not target.is_dir()):
        raise ExtensionRecoveryError("Plugin publication target is unsafe")

    rollback_digest = extension_policy.tree_digest(rollback) if rollback_exists else ""
    if rollback_exists and rollback_digest != old_digest:
        raise ExtensionRecoveryError("Plugin rollback digest does not match intent")
    target_digest = extension_policy.tree_digest(target) if target_exists else ""

    if rollback_exists:
        if target_exists:
            if target_digest == old_digest:
                shutil.rmtree(rollback)
                sync_directory(plugins_root)
            elif target_digest == new_digest:
                # The verified old tree remains available at rollback.
                shutil.rmtree(target)
                os.replace(rollback, target)
                sync_directory(plugins_root)
            else:
                raise ExtensionRecoveryError("Plugin target digest does not match intent")
        else:
            os.replace(rollback, target)
            sync_directory(plugins_root)
        target_exists = True
        target_digest = extension_policy.tree_digest(target)
    elif had_old and not target_exists:
        target = _restore_backup(profile, plugin_id, old_digest, target)
        target_exists = target.is_dir()
        target_digest = extension_policy.tree_digest(target) if target_exists else ""
        if not target_exists:
            raise ExtensionRecoveryError("Original plugin artifact is unavailable")
    elif target_exists and new_digest and target_digest not in {old_digest, new_digest}:
        raise ExtensionRecoveryError("Plugin target digest does not match intent")
    elif target_exists and had_old and target_digest != old_digest and not new_digest:
        raise ExtensionRecoveryError("Plugin target digest does not match intent")

    if replacement.exists():
        if replacement.is_symlink() or not replacement.is_dir():
            raise ExtensionRecoveryError("Plugin staged artifact is unsafe")
        shutil.rmtree(replacement)
    if staging_root is not None:
        _remove_staging(staging_root, operation_id)

    # A recovered artifact is always disabled and requires a fresh scan and
    # approval, even when recovery restored the previous tree.
    extension_engine.plugin_set_enabled(profile, plugin_id, False)
    state = extension_policy.load_state(profile)
    record = state["plugins"].get(plugin_id, {})
    if target_exists:
        digest = extension_policy.tree_digest(target)
        record["artifact_digest"] = digest
        record["scan"] = {"status": "REQUIRED", "verdict": "unknown", "findings": []}
        record["approval"] = {"approved": False, "artifact_digest": ""}
        record.pop("source", None)
        record.pop("ref", None)
        record.pop("commit", None)
        record["revision"] = int(record.get("revision", 0)) + 1
        state["plugins"][plugin_id] = record
    else:
        state["plugins"].pop(plugin_id, None)
    state["revision"] = int(state.get("revision", 1)) + 1
    extension_policy.save_state(profile, state)
    clear_publication(profile)
    return True


def discard_unjournaled_stages(profile: Path) -> None:
    """Remove abandoned replacement trees only after exclusive worker start."""
    plugins = extension_policy.safe_child(profile, "plugins")
    if not plugins.is_dir():
        return
    pattern = re.compile(r"^\.[A-Za-z0-9][A-Za-z0-9_-]{0,127}\.[a-f0-9]{32}\.stage$")
    for path in plugins.iterdir():
        if not pattern.fullmatch(path.name):
            continue
        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)


def discard_staging_root(root: Path) -> None:
    """Clean abandoned public clone directories when no worker holds the fence."""
    if root.is_symlink():
        raise ExtensionRecoveryError("Extension staging root is unsafe")
    if not root.is_dir():
        return
    for path in root.iterdir():
        if not _OPERATION_ID.fullmatch(path.name):
            continue
        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        elif path.is_file():
            path.unlink()


def _validate_intent(intent: Any) -> None:
    if not isinstance(intent, dict):
        raise ExtensionRecoveryError("Publication intent is invalid")
    expected = {"operation_id", "plugin_id", "phase", "had_old", "old_artifact_digest"}
    allowed = expected | {"new_artifact_digest"}
    if set(intent) - allowed or not expected.issubset(intent):
        raise ExtensionRecoveryError("Publication intent is invalid")
    if not _OPERATION_ID.fullmatch(str(intent.get("operation_id", ""))):
        raise ExtensionRecoveryError("Publication operation id is invalid")
    if not _PLUGIN_ID.fullmatch(str(intent.get("plugin_id", ""))):
        raise ExtensionRecoveryError("Publication plugin id is invalid")
    phase = intent.get("phase")
    if not isinstance(phase, str) or phase not in _PUBLICATION_PHASES:
        raise ExtensionRecoveryError("Publication phase is invalid")
    if type(intent.get("had_old")) is not bool:
        raise ExtensionRecoveryError("Publication state is invalid")
    old_digest = intent.get("old_artifact_digest")
    new_digest = intent.get("new_artifact_digest", "")
    if (old_digest and not _DIGEST.fullmatch(str(old_digest))) or not _DIGEST.fullmatch(
        str(new_digest)
    ):
        raise ExtensionRecoveryError("Publication digest is invalid")
    if intent["had_old"] != bool(old_digest):
        raise ExtensionRecoveryError("Publication old artifact is inconsistent")


def _restore_backup(profile: Path, plugin_id: str, digest: str, target: Path) -> Path:
    if not digest:
        raise ExtensionRecoveryError("Original plugin digest is unavailable")
    archive = extension_policy.safe_child(
        extension_policy.state_root(profile), "backups", plugin_id, f"{digest}.zip"
    )
    if archive.is_symlink() or not archive.is_file():
        raise ExtensionRecoveryError("Original plugin backup is unavailable")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = target.with_name(target.name + ".recovery")
    if staging.exists() or staging.is_symlink():
        raise ExtensionRecoveryError("Plugin recovery path is occupied")
    staging.mkdir(mode=0o700)
    try:
        with zipfile.ZipFile(archive) as source:
            items = source.infolist()
            if len(items) > 500:
                raise ExtensionRecoveryError("Plugin backup exceeds file-count limit")
            total_size = 0
            for item in items:
                relative = Path(item.filename)
                if relative.is_absolute() or any(
                    part in {"", ".", ".."} for part in relative.parts
                ):
                    raise ExtensionRecoveryError("Plugin backup path is unsafe")
                mode = item.external_attr >> 16
                if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                    raise ExtensionRecoveryError("Plugin backup entry is unsafe")
                destination = staging.joinpath(*relative.parts)
                if item.is_dir():
                    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    if item.file_size > 1024 * 1024:
                        raise ExtensionRecoveryError("Plugin backup entry exceeds limits")
                    total_size += item.file_size
                    if total_size > 8 * 1024 * 1024:
                        raise ExtensionRecoveryError("Plugin backup exceeds size limit")
                    with source.open(item) as stream, destination.open("xb") as output:
                        shutil.copyfileobj(stream, output)
        if extension_policy.tree_digest(staging) != digest:
            raise ExtensionRecoveryError("Plugin backup digest does not match intent")
        os.replace(staging, target)
        sync_directory(target.parent)
        return target
    except BaseException:
        if staging.exists() and not staging.is_symlink():
            shutil.rmtree(staging, ignore_errors=True)
        raise


def _remove_staging(root: Path, operation_id: str) -> None:
    if root.is_symlink():
        raise ExtensionRecoveryError("Extension staging root is unsafe")
    stage = root / operation_id
    if stage.is_symlink():
        stage.unlink()
    elif stage.is_dir():
        shutil.rmtree(stage)
    elif stage.exists():
        stage.unlink()


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
