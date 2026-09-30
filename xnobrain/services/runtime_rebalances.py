"""Graceful VM relocation admission; never cancels or restarts a task."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path

from ..feature_flags import enabled
from ..integrations.runtime_build_identity import canonical_digest, incus_installed_identity
from ..integrations.runtime_data_volume import verify_volume
from ..repositories.base import StoreError
from ..repositories.runtime_update_gate import activity_present, update_operation
from ..repositories.runtime_updates import RuntimeUpdateRepository

CAPABILITY = "workspace_rebalance_runtime_v1"


class RuntimeRebalanceService:
    def __init__(self, platform):
        self.platform = platform
        self.repository = platform.runtime_updates.repository
        self.root = platform.repository.data_dir
        self.receipt_path = self.root / "runtime-updates" / "rebalance-fence.json"
        self._lock = asyncio.Lock()

    @staticmethod
    def conflict():
        raise StoreError(
            "Workspace maintenance fence conflict", status=409, code="rebalance_fence_conflict"
        )

    def _identity(self, identity, *, observation=False):
        workspace = os.getenv("RUNTIME_WORKSPACE_ID", "")
        if not workspace or identity.workspace_id != workspace:
            self.conflict()
        if observation and not identity.operation_id and identity.fence == 0:
            return
        if (
            not re.fullmatch(r"reb_[a-zA-Z0-9_-]{1,100}", identity.operation_id)
            or identity.fence < 1
        ):
            self.conflict()
        if identity.step_id not in {
            f"{identity.operation_id}:{phase}"
            for phase in ("preparing", "migrating", "verifying", "resuming", "aborting")
        }:
            self.conflict()

    def _receipt(self):
        return RuntimeUpdateRepository._read(self.receipt_path)

    def _check_owner(self, identity, receipt, gate):
        if receipt and (
            int(receipt.get("fence", 0)) > identity.fence
            or int(receipt.get("fence", 0)) == identity.fence
            and receipt.get("operation_id") != identity.operation_id
        ):
            self.conflict()
        if gate.get("dispatch_paused") and (
            gate.get("kind") != "vm_rebalance"
            or gate.get("operation_id") != identity.operation_id
            or gate.get("generation") != identity.fence
        ):
            self.conflict()

    async def command(self, identity, action):
        self._identity(identity, observation=action == "status")
        fields = {
            "event": "runtime_rebalance",
            "operation_id": identity.operation_id,
            "workspace_id": identity.workspace_id,
            "fence": identity.fence,
            "action": action if action in {"status", "prepare", "resume"} else "unknown",
        }
        log = logging.getLogger(__name__)
        try:
            result = await self._command(identity, action)
        except Exception as error:
            log.warning(
                "Runtime rebalance command failed",
                extra={
                    **fields,
                    "error": "rebalance_command_failed",
                    "error_type": type(error).__name__,
                },
            )
            raise
        if action != "status":
            log.info(
                "Runtime rebalance command completed",
                extra={
                    **fields,
                    "phase": "paused" if result["paused"] else "resumed",
                    "error": None,
                },
            )
        return result

    async def _command(self, identity, action):
        # Models discovery is a bounded, read-only dependency check. It never
        # submits inference or replays a user task.
        dependencies = False
        try:
            await asyncio.wait_for(self.platform.router._request("GET", "/models"), timeout=3)
            dependencies = True
        except Exception as error:
            logging.getLogger(__name__).warning(
                "Runtime rebalance dependency probe failed",
                extra={
                    "event": "runtime_rebalance_dependency_failed",
                    "operation_id": identity.operation_id,
                    "error": "router_probe_failed",
                    "error_type": type(error).__name__,
                },
            )
        async with self._lock:
            # This lock is also held by update commands, including across awaits.
            with update_operation(self.root):
                receipt = self._receipt()
                gate = self.repository.maintenance()
                if identity.operation_id:
                    self._check_owner(identity, receipt, gate)
                if action == "prepare":
                    if identity.step_id != f"{identity.operation_id}:preparing":
                        self.conflict()
                    if receipt.get("operation_id") == identity.operation_id and receipt.get(
                        "resumed"
                    ):
                        self.conflict()
                    if not enabled("VM_REBALANCE") and not gate.get("dispatch_paused"):
                        self.conflict()
                    observed = self._status(identity)
                    if not dependencies or not observed["build_identity"]:
                        self.conflict()
                    if receipt.get("operation_id") == identity.operation_id and any(
                        receipt.get(key) != observed[key]
                        for key in ("build_identity", "storage_identity")
                    ):
                        self.conflict()
                    receipt = {
                        "operation_id": identity.operation_id,
                        "fence": identity.fence,
                        "resumed": False,
                        "build_identity": observed["build_identity"],
                        "storage_identity": observed["storage_identity"],
                    }
                    self.platform.repository.atomic_json(self.receipt_path, receipt)
                    self.repository.save_maintenance(
                        {
                            "kind": "vm_rebalance",
                            "operation_id": identity.operation_id,
                            "generation": identity.fence,
                            "target": {"kind": "vm_rebalance"},
                            "dispatch_paused": True,
                            "phase": "draining",
                        }
                    )
                    self.platform.organization_connector.dispatch_paused = True
                elif action == "resume":
                    if identity.step_id not in {
                        f"{identity.operation_id}:resuming",
                        f"{identity.operation_id}:aborting",
                    }:
                        self.conflict()
                    if identity.step_id.endswith(":resuming") and (
                        receipt.get("operation_id") != identity.operation_id
                        or receipt.get("fence") != identity.fence
                    ):
                        self.conflict()
                    observed = self._status(identity)
                    if not dependencies or not observed["build_identity"]:
                        self.conflict()
                    if receipt.get("operation_id") == identity.operation_id and any(
                        receipt.get(key) != observed[key]
                        for key in ("build_identity", "storage_identity")
                    ):
                        self.conflict()
                    # Persist the obligation before releasing admission. A retry
                    # after either write safely completes the same resume.
                    receipt = {
                        "operation_id": identity.operation_id,
                        "fence": identity.fence,
                        "resumed": True,
                        "build_identity": observed["build_identity"],
                        "storage_identity": observed["storage_identity"],
                    }
                    self.platform.repository.atomic_json(self.receipt_path, receipt)
                    if gate.get("dispatch_paused"):
                        self.repository.save_maintenance(
                            {
                                "kind": "vm_rebalance",
                                "operation_id": identity.operation_id,
                                "generation": identity.fence,
                                "dispatch_paused": False,
                                "target": {"kind": "vm_rebalance"},
                                "phase": "resumed",
                            }
                        )
                    self.platform.organization_connector.dispatch_paused = False
                status = self._status(identity)
                status["dependencies_ready"] = status["dependencies_ready"] and dependencies
                return status

    def _status(self, identity):
        gate = self.repository.maintenance()
        receipt = self._receipt()
        installed = incus_installed_identity()
        self.platform.repository.storage_mount.check()
        volume_id = ""
        required = os.getenv("RUNTIME_DATA_MOUNT_REQUIRED", "false").lower()
        if required not in {"false", "0", ""}:
            anchor = Path(os.getenv("RUNTIME_DATA_VOLUME_PATH", ""))
            if required not in {"true", "1"} or str(anchor) not in {
                "/srv/xnobrain-data",
                "/opt/data",
            }:
                self.conflict()
            volume_id = os.getenv("RUNTIME_DATA_VOLUME_ID", "")
            verify_volume(anchor, volume_id)
        stat = self.root.stat()
        storage = canonical_digest(
            {
                "device": stat.st_dev,
                "inode": stat.st_ino,
                "root": str(self.root),
                "volume_id": volume_id,
            }
        )
        # Registry checks are defense in depth. OS activity leases also include
        # CLI processes and blocking executors outside the ASGI event loop.
        active = self.platform.runtime_updates._active_counts()["total"]
        bound = (
            receipt.get("operation_id") == identity.operation_id
            and receipt.get("fence") == identity.fence
        )
        return {
            "capability": CAPABILITY if enabled("VM_REBALANCE") or bound else "",
            "operation_id": receipt.get("operation_id", "") if bound else "",
            "fence": receipt.get("fence", 0) if bound else 0,
            "paused": bool(gate.get("dispatch_paused")),
            "idle": not active and not activity_present(self.root),
            "resumed": bool(bound and receipt.get("resumed") and not gate.get("dispatch_paused")),
            "build_identity": installed.get("build_descriptor_digest", ""),
            "dependencies_ready": bool(installed),
            "storage_identity": storage,
            "data_volume_id": volume_id,
        }
