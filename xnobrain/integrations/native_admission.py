"""Admission before native claims; task inputs and recurrence stay in native stores."""

from __future__ import annotations

import asyncio
import hashlib
from contextlib import contextmanager, suppress

from ..repositories.base import StoreError
from ..repositories.custom_page_locks import acquire, release
from ..repositories.run_admission import AdmissionRepository
from ..repositories.runtime_update_gate import WorkspaceActivity
from ..services.base import ServiceError
from .run_admission import BOOT_ID, AdmissionClient, admitted_root, managed


def call(method, *args, **kwargs):
    async def request():
        client = AdmissionClient()
        try:
            return await getattr(client, method)(*args, **kwargs)
        finally:
            await client.close()

    return asyncio.run(request())


def waiting_projection(root, key):
    if not managed() or not (root / "agent-run-admission.sqlite").is_file():
        return None
    identifier = "native_" + hashlib.sha256(key.encode()).hexdigest()
    row = AdmissionRepository(root).native_get(identifier)
    if row is None or row["state"] not in {"waiting", "claiming"}:
        return None
    return {
        "state": "waiting",
        "reason_code": row["admission"].get("reason_code") or "capacity_recovering",
        "retry_after_ms": 5000,
    }


class NativeAdmission:
    def __init__(self, root, key, kind, *, identifier=None):
        self.root = root
        self.id = identifier or "native_" + hashlib.sha256(key.encode()).hexdigest()
        self.kind = kind
        self.key = key
        self.store = AdmissionRepository(root)
        self.descriptor = None
        self.admission = None

    def save(self, state):
        self.store.native_put(self.id, self.kind, state, self.admission or {}, self.key)

    def poll(self):
        admitted = False
        try:
            self.descriptor = acquire(self.root, ".capacity-" + self.id + ".lock", shared=False)
            row = self.store.native_get(self.id)
            if row and row["state"] not in {"waiting", "claiming"}:
                return False
            self.admission = row["admission"] if row else {}
            if not row:
                # Journal before registration, including a lost HTTP response.
                with WorkspaceActivity(self.root):
                    self.save("waiting")
            self.admission = (
                call("observe", self.admission["id"])
                if self.admission
                else call("register", self.id, self.kind)
            )
            if self.admission["state"] in {"cancelled", "finished", "rejected"}:
                self.save("settled")
                return False
            if self.admission["state"] == "starting":
                # Lost claim response, before executor handoff: safe to abort.
                call("receipt", self.admission, "aborted")
                self.admission = {}
                self.save("waiting")
                return False
            self.save("waiting")
            if self.admission["state"] != "offered":
                return False
            self.admission["runtime_boot"] = BOOT_ID
            self.save("claiming")
            self.admission = call("claim", self.admission)
            if self.admission["state"] != "starting":
                self.save("waiting")
                return False
            self.save("starting")
            admitted = True
            return True
        except (ServiceError, StoreError, OSError):
            return False
        finally:
            if not admitted:
                self.close()

    def started(self):
        self.save("running")
        with suppress(ServiceError):
            call("receipt", self.admission, "running")

    def finish(self):
        self.save("finished")
        try:
            call("receipt", self.admission, "finished")
            self.save("settled")
        except ServiceError:
            pass
        finally:
            self.close()

    def abort(self):
        self.save("cancelled")
        try:
            if not self.admission:
                self.admission = call("observe", self.id)
            call("receipt", self.admission, "cancelled")
            self.save("settled")
        except ServiceError as error:
            if not self.admission and error.status in {400, 404}:
                self.save("settled")
        finally:
            self.close()

    def defer(self):
        """No executor was handed off: release this grant and retain the intent."""
        try:
            call("receipt", self.admission, "aborted")
            self.admission = {}
            self.save("waiting")
        except ServiceError:
            # Reconciliation of a 'claiming' journal can prove no handoff.
            self.save("claiming")
        finally:
            self.close()

    def close(self):
        if self.descriptor is not None:
            release(self.descriptor)
            self.descriptor = None


@contextmanager
def native_root(root, key, kind):
    if not managed():
        yield True
        return
    gate = NativeAdmission(root, key, kind)
    if not gate.poll():
        yield False
        return
    try:
        from ..repositories.runtime_update_gate import WorkspaceActivity

        try:
            activity = WorkspaceActivity(root)
        except StoreError:
            gate.defer()
            yield False
            return
        with activity:
            gate.started()
            with admitted_root(gate.admission):
                yield True
    finally:
        if gate.descriptor is not None:
            gate.finish()


def cancel_waiting(root, key):
    """Cancel under the same kernel fence used by the native claim adapter."""
    gate = NativeAdmission(root, key, "kanban")
    gate.descriptor = acquire(root, ".capacity-" + gate.id + ".lock", shared=False)
    try:
        row = gate.store.native_get(gate.id)
        if not row or row["state"] not in {"waiting", "claiming"}:
            return False
        gate.admission = row["admission"]
        gate.abort()
        return True
    finally:
        gate.close()


def reconcile_native(root, source_exists=None):
    store = AdmissionRepository(root)
    for identifier in store.native_pending():
        row = store.native_get(identifier)
        gate = NativeAdmission(root, "", row["kind"], identifier=identifier)
        gate.admission = row["admission"]
        try:
            gate.descriptor = acquire(root, ".capacity-" + identifier + ".lock", shared=False)
        except StoreError:
            if row["state"] == "running":
                with suppress(ServiceError):
                    call("receipt", gate.admission, "running")
            continue
        try:
            if not gate.admission and row["state"] == "cancelled":
                gate.abort()
                continue
            if not gate.admission and row["state"] != "waiting":
                continue
            if row["state"] == "waiting":
                if row["kind"] == "cli":
                    owner = None
                    try:
                        owner = acquire(
                            root, ".capacity-owner-" + identifier + ".lock", shared=False
                        )
                        gate.abort()
                    except StoreError:
                        pass  # The CLI still owns its waiting turn.
                    finally:
                        if owner is not None:
                            release(owner)
                    continue
                key = store.native_source(identifier)
                if source_exists is not None and key and not source_exists(row["kind"], key):
                    gate.abort()
                continue
            if row["state"] == "claiming":
                gate.admission = call("observe", gate.admission["id"])
                if gate.admission["state"] == "starting":
                    gate.defer()
                else:
                    gate.save("waiting")
                continue
            # A dead started worker never causes an automatic side-effect replay.
            gate.abort() if row["state"] in {"claiming", "starting", "cancelled"} else gate.finish()
        except (ServiceError, StoreError, OSError, ValueError):
            pass
        finally:
            gate.close()
