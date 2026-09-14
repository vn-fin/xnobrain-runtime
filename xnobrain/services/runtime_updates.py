"""Runtime-side maintenance lifecycle for typed workspace updates."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import shutil
from typing import Any, Mapping

from ..integrations.runtime_update_storage import (
    RuntimeUpdateStorage,
    RuntimeUpdateStorageError,
)
from ..repositories.base import StoreError
from ..repositories.runtime_update_gate import activity_present, checkpoint_gate, update_operation
from ..repositories.runtime_updates import RuntimeUpdateRepository
from .base import ServiceError, iso

_ALLOWED_LAYOUTS = {
    "root_only",
    "incus_custom_volume",
    "docker_volume",
    "native_vm",
}


class RuntimeUpdateService:
    """Quiesce and verify this process; node replacement remains gateway-owned."""

    def __init__(self, platform):
        self.platform = platform
        self.repository = RuntimeUpdateRepository(platform.repository)
        self.storage = RuntimeUpdateStorage(
            platform.repository.data_dir,
            platform.repository.profiles_root,
            platform.config.root_profile,
        )
        self._lock = asyncio.Lock()
        self._condition = asyncio.Condition()
        self._maintenance = self.repository.maintenance()
        if self._maintenance.get("dispatch_paused"):
            self.platform.organization_connector.dispatch_paused = True
        self._active_organization_commands: set[str] = set()

    @property
    def dispatch_paused(self) -> bool:
        return bool(self.repository.maintenance().get("dispatch_paused"))

    def require_dispatch(self) -> None:
        self.platform.repository.storage_mount.check()
        if self.dispatch_paused:
            raise ServiceError(
                "Runtime is draining for an approved update",
                status=503,
                code="runtime_update_maintenance",
            )

    def register_organization_command(self, command_id: str) -> None:
        if self.dispatch_paused:
            raise ServiceError(
                "Runtime is draining for an approved update",
                status=503,
                code="runtime_update_maintenance",
            )
        self._active_organization_commands.add(command_id)

    async def release_organization_command(self, command_id: str) -> None:
        self._active_organization_commands.discard(command_id)
        await self._notify_activity()

    async def _notify_activity(self) -> None:
        async with self._condition:
            self._condition.notify_all()

    @staticmethod
    async def _durable_io(operation, *args):
        # Keep OS command/activity gates while submitted storage work finishes,
        # even after observer cancellation. A late fsync/rename/scan cannot race a
        # new command or release checkpoint exclusivity before its worker exits.
        task = asyncio.create_task(asyncio.to_thread(operation, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
            task.result()
            raise

    async def preflight(
        self, request: Mapping[str, Any], *, update_token: str | None = None
    ) -> dict[str, Any]:
        self._authorize(update_token)
        async with self._lock, _UpdateCommand(self.platform.repository.data_dir):
            self._validate_request(request)
            layout = str(request["expected_layout"])
            if layout not in _ALLOWED_LAYOUTS:
                raise ServiceError(
                    "unknown storage layout blocks Runtime update",
                    status=409,
                    code="runtime_update_layout_unknown",
                )
            try:
                observed = self.storage.layout(str(request["data_path"]))
            except RuntimeUpdateStorageError as error:
                self._storage_error(error)
            if layout == "root_only" and observed["separate_mount"]:
                self._conflict("approved root-only layout no longer matches Runtime storage")
            if (
                layout in {"incus_custom_volume", "docker_volume"}
                and not observed["separate_mount"]
            ):
                self._conflict("approved separate-volume layout no longer matches Runtime storage")
            free_bytes = shutil.disk_usage(observed["data_path"]).free
            required = int(request.get("required_free_bytes") or 0)
            blockers = []
            if free_bytes < required:
                blockers.append("insufficient_data_capacity")
            result = {
                "operation_id": request["operation_id"],
                "generation": request["generation"],
                "ready": not blockers,
                "layout": layout,
                "data_path": observed["data_path"],
                "separate_mount": observed["separate_mount"],
                "filesystem": observed["filesystem"],
                "free_bytes": free_bytes,
                "required_free_bytes": required,
                "blockers": blockers,
                "durable_roots": [label for label, _ in self.storage.durable_roots()],
                "checked_at": iso(),
            }
            self._record(request, "preflight", result)
            if blockers:
                raise ServiceError(
                    "Runtime update preflight failed",
                    status=409,
                    code="runtime_update_insufficient_capacity",
                )
            return result

    async def drain(
        self, request: Mapping[str, Any], *, update_token: str | None = None
    ) -> dict[str, Any]:
        self._authorize(update_token)
        async with self._lock, _UpdateCommand(self.platform.repository.data_dir):
            self._validate_request(request)
            self._maintenance = {
                "operation_id": request["operation_id"],
                "generation": request["generation"],
                "target": dict(request["target"]),
                "dispatch_paused": True,
                "phase": "draining",
                "updated_at": iso(),
            }
            await self._durable_io(self.repository.save_maintenance, dict(self._maintenance))
            await self.platform.organization_connector.pause_dispatch()
            deadline = asyncio.get_running_loop().time() + float(
                request.get("deadline_seconds") or 0
            )
            while self._active_counts()["total"]:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                async with self._condition:
                    try:
                        await asyncio.wait_for(self._condition.wait(), timeout=min(0.25, remaining))
                    except asyncio.TimeoutError:
                        pass
            cancelled = False
            if self._active_counts()["total"] and request.get("cancel_active_at_deadline"):
                await self._cancel_active()
                cancelled = True
            counts = self._active_counts()
            if counts["total"]:
                raise ServiceError(
                    "Runtime still has active work after the drain deadline",
                    status=409,
                    code="runtime_update_drain_timeout",
                )
            self._maintenance["phase"] = "drained"
            self._maintenance["updated_at"] = iso()
            await self._durable_io(self.repository.save_maintenance, dict(self._maintenance))
            result = {
                "operation_id": request["operation_id"],
                "generation": request["generation"],
                "drained": True,
                "dispatch_paused": True,
                "cancelled_at_deadline": cancelled,
                "active": counts,
                "completed_at": iso(),
            }
            self._record(request, "drain", result)
            return result

    async def checkpoint(
        self, request: Mapping[str, Any], *, update_token: str | None = None
    ) -> dict[str, Any]:
        self._authorize(update_token)
        async with self._lock, _UpdateCommand(self.platform.repository.data_dir):
            self._validate_request(request, require_maintenance=True)
            with checkpoint_gate(self.platform.repository.data_dir):
                if self._active_counts(include_leases=False)["total"]:
                    raise ServiceError(
                        "Runtime must be fully drained before checkpoint",
                        status=409,
                        code="runtime_update_not_drained",
                    )
                existing = self.repository.operation(str(request["operation_id"]))
                completed = (existing.get("steps") or {}).get("checkpoint")
                if isinstance(completed, dict):
                    self._validate_checkpoint(request, completed)
                    return completed
                try:

                    def inspect_and_flush():
                        self.storage.manifest()
                        paths = self.storage.flush_sqlite()
                        return paths, self.storage.manifest()

                    sqlite_paths, manifest = await self._durable_io(inspect_and_flush)
                except RuntimeUpdateStorageError as error:
                    self._storage_error(error)
                target = dict(request["target"])
                checkpoint_id = (
                    "ckp_"
                    + hashlib.sha256(
                        json.dumps(
                            {
                                "operation_id": request["operation_id"],
                                "generation": request["generation"],
                                "target": target,
                                "manifest": manifest["digest"],
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode()
                    ).hexdigest()
                )
                checkpoint = {
                    "checkpoint_id": checkpoint_id,
                    "operation_id": request["operation_id"],
                    "generation": request["generation"],
                    "target": target,
                    "manifest": manifest,
                    "sqlite_checkpoints": sqlite_paths,
                    "created_at": iso(),
                    "recovery_mode": "forward_only",
                }
                try:
                    self.repository.save_checkpoint(checkpoint)
                except Exception:
                    existing = self.repository.checkpoint(checkpoint_id)
                    if existing != checkpoint:
                        raise
                self._maintenance["phase"] = "checkpointed"
                self._maintenance["checkpoint_id"] = checkpoint_id
                self._maintenance["updated_at"] = iso()
                await self._durable_io(self.repository.save_maintenance, dict(self._maintenance))
                self._record(request, "checkpoint", checkpoint)
                return checkpoint

    async def readiness(
        self, request: Mapping[str, Any], *, update_token: str | None = None
    ) -> dict[str, Any]:
        self._authorize(update_token)
        async with self._lock, _UpdateCommand(self.platform.repository.data_dir):
            self._validate_request(request, require_maintenance=True)
            checkpoint_id = str(request.get("checkpoint_id") or "")
            if checkpoint_id:
                checkpoint = self.repository.checkpoint(checkpoint_id)
                if not checkpoint:
                    self._conflict("approved Runtime update checkpoint was not found")
                self._validate_checkpoint(request, checkpoint)
            current_schema = int(os.getenv("RUNTIME_DATA_SCHEMA", "1"))
            target = self._effective_target(request)
            target_schema = int(target["data_schema"])
            probes = {
                "http": True,
                "profiles": self.platform.repository.profiles_root.is_dir(),
                "data_writable": os.access(self.platform.repository.data_dir, os.W_OK),
                "schema_compatible": current_schema >= target_schema,
                "local_tools": True,
            }
            result = {
                "operation_id": request["operation_id"],
                "generation": request["generation"],
                "ready": all(probes.values()),
                "probes": probes,
                "installed": self._installed_identity(),
                "profile_count": len(self.platform.repository._profile_dirs()),
                "checked_at": iso(),
            }
            self._record(request, "readiness", result)
            if not result["ready"]:
                raise ServiceError(
                    "candidate Runtime readiness failed",
                    status=503,
                    code="runtime_update_readiness_failed",
                )
            return result

    async def post_verify(
        self, request: Mapping[str, Any], *, update_token: str | None = None
    ) -> dict[str, Any]:
        self._authorize(update_token)
        async with self._lock, _UpdateCommand(self.platform.repository.data_dir):
            self._validate_request(request, require_maintenance=True)
            with checkpoint_gate(self.platform.repository.data_dir):
                checkpoint = self.repository.checkpoint(str(request["checkpoint_id"]))
                if not checkpoint:
                    self._conflict("approved Runtime update checkpoint was not found")
                self._validate_checkpoint(request, checkpoint)
                try:
                    current = await self._durable_io(self.storage.manifest)
                except RuntimeUpdateStorageError as error:
                    self._storage_error(error)
                preserved = current["digest"] == checkpoint["manifest"]["digest"]
                installed = self._installed_identity()
                target = self._effective_target(request)
                identity_matches = (
                    installed["version"] == target["version"]
                    and installed["source_commit"] == target["source_commit"]
                    and installed["runtime_digest"] == target["runtime_digest"]
                    and installed["data_schema"] >= target["data_schema"]
                )
                result = {
                    "operation_id": request["operation_id"],
                    "generation": request["generation"],
                    "verified": preserved and identity_matches,
                    "data_preserved": preserved,
                    "installed_identity_matches": identity_matches,
                    "manifest_digest": current["digest"],
                    "installed": installed,
                    "verified_at": iso(),
                }
                self._record(request, "post_verify", result)
                if not result["verified"]:
                    raise ServiceError(
                        "candidate Runtime post-verification failed",
                        status=409,
                        code="runtime_update_post_verify_failed",
                    )
                return result

    async def recover(
        self, request: Mapping[str, Any], *, update_token: str | None = None
    ) -> dict[str, Any]:
        self._authorize(update_token)
        async with self._lock, _UpdateCommand(self.platform.repository.data_dir):
            self._validate_request(request, require_maintenance=True)
            action = str(request["action"])
            phase = "needs_operator" if action == "needs_operator" else "recovering"
            if action == "approve_newer_target":
                newer = dict(request.get("newer_target") or {})
                current = self._effective_target(request)
                if (
                    self._version_key(str(newer.get("version") or ""))
                    < self._version_key(str(current.get("version") or ""))
                    or int(newer.get("data_schema") or 0) < int(current["data_schema"])
                    or (
                        newer.get("source_commit") == current.get("source_commit")
                        and newer.get("runtime_digest") == current.get("runtime_digest")
                    )
                ):
                    raise ServiceError(
                        "approved recovery target is not a newer compatible target",
                        status=409,
                        code="runtime_update_recovery_target_invalid",
                    )
                self._maintenance["recovery_target"] = newer
            self._maintenance.update(
                {
                    "phase": phase,
                    "dispatch_paused": True,
                    "reason_code": request["reason_code"],
                    "updated_at": iso(),
                }
            )
            await self._durable_io(self.repository.save_maintenance, dict(self._maintenance))
            result = {
                "operation_id": request["operation_id"],
                "generation": request["generation"],
                "state": phase,
                "dispatch_paused": True,
                "allowed_actions": (
                    ["retry_selected_target", "check_newer_fix", "contact_operator"]
                    if phase == "recovering"
                    else ["check_newer_fix", "contact_operator"]
                ),
                "effective_target": self._effective_target(request),
                "version_rollback_allowed": False,
                "updated_at": iso(),
            }
            self._record(request, "recovery", result)
            return result

    async def resume(
        self, request: Mapping[str, Any], *, update_token: str | None = None
    ) -> dict[str, Any]:
        self._authorize(update_token)
        async with self._lock, _UpdateCommand(self.platform.repository.data_dir):
            self._validate_request(request, require_maintenance=True)
            operation = self.repository.operation(str(request["operation_id"]))
            verified = operation.get("steps", {}).get("post_verify", {}).get("verified") is True
            if not verified:
                raise ServiceError(
                    "Runtime cannot resume before successful post-verification",
                    status=409,
                    code="runtime_update_not_verified",
                )
            await self._durable_io(self.repository.clear_maintenance)
            self._maintenance = {}
            self.platform.organization_connector.resume_dispatch()
            result = {
                "operation_id": request["operation_id"],
                "generation": request["generation"],
                "resumed": True,
                "dispatch_paused": False,
                "resumed_at": iso(),
            }
            self._record(request, "resume", result)
            return result

    @staticmethod
    def _authorize(supplied: str | None) -> None:
        expected = (
            os.getenv("RUNTIME_UPDATE_SERVICE_TOKEN", "").strip()
            or os.getenv("RUNTIME_INTERNAL_SERVICE_TOKEN", "").strip()
        )
        if not expected or not supplied or not hmac.compare_digest(supplied, expected):
            raise ServiceError(
                "Runtime update service identity is required",
                status=401,
                code="runtime_update_unauthorized",
            )

    def _effective_target(self, request: Mapping[str, Any]) -> dict[str, Any]:
        recovery = self._maintenance.get("recovery_target")
        return dict(recovery) if isinstance(recovery, Mapping) else dict(request["target"])

    @staticmethod
    def _version_key(value: str) -> tuple[int, int, int, str]:
        match = __import__("re").fullmatch(
            r"v?(\d+)\.(\d+)\.(\d+)(?:[-+]([A-Za-z0-9.-]+))?",
            value,
        )
        if match is None:
            raise ServiceError(
                "Runtime update target version is invalid",
                status=409,
                code="runtime_update_recovery_target_invalid",
            )
        major, minor, patch = (int(match.group(index)) for index in range(1, 4))
        return major, minor, patch, str(match.group(4) or "")

    def _validate_request(
        self, request: Mapping[str, Any], *, require_maintenance: bool = False
    ) -> None:
        existing = self.repository.operation(str(request["operation_id"]))
        if existing:
            if int(request["generation"]) < int(existing.get("generation") or 0):
                raise ServiceError(
                    "stale Runtime update generation",
                    status=409,
                    code="runtime_update_stale_generation",
                )
            if existing.get("target") != request["target"]:
                self._conflict("Runtime update target is immutable after preparation")
        self._maintenance = self.repository.maintenance()
        maintenance = self._maintenance
        if require_maintenance and not maintenance:
            self._conflict("Runtime update maintenance fence is not active")
        if maintenance:
            if maintenance.get("operation_id") != request["operation_id"]:
                self._conflict("another Runtime update owns the maintenance fence")
            if int(maintenance.get("generation") or 0) != int(request["generation"]):
                raise ServiceError(
                    "Runtime update maintenance generation does not match",
                    status=409,
                    code="runtime_update_stale_generation",
                )
            if maintenance.get("target") != request["target"]:
                self._conflict("Runtime update maintenance target does not match")

    def _record(self, request: Mapping[str, Any], step: str, result: Mapping[str, Any]) -> None:
        existing = self.repository.operation(str(request["operation_id"]))
        steps = dict(existing.get("steps") or {})
        steps[step] = dict(result)
        self.repository.save_operation(
            {
                "operation_id": request["operation_id"],
                "generation": request["generation"],
                "target": dict(request["target"]),
                "steps": steps,
                "updated_at": iso(),
            }
        )

    def _validate_checkpoint(
        self, request: Mapping[str, Any], checkpoint: Mapping[str, Any]
    ) -> None:
        if (
            checkpoint.get("operation_id") != request["operation_id"]
            or int(checkpoint.get("generation") or 0) != int(request["generation"])
            or checkpoint.get("target") != request["target"]
        ):
            self._conflict("Runtime update checkpoint does not match the selected target")

    def _active_counts(self, *, include_leases=True) -> dict[str, int]:
        try:
            kanban_workers = len(self.platform.kanban.active_agent_ids())
        except ServiceError as error:
            raise ServiceError(
                "Runtime cannot verify active Kanban work",
                status=503,
                code="runtime_update_activity_unavailable",
            ) from error
        counts = {
            "conversations": sum(
                1
                for entry in self.platform.conversation_runs._active.values()
                if entry.task is not None and not entry.task.done()
            ),
            "teams": sum(
                1
                for entry in self.platform.team_runs._active.values()
                if entry.task is not None and not entry.task.done()
            ),
            "organization_commands": len(self._active_organization_commands),
            "cron_executions": self.platform.cron.active_execution_count,
            "kanban_workers": kanban_workers,
        }
        try:
            counts["workspace_activity"] = int(
                include_leases and activity_present(self.platform.repository.data_dir)
            )
        except StoreError as error:
            raise ServiceError(
                "Runtime cannot verify workspace activity",
                status=503,
                code="runtime_update_activity_unavailable",
            ) from error
        counts["total"] = sum(counts.values())
        return counts

    async def _cancel_active(self) -> None:
        await self.platform.conversation_runs.shutdown()
        await self.platform.team_runs.shutdown()
        self.platform.kanban.cancel_active_tasks_for_update()
        await self.platform.organization_connector.cancel_active_commands()

    @staticmethod
    def _installed_identity() -> dict[str, Any]:
        return {
            "version": os.getenv("XNOBRAIN_VERSION", "dev"),
            "source_commit": os.getenv("XNOBRAIN_SOURCE_COMMIT", ""),
            "runtime_digest": os.getenv("XNOBRAIN_RUNTIME_DIGEST", ""),
            "data_schema": int(os.getenv("RUNTIME_DATA_SCHEMA", "1")),
        }

    @staticmethod
    def _conflict(message: str) -> None:
        raise ServiceError(message, status=409, code="runtime_update_fence_conflict")

    @staticmethod
    def _storage_error(error: RuntimeUpdateStorageError) -> None:
        raise ServiceError(str(error), status=409, code=error.code) from error


class _UpdateCommand:
    """Hold the cross-process command lock across awaits and translate gate errors."""

    def __init__(self, root):
        self.context = update_operation(root)

    async def __aenter__(self):
        try:
            self.context.__enter__()
        except StoreError as error:
            raise ServiceError(
                "Another update command is active or storage is unavailable",
                status=409,
                code="runtime_update_fence_conflict",
            ) from error
        return self

    async def __aexit__(self, kind, error, traceback):
        self.context.__exit__(kind, error, traceback)
        if isinstance(error, StoreError) and error.code == "custom_page_jobs_active":
            raise ServiceError(
                "Runtime must be fully drained before storage verification",
                status=409,
                code="runtime_update_not_drained",
            ) from error
