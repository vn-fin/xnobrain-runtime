"""Continuous-idle authority and durable, fenced guest reclaim operations."""

from __future__ import annotations

import asyncio
import os
import re
import secrets
import time

from ..feature_flags import enabled
from ..integrations.workspace_memory import (
    GuestMemoryHelper,
    managed_process_coverage,
    memory_sample,
    reporting_backend,
)
from ..repositories.base import StoreError
from ..repositories.runtime_update_gate import activity_present, admission_gate, update_operation
from ..repositories.runtime_updates import RuntimeUpdateRepository
from ..repositories.workspace_memory import WorkspaceMemoryRepository

CAPABILITY = "workspace_idle_memory_reclaim_v1"
KIND = "workspace_memory_reclaim"
TERMINAL = {"evicted", "skipped", "failed", "outcome_unknown"}


def policy_values() -> dict:
    values = {
        "idle_seconds": int(os.getenv("RUNTIME_WORKSPACE_IDLE_MEMORY_IDLE_SECONDS", "600")),
        "scan_seconds": int(os.getenv("RUNTIME_WORKSPACE_IDLE_MEMORY_SCAN_SECONDS", "60")),
        "min_cache_bytes": int(os.getenv("RUNTIME_WORKSPACE_IDLE_MEMORY_MIN_CACHE_BYTES", "0")),
        "retry_seconds": int(os.getenv("RUNTIME_WORKSPACE_IDLE_MEMORY_RETRY_SECONDS", "1800")),
    }
    if (
        values["idle_seconds"] not in {30, 60, 600}
        or values["scan_seconds"] not in {5, 60}
        or values["min_cache_bytes"] < 0
        or values["retry_seconds"] < 1800
    ):
        raise ValueError("invalid memory policy")
    values["revision"] = "memory-v1:" + ":".join(str(value) for value in values.values())
    return values


class WorkspaceMemoryService:
    def __init__(
        self, platform, *, helper=None, tracker=None, sample=None, backend=None, coverage=None
    ):
        self.platform = platform
        self.root = platform.repository.data_dir
        self.workspace_id = os.getenv("RUNTIME_WORKSPACE_ID", "")
        self.tracker = tracker or WorkspaceMemoryRepository(self.root, self.workspace_id)
        self.updates = platform.runtime_updates.repository
        self.receipt_path = self.root / "runtime-memory" / "reclaim.json"
        self.helper = helper or GuestMemoryHelper()
        self.sample = sample or memory_sample
        self.backend = backend or reporting_backend
        self.coverage = coverage or managed_process_coverage
        self._lock = asyncio.Lock()
        self._task = None

    def start(self):
        if self.workspace_id:
            with admission_gate(self.root):
                self.tracker.start(busy=True)

    @staticmethod
    def conflict(reason="memory_reclaim_conflict"):
        raise StoreError("Workspace memory reclaim unavailable", status=409, code=reason)

    def _policy(self, policy):
        try:
            values = policy_values()
        except ValueError:
            self.conflict("memory_policy_unavailable")
        if any(getattr(policy, key, None) != value for key, value in values.items()):
            self.conflict("memory_policy_mismatch")
        return values

    def _observe(self):
        counts = 0
        complete = bool(self.workspace_id)
        try:
            counts = self.platform.runtime_updates._active_counts(include_leases=False)["total"]
            counts += int(activity_present(self.root, business_only=True))
            if not counts:
                complete = complete and self.coverage()
        except Exception:
            complete = False
        value = self.tracker.observe(busy=bool(counts), coverage_complete=complete)
        keys = {
            "workspace_id",
            "guest_boot_id",
            "runtime_boot_id",
            "activity_epoch",
            "idle_duration_ms",
            "state",
            "reason",
            "coverage_complete",
        }
        result = {key: item for key, item in value.items() if key in keys}
        result["state"] = value["activity_state"]
        result.update(capability=CAPABILITY, active_count=counts, backend="")
        try:
            result.update(self.sample())
            result["backend"] = self.backend()
            result["policy_revision"] = policy_values()["revision"]
        except (OSError, ValueError, KeyError):
            result.update(state="unknown", reason="memory_evidence_unavailable")
        return result

    def activity(self, workspace_id, policy):
        if not self.workspace_id or workspace_id != self.workspace_id:
            self.conflict()
        self._policy(policy)
        with admission_gate(self.root):
            return self._observe()

    def _identity(self, identity):
        if (
            not self.workspace_id
            or identity.workspace_id != self.workspace_id
            or not re.fullmatch(r"mem_[a-zA-Z0-9_-]{1,100}", identity.operation_id)
            or identity.fence < 1
            or identity.activity_epoch < 0
            or not identity.guest_boot_id
            or not identity.runtime_boot_id
        ):
            self.conflict()
        return {
            "operation_id": identity.operation_id,
            "fence": identity.fence,
            "workspace_id": identity.workspace_id,
            "guest_boot_id": identity.guest_boot_id,
            "runtime_boot_id": identity.runtime_boot_id,
            "activity_epoch": identity.activity_epoch,
            "policy_revision": identity.policy.revision,
        }

    def _read(self):
        return RuntimeUpdateRepository._read(self.receipt_path)

    def _save(self, value):
        self.platform.repository.atomic_json(self.receipt_path, value)

    def _gate(self, receipt, paused):
        # Caller holds admission. Do not nest save_maintenance's admission lock.
        self.platform.repository.atomic_json(
            self.updates.maintenance_path,
            {
                "kind": KIND,
                "operation_id": receipt["operation_id"],
                "generation": receipt["fence"],
                "target": {"kind": KIND},
                "dispatch_paused": paused,
                "phase": receipt["state"],
            },
        )

    def _release(self):
        # Caller holds admission. Remove the fence rather than unpause it: any
        # remaining maintenance record, paused or not, belongs to another owner
        # for Runtime updates, which then refuse to drain this workspace.
        try:
            self.updates.maintenance_path.unlink()
        except FileNotFoundError:
            return
        self.platform.repository._sync_dir(self.updates.maintenance_path.parent)

    def _eligible(self, identity, policy):
        value = self._observe()
        if (
            not enabled("WORKSPACE_IDLE_MEMORY_RECLAIM")
            or not value.get("coverage_complete")
            or value.get("state") != "idle"
            or value.get("active_count")
            or value.get("idle_duration_ms", 0) < policy["idle_seconds"] * 1000
            or any(
                value.get(key) != getattr(identity, key)
                for key in ("guest_boot_id", "runtime_boot_id", "activity_epoch")
            )
            or not value.get("backend")
            # drop_caches discards only clean pages: dirty/writeback pages and
            # free memory do not affect safety. The cache floor is optional.
            or value.get("cached_bytes", 0) < policy["min_cache_bytes"]
        ):
            self.conflict("memory_not_eligible")
        return value

    async def command(self, identity, action, prepare_token=""):
        binding = self._identity(identity)
        async with self._lock:
            if action in {"prepare", "execute"} and not await self.helper.stopped():
                # Never fence an idle VM merely because the helper is missing.
                # Existing execution remains observable/recoverable below.
                previous = self._read()
                if (
                    previous.get("operation_id") != identity.operation_id
                    or previous.get("state") != "executing"
                ):
                    self.conflict("memory_helper_unavailable")
            if action in {"status", "finish"}:
                await self._recover()
            with update_operation(self.root), admission_gate(self.root):
                receipt = self._read()
                gate = self.updates.maintenance()
                same = receipt and all(receipt.get(key) == value for key, value in binding.items())
                if gate.get("dispatch_paused") and (
                    gate.get("kind") != KIND
                    or gate.get("operation_id") != identity.operation_id
                    or gate.get("generation") != identity.fence
                ):
                    self.conflict()
                if (
                    receipt
                    and not same
                    and (
                        receipt.get("fence", 0) >= identity.fence
                        or not receipt.get("finished")
                        or all(
                            receipt.get(key) == binding[key]
                            for key in ("guest_boot_id", "runtime_boot_id", "activity_epoch")
                        )
                    )
                ):
                    self.conflict()
                if action == "prepare" and not same:
                    policy = self._policy(identity.policy)
                    self._eligible(identity, policy)
                    receipt = {
                        **binding,
                        "state": "prepared",
                        "prepare_token": secrets.token_hex(32),
                        "prepared_at_ns": time.monotonic_ns(),
                        "finished": False,
                    }
                    self._save(receipt)
                elif action == "execute":
                    if not same or not secrets.compare_digest(
                        str(receipt.get("prepare_token", "")), prepare_token
                    ):
                        self.conflict()
                    if receipt["state"] == "prepared":
                        policy = self._policy(identity.policy)
                        if time.monotonic_ns() - receipt["prepared_at_ns"] > 10_000_000_000:
                            self.conflict("memory_prepare_expired")
                        observed = self._eligible(identity, policy)
                        # Intent before the maintenance gate and before socket activation.
                        receipt.update(
                            state="executing", cached_before_bytes=observed["cached_bytes"]
                        )
                        self._save(receipt)
                        self._gate(receipt, True)
                        self._task = asyncio.create_task(self._execute(receipt.copy()))
                elif action in {"status", "finish"}:
                    if not same:
                        # A lost prepare response must be recoverable even when
                        # prepare never arrived. Persist a tombstone on finish
                        # so a delayed prepare/execute cannot start afterwards.
                        if gate.get("dispatch_paused"):
                            self.conflict()
                        receipt = {**binding, "state": "absent", "helper_stopped": True}
                    if action == "finish":
                        if receipt["state"] in {"prepared", "absent"}:
                            receipt.update(
                                state="skipped",
                                reason="cancelled_before_execution",
                                helper_stopped=True,
                            )
                        if receipt["state"] not in TERMINAL or gate.get("dispatch_paused"):
                            self.conflict("memory_helper_unresolved")
                        receipt["finished"] = True
                        self._save(receipt)
                elif action != "prepare":
                    self.conflict()
                return self._status(receipt)

    def _status(self, receipt):
        gate = self.updates.maintenance()
        keys = {
            "operation_id",
            "fence",
            "state",
            "reason",
            "prepare_token",
            "cached_before_bytes",
            "cached_after_bytes",
        }
        return {
            **{key: value for key, value in receipt.items() if key in keys},
            "paused": bool(gate.get("dispatch_paused")),
            "helper_stopped": bool(receipt.get("helper_stopped")),
            "activity": self._observe(),
        }

    async def _execute(self, receipt):
        try:
            if not await self.helper.stopped():
                return
            await self.helper.execute(receipt["operation_id"], receipt["fence"])
        except Exception:
            # Ambiguous transport result is resolved from root receipt and actual cgroup exit.
            pass
        finally:
            # A disconnected RPC never owns/cancels this operation task.
            for _ in range(10):
                try:
                    if await self._recover():
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.2)

    async def _recover(self):
        if (
            self._task is not None
            and not self._task.done()
            and self._task is not asyncio.current_task()
        ):
            return False
        receipt = self._read()
        gate = self.updates.maintenance()
        if not receipt or (
            receipt.get("state") != "executing"
            and not (
                gate.get("dispatch_paused")
                and gate.get("kind") == KIND
                and gate.get("operation_id") == receipt.get("operation_id")
            )
        ):
            return True
        if not await self.helper.stopped():
            return False
        evidence = self.helper.receipt()
        matched = all(
            evidence.get(key) == receipt[key] for key in ("operation_id", "fence", "guest_boot_id")
        )
        # An inactive service alone does not exclude a queued socket activation.
        # A root intent receipt makes any later delivery a deduplicated no-op.
        # A guest reboot also proves that old socket jobs cannot survive.
        if not matched and self.tracker.boot_id() == receipt["guest_boot_id"]:
            return False
        with update_operation(self.root), admission_gate(self.root):
            current = self._read()
            if current != receipt:
                return False
            if receipt.get("state") == "executing":
                state = evidence.get("state") if matched else "outcome_unknown"
                receipt.update(
                    state=state if state in TERMINAL else "outcome_unknown", helper_stopped=True
                )
                if matched:
                    receipt["cached_after_bytes"] = evidence.get("cached_after_bytes", 0)
                    receipt["reason"] = evidence.get("reason", "")
                else:
                    receipt["reason"] = "helper_receipt_unavailable"
            self._save(receipt)
            gate = self.updates.maintenance()
            if gate.get("dispatch_paused"):
                if (
                    gate.get("kind") != KIND
                    or gate.get("operation_id") != receipt["operation_id"]
                    or gate.get("generation") != receipt["fence"]
                ):
                    self.conflict()
                self._release()
            return True
