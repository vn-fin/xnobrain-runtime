"""Replace adopted content with recoverable, idempotent refresh operations."""

import fcntl
import hashlib
import json
import os
import shutil
from contextlib import contextmanager

from ..repositories.base import StoreError

CONTENT_NAMES = ("config.yaml", "agent.json", "HERMES.md", "AGENTS.md", "skills", "workspace")
BASELINE = ".organization-content-baseline.json"


def content_fingerprint(target):
    """Hash replaceable content only; never export destination data or history."""
    digest = hashlib.sha256()
    total = 0
    count = 0
    for name in CONTENT_NAMES:
        root = target / name
        if root.is_symlink():
            raise StoreError("unsafe refresh path", code="invalid_bundle")
        paths = [root, *sorted(root.rglob("*"))] if root.is_dir() else [root]
        for path in paths:
            if path.is_symlink():
                raise StoreError("unsafe refresh path", code="invalid_bundle")
            if not path.is_file():
                continue
            count += 1
            if count > 20000:
                raise StoreError("agent content is too large", code="invalid_bundle")
            file_digest = hashlib.sha256()
            with path.open("rb") as source:
                while chunk := source.read(512 * 1024):
                    total += len(chunk)
                    if total > 8 * 1024**3:
                        raise StoreError("agent content is too large", code="invalid_bundle")
                    file_digest.update(chunk)
            digest.update(path.relative_to(target).as_posix().encode() + b"\x00")
            digest.update(file_digest.digest())
    return digest.hexdigest()


def save_content_baseline(repository, target):
    repository.atomic_json(target / BASELINE, {"fingerprint": content_fingerprint(target)})


def organization_target(service, target_id, recipient):
    service.repository._id(target_id, "agent id")
    target = service.repository.profile_path(target_id)
    if not target.is_dir() or target.is_symlink():
        raise StoreError("organization agent unavailable", code="invalid_bundle")
    try:
        owner = json.loads((target / ".community-profile-owner.json").read_text())
    except (OSError, ValueError) as error:
        raise StoreError(
            "organization ownership unavailable", status=403, code="permission_denied"
        ) from error
    organization = recipient.split("\x00")[2]
    if not organization or owner.get("organization_id") != organization:
        raise StoreError("organization ownership mismatch", status=403, code="permission_denied")
    return target


def inspect_organization_profile(service, target_id, recipient):
    target = organization_target(service, target_id, recipient)
    with service.task_store.publication_lock():
        fingerprint = content_fingerprint(target)
        try:
            baseline = json.loads((target / BASELINE).read_text())["fingerprint"]
        except (OSError, ValueError, KeyError):
            baseline = ""
        return {
            "fingerprint": fingerprint,
            "baseline_known": bool(baseline),
            "locally_modified": bool(baseline and baseline != fingerprint),
        }


@contextmanager
def idle_profile(service, target_id):
    manager = getattr(service, "agent_manager", None)
    if manager is None:
        yield
        return
    # Run admission marks activity under this same registry lock.
    with manager._registry_lock:
        active = manager.active_agent_ids() | service.active_kanban_agents()
        if target_id in active:
            raise StoreError("Stop the active run before updating", status=409, code="agent_busy")
        yield


def organization_refresh_receipt(service, target_id, recipient, operation_id, digest):
    service.repository._id(operation_id, "refresh operation id")
    organization_target(service, target_id, recipient)
    key = hashlib.sha256((recipient + "\x00" + operation_id).encode()).hexdigest()
    journal_path = (
        service.repository.data_dir / "snapshots" / target_id / ("refresh-" + key) / "refresh.json"
    )
    descriptor = os.open(
        service.repository.data_dir / ".organization-refresh.lock",
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
        0o600,
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        if not journal_path.exists():
            return {"state": "missing"}
        journal = json.loads(journal_path.read_text())
        operation = journal["operation"]
        if operation["recipient"] != recipient or operation["digest"] != digest:
            raise StoreError("refresh operation conflicts", status=409, code="clone_conflict")
        return {
            "state": journal["state"],
            "agent_id_mappings": {operation["source_id"]: target_id},
            "sha256": digest,
        }
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


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
    service,
    archive,
    target_id,
    source_id,
    operation_id,
    digest,
    recipient,
    expected_fingerprint=None,
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
            with service.task_store.publication_lock(), idle_profile(service, target_id):
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
        try:
            with service.task_store.publication_lock(), idle_profile(service, target_id):
                try:
                    if expected_fingerprint and content_fingerprint(target) != expected_fingerprint:
                        raise StoreError(
                            "Agent changed after preview", status=409, code="target_changed"
                        )
                    for name in (*CONTENT_NAMES, BASELINE):
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
                    service._reset_imported_identity(
                        target, source_id=staged.name, target_id=target_id
                    )
                    save_content_baseline(service.repository, target)
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
