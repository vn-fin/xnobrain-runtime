"""Recoverable import preparation isolated from live profile publication."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from ..repositories.base import StoreError
from ..repositories.files import FileRepository
from .portability import PortabilityService


class ImportPreparation:
    """Reuse archive safety/reset semantics against a private staging repository.

    No live profile or team is changed by preparation. A complete, fsynced stage
    receipt is reusable after process death; incomplete stages are rebuilt from
    the pinned immutable input. Publication is a separate journalled phase.
    """

    def __init__(self, tasks):
        self.tasks = tasks
        self.portability = tasks.portability
        self.root = self.portability.repository.data_dir / "portability-imports"
        self.root.mkdir(mode=0o700, exist_ok=True)
        if self.root.is_symlink():
            raise StoreError(
                "Import storage unavailable", status=503, code="task_store_unavailable"
            )

    def prepare(self, claim):
        inputs = json.loads(claim["input_json"])
        stage = self.root / claim["id"]
        with self.tasks.store.publication_lock():
            self.tasks._require_claim(claim)
            if stage.is_symlink():
                raise StoreError("Unsafe import stage", code="import_recovery_required")
            receipt_path = stage / "prepared.json"
            if receipt_path.is_symlink():
                raise StoreError("Unsafe import receipt", code="import_recovery_required")
            if receipt_path.is_file():
                receipt = json.loads(receipt_path.read_text())
                if receipt.get("task_id") != claim["id"]:
                    raise StoreError("Invalid import receipt", code="import_recovery_required")
                return stage, receipt

        # Fence-specific stages let a replacement worker prepare without sharing
        # mutable files with an old process that has not noticed lease loss yet.
        temporary = self.root / f"{claim['id']}-{claim['fence']}.preparing"
        if temporary.exists():
            shutil.rmtree(temporary)
        temporary.mkdir(mode=0o700)
        try:
            upload = (
                self.portability._transfer_dir(self.portability.upload_root, inputs["upload_id"])
                / "bundle.zip"
            )
            if (
                upload.is_symlink()
                or self.portability._hash_file(upload) != inputs["archive_sha256"]
            ):
                raise StoreError("Upload checksum changed", code="invalid_bundle_checksum")
            reference = self.portability.repository._id(inputs["environment_ref"])
            secret = self.portability.repository.data_dir / "portability-inputs" / reference
            descriptor = os.open(secret, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor) as source:
                environment = json.load(source)
            # Workspace-local staging remains on the same operator-validated
            # durable mount, and does not modify Hermes state.db schema.
            repository = FileRepository(
                temporary / "data",
                temporary / "profiles",
                root_profile=self.portability.root_profile,
            )
            service = PortabilityService(repository, self.portability.root_profile)
            report = service.apply_file(upload, environment)
            receipt = {
                "task_id": claim["id"],
                "archive_sha256": inputs["archive_sha256"],
                "report": report,
            }
            repository.atomic_json(temporary / "prepared.json", receipt)
            self._sync_tree(temporary)
            with self.tasks.store.publication_lock():
                self.tasks._require_claim(claim)
                if stage.exists():
                    raise StoreError("Import stage conflict", code="import_recovery_required")
                os.rename(temporary, stage)
                self.tasks._sync_directory(self.root)
            return stage, receipt
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def _sync_tree(self, root: Path):
        directories = [root]
        for path in root.rglob("*"):
            if path.is_symlink():
                raise StoreError("Unsafe staged file", code="invalid_bundle")
            if path.is_dir():
                directories.append(path)
            elif path.is_file():
                with path.open("rb") as source:
                    os.fsync(source.fileno())
        for directory in reversed(directories):
            self.tasks._sync_directory(directory)

    def reserve_mappings(self, claim, receipt):
        """Allocate collision-resistant live IDs once and retain them on recovery.

        Staged IDs are not live IDs: a source name may already exist in the live
        workspace. Every installed identity and team reference must use these
        recorded mappings before the publication digest is committed.
        """
        import uuid

        mappings = {}
        with self.tasks.store.publication_lock():
            self.tasks._require_claim(claim)
            for kind, field in (("PROFILE", "agent_id_mappings"), ("TEAM", "team_id_mappings")):
                mappings[field] = {}
                for source in receipt["report"].get(field, {}):
                    source = self.portability.repository._id(source)
                    # Reserve a fresh ID even if the source name is currently
                    # unused. Publication must still use no-clobber primitives.
                    candidate = f"{source[:100]}-import-{uuid.uuid4().hex[:12]}"
                    row = self.tasks.store.reserve_target(
                        claim["id"],
                        claim["lease_owner"],
                        claim["fence"],
                        kind=kind,
                        source_id=source,
                        target_id=candidate,
                    )
                    mappings[field][source] = row["target_id"]
        return mappings

    def prepare_publication(self, claim, stage, receipt, mappings):
        """Freeze exact live-target content before recording publication intent."""
        import hashlib

        import yaml

        publication = stage / "publication"
        with self.tasks.store.preparation_lock(claim["id"]):
            self.tasks._require_claim(claim)
            if publication.is_symlink() or (publication / "ready.json").is_symlink():
                raise StoreError("Unsafe publication stage", code="import_recovery_required")
            if (publication / "ready.json").is_file():
                recorded = json.loads((publication / "ready.json").read_text())
                if recorded != mappings:
                    raise StoreError("Import mapping changed", code="import_recovery_required")
                return publication
            if publication.exists():
                shutil.rmtree(publication)
            publication.mkdir(mode=0o700)
            profiles = publication / "profiles"
            teams = publication / "teams"
            profiles.mkdir(mode=0o700)
            teams.mkdir(mode=0o700)
            staged_report = receipt["report"]
            translated = {
                staged_id: mappings["agent_id_mappings"][source]
                for source, staged_id in staged_report["agent_id_mappings"].items()
            }
            for source, staged_id in staged_report["agent_id_mappings"].items():
                target = mappings["agent_id_mappings"][source]
                destination = profiles / target
                shutil.copytree(stage / "profiles" / staged_id, destination)
                self.portability._reset_imported_identity(
                    destination,
                    source_id=source,
                    target_id=target,
                )
                identity_path = destination / "agent.json"
                identity = json.loads(identity_path.read_text())
                from .portability_tasks import timestamp

                identity["updated_at"] = timestamp(claim["created_at"])
                self.portability.repository.atomic_json(identity_path, identity)
                self.portability.repository.atomic_json(
                    destination / ".portability-owner.json",
                    {"task_id": claim["id"], "source_id": source},
                )
            for source, staged_id in staged_report.get("team_id_mappings", {}).items():
                target = mappings["team_id_mappings"][source]
                team = yaml.safe_load((stage / "data" / "teams" / f"{staged_id}.yaml").read_text())
                team["id"] = target
                for field in ("orchestrator_id", "synthesis_agent_id"):
                    team[field] = translated.get(team.get(field), team.get(field))
                for item in [*team.get("members", []), *team.get("workflow", [])]:
                    if item.get("agent_id") in translated:
                        item["agent_id"] = translated[item["agent_id"]]
                team["_portability_task_id"] = claim["id"]
                self.portability.repository.atomic_yaml(teams / f"{target}.yaml", team)
            self._sync_tree(publication)
            for kind, field in (("PROFILE", "agent_id_mappings"), ("TEAM", "team_id_mappings")):
                for source, target in mappings[field].items():
                    path = profiles / target if kind == "PROFILE" else teams / f"{target}.yaml"
                    digest = (
                        self.content_digest(path)
                        if kind == "PROFILE"
                        else hashlib.sha256(path.read_bytes()).hexdigest()
                    )
                    self.tasks.store.record_publication(
                        claim["id"],
                        claim["lease_owner"],
                        claim["fence"],
                        kind=kind,
                        source_id=source,
                        digest=digest,
                        phase="PREPARED",
                    )
            self.portability.repository.atomic_json(publication / "ready.json", mappings)
            self.tasks._sync_directory(publication)
        return publication

    @staticmethod
    def content_digest(root: Path):
        import hashlib

        digest = hashlib.sha256()
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise StoreError("Unsafe publication content", code="import_recovery_required")
            relative = path.relative_to(root).as_posix().encode()
            digest.update(len(relative).to_bytes(8, "big"))
            digest.update(relative)
            digest.update(b"D" if path.is_dir() else b"F")
            if path.is_file():
                digest.update(path.stat().st_size.to_bytes(8, "big"))
                with path.open("rb") as source:
                    while chunk := source.read(1024 * 1024):
                        digest.update(chunk)
        return digest.hexdigest()

    def publish(self, claim, publication):
        """Reconcile each exact resource before advancing its durable receipt.

        Caller must enforce logical visibility until the task commit. Published
        paths remain journal-owned across crashes; mismatches fail closed rather
        than allocating new IDs or deleting externally changed files.
        """
        import hashlib

        from ..repositories.portability_publication import rename_without_replace, sync_directory

        for entry in self.tasks.store.journal(claim["id"]):
            target_id = self.portability.repository._id(entry["target_id"])
            profile = entry["resource_kind"] == "PROFILE"
            source = (
                publication / "profiles" / target_id
                if profile
                else publication / "teams" / f"{target_id}.yaml"
            )
            target = (
                self.portability.repository.profiles_root / target_id
                if profile
                else self.portability.repository.teams_root / f"{target_id}.yaml"
            )
            with self.tasks.store.publication_lock():
                self.tasks._require_claim(claim)
                if entry["phase"] not in {"PREPARED", "PUBLISHED"} or not entry["digest"]:
                    raise StoreError("Missing import intent", code="import_recovery_required")
                if target.is_symlink() or source.is_symlink():
                    raise StoreError("Unsafe import target", code="import_recovery_required")
                if target.exists():
                    # A consumed source plus exact owner/content evidence is the
                    # recoverable rename-before-receipt crash window.
                    if source.exists():
                        raise StoreError("Import target conflict", code="import_recovery_required")
                    actual = (
                        self.content_digest(target)
                        if profile
                        else hashlib.sha256(target.read_bytes()).hexdigest()
                    )
                    if actual != entry["digest"]:
                        raise StoreError("Import target changed", code="import_recovery_required")
                else:
                    if entry["phase"] == "PUBLISHED" or not source.exists():
                        raise StoreError("Import resource missing", code="import_recovery_required")
                    actual = (
                        self.content_digest(source)
                        if profile
                        else hashlib.sha256(source.read_bytes()).hexdigest()
                    )
                    if actual != entry["digest"]:
                        raise StoreError("Import stage changed", code="import_recovery_required")
                    # Hashing may outlast the lease even while exclusion is held.
                    # Recheck fencing immediately before mutating live storage.
                    self.tasks._require_claim(claim)
                    rename_without_replace(source, target)
                    sync_directory(target.parent)
                    sync_directory(source.parent)
                self.tasks.store.record_publication(
                    claim["id"],
                    claim["lease_owner"],
                    claim["fence"],
                    kind=entry["resource_kind"],
                    source_id=entry["source_id"],
                    digest=entry["digest"],
                    phase="PUBLISHED",
                )
