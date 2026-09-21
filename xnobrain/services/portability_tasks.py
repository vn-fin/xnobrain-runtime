"""Task admission and public projections for workspace portability."""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..repositories.base import StoreError
from .portability import CHUNK_SIZE
from .portability_worker import ClaimLostError, PortabilityWorker

PREFIX = "/xnobrain/api/runtime/v1/bundles"


def timestamp(value: float | None) -> str | None:
    return datetime.fromtimestamp(value, UTC).isoformat() if value is not None else None


class PortabilityTasks:
    """Coordinates durable tasks without making the HTTP request own execution."""

    def __init__(self, portability, *, store=None):
        self.portability = portability
        self.store = store or portability.task_store
        self.artifacts = portability.transfer_root / "task-exports"
        self.artifacts.mkdir(mode=0o700, exist_ok=True)
        self.store.capacity = self._positive_setting("RUNTIME_PORTABILITY_QUEUE_LIMIT", 100)
        self.minimum_free_bytes = self._positive_setting(
            "RUNTIME_PORTABILITY_MIN_FREE_BYTES", 256 * 1024 * 1024
        )
        self.worker = PortabilityWorker(
            self.store, self.execute, maintenance=self.cleanup_terminal_inputs
        )

    def create_export(self, body, *, scope: str, actor: str, key: str | None = None):
        self.store.validate_key(key, required=False)
        selections = {}
        for field in ("agent_ids", "team_ids"):
            values = body.get(field, [])
            if not isinstance(values, list) or len(values) > 100:
                raise StoreError("Invalid snapshot selection", code="invalid_snapshot_selection")
            selections[field] = sorted({self.portability.repository._id(value) for value in values})
        if not any(selections.values()):
            raise StoreError(
                "Select at least one profile or team", code="invalid_snapshot_selection"
            )
        selections["include_conversations"] = bool(body.get("include_conversations", False))
        fingerprint = self.store.digest({"version": 1, "kind": "EXPORT", **selections})
        prior = self.store.replay(
            scope=scope, actor=actor, kind="EXPORT", key=key, fingerprint=fingerprint
        )
        if prior is not None:
            return self.project(prior), False
        self._check_space()
        task, created = self.store.admit(
            scope=scope,
            actor=actor,
            kind="EXPORT",
            key=key,
            fingerprint=fingerprint,
            inputs={"selection": selections, "export_id": uuid.uuid4().hex},
        )
        return self.project(task), created

    def get(self, task_id, *, scope, actor):
        return self.project(self.store.get(task_id, scope=scope, actor=actor))

    def list(self, *, scope, actor, before="", limit=50):
        page = self.store.list_tasks(scope=scope, actor=actor, before=before, limit=limit)
        page["items"] = [self.project(row) for row in page["items"]]
        return page

    def project(self, row: dict[str, Any]) -> dict[str, Any]:
        inputs = json.loads(row["input_json"])
        result = json.loads(row["result_json"]) if row["result_json"] else None
        if result is not None and row["kind"] == "EXPORT":
            directory = self.artifacts / result["export_id"]
            result["artifact_available"] = (
                result["expires_at_epoch"] > time.time() and (directory / "bundle.zip").is_file()
            )
            result.pop("expires_at_epoch", None)
        output = {
            "task_id": row["id"],
            "kind": row["kind"],
            "status": row["status"],
            "revision": row["revision"],
            "status_url": f"{PREFIX}/tasks/{row['id']}",
            "result": result,
            "error": json.loads(row["error_json"]) if row["error_json"] else None,
        }
        for field in ("created_at", "updated_at", "started_at", "completed_at"):
            output[field] = timestamp(row[field])
        if row["kind"] == "EXPORT":
            output["export_id"] = inputs["export_id"]
        if "event_id" in row:
            output["event_id"] = row["event_id"]
        return output

    def execute(self, claim):
        self._check_space()
        if claim["kind"] == "IMPORT":
            from .portability_imports import ImportPreparation

            preparation = ImportPreparation(self)
            stage, receipt = preparation.prepare(claim)
            mappings = preparation.reserve_mappings(claim, receipt)
            publication = preparation.prepare_publication(claim, stage, receipt, mappings)
            preparation.publish(claim, publication)
            return {**receipt["report"], **mappings}
        inputs = json.loads(claim["input_json"])
        export_id = self.portability.repository._id(inputs["export_id"])
        directory = self.artifacts / export_id
        directory.mkdir(mode=0o700, exist_ok=True)
        if directory.is_symlink():
            raise StoreError("Unsafe snapshot location", code="invalid_snapshot_path")
        # Each fencing generation owns its temporary archive. A stale generator
        # cannot overwrite a replacement worker's preparation output.
        temporary = directory / f"preparing-{claim['fence']}.zip"
        receipt = directory / "metadata.json"
        with self.store.publication_lock():
            self._require_claim(claim)
            if receipt.is_file() and (directory / "bundle.zip").is_file():
                metadata = self.portability._read_metadata(directory)
                if metadata.get("task_id") == claim["id"]:
                    archive = directory / "bundle.zip"
                    if archive.is_symlink() or archive.stat().st_size != metadata.get("size"):
                        raise StoreError(
                            "Snapshot recovery failed", code="snapshot_recovery_required"
                        )
                    if self.portability._hash_file(archive) != metadata.get("sha256"):
                        raise StoreError(
                            "Snapshot recovery failed", code="snapshot_recovery_required"
                        )
                    return metadata
        try:
            metadata = self.portability._export_to_path(inputs["selection"], temporary)
            with temporary.open("rb") as archive:
                os.fsync(archive.fileno())
            expires = time.time() + 24 * 60 * 60
            metadata.update(
                {
                    "task_id": claim["id"],
                    "export_id": export_id,
                    "chunk_size": CHUNK_SIZE,
                    "total_parts": self.portability._part_count(metadata["size"]),
                    "download_parts_url_template": (
                        f"{PREFIX}/task-exports/{export_id}/parts/{{part_number}}"
                    ),
                    "expires_at": timestamp(expires),
                    "expires_at_epoch": expires,
                    "artifact_available": True,
                }
            )
            with self.store.publication_lock():
                self._require_claim(claim)
                os.replace(temporary, directory / "bundle.zip")
                self.portability.repository.atomic_json(receipt, metadata)
                self._sync_directory(directory)
            return metadata
        finally:
            temporary.unlink(missing_ok=True)

    def _require_claim(self, claim):
        if not self.store.owns_claim(claim["id"], claim["lease_owner"], claim["fence"]):
            raise ClaimLostError("Portability claim expired")

    @staticmethod
    def _sync_directory(path: Path):
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def read_part(self, export_id, part_number, *, scope, actor):
        export_id = self.portability.repository._id(export_id)
        with self.store.publication_lock():
            task = self.store.export_task(export_id, scope=scope, actor=actor)
            if task["status"] != "COMPLETED":
                raise StoreError("Snapshot is not ready", status=409, code="snapshot_not_ready")
            metadata = json.loads(task["result_json"])
            directory = self.artifacts / export_id
            archive = directory / "bundle.zip"
            reader_active = self.store.artifact_reader(export_id)
            if (
                metadata["expires_at_epoch"] <= time.time() and not reader_active
            ) or not archive.is_file():
                raise StoreError("Snapshot expired", status=410, code="snapshot_expired")
            if directory.is_symlink() or archive.is_symlink():
                raise StoreError("Snapshot unavailable", status=410, code="snapshot_expired")
            number = self.portability._part_number(part_number, metadata["total_parts"])
            with archive.open("rb") as source:
                source.seek(number * CHUNK_SIZE)
                payload = source.read(CHUNK_SIZE)
            self.store.artifact_reader(export_id, renew=True)
            return payload, metadata

    def delete_export(self, export_id, *, scope, actor):
        import shutil

        export_id = self.portability.repository._id(export_id)
        # Detach under the same lock used by readers, then reclaim bytes without
        # blocking admission/publication for unrelated tasks. A fixed tombstone
        # path makes an interrupted deletion retryable after process restart.
        detached = self.artifacts / f".deleted-{export_id}"
        with self.store.publication_lock():
            task = self.store.export_task(export_id, scope=scope, actor=actor)
            if task["status"] in {"PENDING", "PROCESSING"} or self.store.artifact_reader(export_id):
                raise StoreError("Snapshot is in use", status=409, code="transfer_in_use")
            directory = self.artifacts / export_id
            if directory.is_symlink() or detached.is_symlink():
                raise StoreError("Snapshot unavailable", status=410, code="snapshot_expired")
            if directory.is_dir():
                os.rename(directory, detached)
                self._sync_directory(self.artifacts)
        try:
            shutil.rmtree(detached)
        except FileNotFoundError:
            # Concurrent authorized deletion may already have reclaimed it.
            pass
        return {"deleted": True, "transfer_id": export_id}

    def create_import(self, body, *, scope: str, actor: str, key: str | None):
        """Accept an immutable completed upload under the publication lock.

        Fingerprints bind the upload identity and options. Its archive digest is
        retained in immutable task input; completed upload IDs cannot be replaced.
        Replay precedes filesystem lookup so cleanup cannot erase idempotency.
        """
        self.store.validate_key(key, required=True)
        upload_id = self.portability.repository._id(body.get("upload_id"), "upload id")
        environment = body.get("environment", {})
        if not isinstance(environment, dict) or any(
            not isinstance(name, str) or not isinstance(value, str)
            for name, value in environment.items()
        ):
            raise StoreError("Invalid environment", code="invalid_environment")
        if len(json.dumps(environment).encode()) > 64 * 1024:
            raise StoreError("Environment exceeds allowed size", code="invalid_environment")
        fingerprint = self.store.digest(
            {
                "version": 1,
                "kind": "IMPORT",
                "upload_id": upload_id,
                "environment": environment,
            }
        )
        with self.store.publication_lock():
            prior = self.store.replay(
                scope=scope,
                actor=actor,
                kind="IMPORT",
                key=key,
                fingerprint=fingerprint,
            )
            if prior is not None:
                original = json.loads(prior["input_json"])
                existing = self.portability.upload_root / upload_id / "metadata.json"
                if existing.is_symlink():
                    raise StoreError(
                        "Unsafe upload metadata", code="idempotency_conflict", status=409
                    )
                if existing.is_file():
                    current = self.portability._read_metadata(existing.parent)
                    if current.get("sha256") != original["archive_sha256"]:
                        raise StoreError(
                            "Upload changed since admission",
                            status=409,
                            code="idempotency_conflict",
                        )
                return self.project(prior), False
            self._check_space()
            directory = self.portability._transfer_dir(self.portability.upload_root, upload_id)
            metadata = self.portability._read_metadata(directory)
            if not metadata.get("complete") or not (directory / "bundle.zip").is_file():
                raise StoreError("Upload is not complete", status=409, code="upload_incomplete")
            if not metadata.get("sha256"):
                raise StoreError("Upload digest unavailable", status=409, code="upload_incomplete")
            secret_root = self.portability.repository.data_dir / "portability-inputs"
            secret_root.mkdir(mode=0o700, exist_ok=True)
            if secret_root.is_symlink():
                raise StoreError(
                    "Input storage unavailable", status=503, code="task_store_unavailable"
                )
            reference = uuid.uuid4().hex
            secret_path = secret_root / reference
            self.portability.repository.atomic_write(
                secret_path,
                json.dumps(environment).encode(),
                mode=0o600,
                replace=False,
            )
            try:
                task, created = self.store.admit(
                    scope=scope,
                    actor=actor,
                    kind="IMPORT",
                    key=key,
                    fingerprint=fingerprint,
                    upload_id=upload_id,
                    inputs={
                        "upload_id": upload_id,
                        "archive_sha256": metadata["sha256"],
                        "environment_ref": reference,
                    },
                )
            except BaseException:
                secret_path.unlink(missing_ok=True)
                raise
            if not created:
                secret_path.unlink(missing_ok=True)
            return self.project(task), created

    def cleanup_terminal_inputs(self):
        """Remove safe terminal scratch data; retain ambiguous import evidence."""
        import shutil

        with self.store.publication_lock():
            with self.store.transaction() as db:
                rows = db.execute(
                    """SELECT id,input_json,result_json,kind FROM portability_tasks
                    WHERE status='COMPLETED' OR (kind='IMPORT' AND status='FAILED'
                    AND json_extract(error_json,'$.code')='import_failed')"""
                ).fetchall()
            for row in rows:
                inputs = json.loads(row["input_json"])
                if row["kind"] == "IMPORT":
                    reference = self.portability.repository._id(inputs["environment_ref"])
                    (
                        self.portability.repository.data_dir / "portability-inputs" / reference
                    ).unlink(missing_ok=True)
                    stage = self.portability.repository.data_dir / "portability-imports" / row["id"]
                    if stage.is_dir() and not stage.is_symlink():
                        shutil.rmtree(stage)
                else:
                    result = json.loads(row["result_json"])
                    if result["expires_at_epoch"] <= time.time() and not self.store.artifact_reader(
                        result["export_id"]
                    ):
                        artifact = self.artifacts / self.portability.repository._id(
                            result["export_id"]
                        )
                        if artifact.is_dir() and not artifact.is_symlink():
                            shutil.rmtree(artifact)

    @staticmethod
    def _positive_setting(name, default):
        try:
            value = int(os.getenv(name, str(default)))
            if value <= 0:
                raise ValueError
            return value
        except ValueError as error:
            raise StoreError(
                "Invalid portability configuration", status=503, code="task_configuration_invalid"
            ) from error

    def _check_space(self):
        import shutil

        if shutil.disk_usage(self.portability.repository.data_dir).free < self.minimum_free_bytes:
            raise StoreError("Insufficient snapshot storage", status=429, code="task_storage_quota")
