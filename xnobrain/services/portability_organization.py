"""Replace adopted content with recoverable, idempotent refresh operations."""

import fcntl
import hashlib
import json
import os
import shutil

from ..repositories.base import StoreError

CONTENT_NAMES = ("config.yaml", "agent.json", "HERMES.md", "AGENTS.md", "skills", "workspace")


def _remove(path):
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _rollback(target, backup, moves):
    for name, existed in reversed(moves):
        current = target / name
        if existed:
            if (backup / name).exists():
                _remove(current)
                os.replace(backup / name, current)
        else:
            _remove(current)


def refresh_organization_profile(
    service, archive, target_id, source_id, operation_id, digest, recipient
):
    service.repository._id(target_id, "agent id")
    service.repository._id(operation_id, "refresh operation id")
    target = service.repository.profile_path(target_id)
    if not target.is_dir() or target.is_symlink():
        raise StoreError("organization agent unavailable", code="invalid_bundle")
    try:
        owner = json.loads((target / ".community-profile-owner.json").read_text())
    except (OSError, ValueError) as error:
        raise StoreError("organization ownership unavailable", code="permission_denied") from error
    if not recipient.split("\x00")[2] or owner.get("organization_id") != recipient.split("\x00")[2]:
        raise StoreError("organization ownership mismatch", status=403, code="permission_denied")
    operation = {
        "target_id": target_id,
        "source_id": source_id,
        "operation_id": operation_id,
        "digest": digest,
        "recipient": recipient,
    }
    key = hashlib.sha256((recipient + "\x00" + operation_id).encode()).hexdigest()
    backup = service.repository.data_dir / "snapshots" / target_id / ("refresh-" + key)
    backup.mkdir(parents=True, exist_ok=True)
    journal_path = backup / "refresh.json"
    descriptor = os.open(
        service.repository.data_dir / ".organization-refresh.lock",
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
        0o600,
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        if journal_path.exists():
            journal = json.loads(journal_path.read_text())
            if journal["operation"] != operation:
                raise StoreError("refresh operation conflicts", status=409, code="clone_conflict")
            if journal["state"] == "completed":
                return {"agent_id_mappings": {source_id: target_id}, "sha256": digest}
            with service.task_store.publication_lock():
                _rollback(target, backup, journal["moves"])
                _remove(service.repository.profile_path(journal["staged_id"]))
        report = service.apply_shareable_file(
            archive,
            operation_id=operation_id,
            digest=digest,
            recipient=recipient,
            organization=True,
        )
        staged = service.repository.profile_path(report["agent_id_mappings"][source_id])
        if staged == target:
            raise StoreError("refresh target conflicts", status=409, code="clone_conflict")
        journal = {
            "operation": operation,
            "state": "applying",
            "staged_id": staged.name,
            "moves": [],
        }
        service.repository.atomic_json(journal_path, journal)
        with service.task_store.publication_lock():
            try:
                for name in CONTENT_NAMES:
                    previous, incoming = target / name, staged / name
                    if previous.is_symlink() or incoming.is_symlink():
                        raise StoreError("unsafe refresh path", code="invalid_bundle")
                    existed = previous.exists()
                    journal["moves"].append((name, existed))
                    service.repository.atomic_json(journal_path, journal)
                    if existed:
                        os.replace(previous, backup / name)
                    if incoming.exists():
                        os.replace(incoming, previous)
                service._reset_imported_identity(target, source_id=staged.name, target_id=target_id)
                journal["state"] = "completed"
                service.repository.atomic_json(journal_path, journal)
            except Exception:
                _rollback(target, backup, journal["moves"])
                journal["moves"] = []
                service.repository.atomic_json(journal_path, journal)
                raise
            finally:
                shutil.rmtree(staged, ignore_errors=True)
        return {"agent_id_mappings": {source_id: target_id}, "sha256": digest}
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
