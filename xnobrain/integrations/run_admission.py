"""Workspace-scoped Control callback and trusted in-process run lineage."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from urllib.parse import quote

import httpx
from opentelemetry import propagate, trace

from ..feature_flags import enabled
from ..services.base import ServiceError

_admitted = ContextVar("agent_pool_admitted_root", default=None)
_immediate = ContextVar("agent_pool_immediate_submission", default=False)
BOOT_ID = uuid.uuid4().hex
POLICY_VERSION = "agent_pool_memory_admission_v1"


def managed() -> bool:
    """A provisioned identity, rather than missing metrics, selects managed mode."""
    return bool(os.getenv("RUNTIME_WORKSPACE_ID", "").strip())


@contextmanager
def admitted_root(receipt):
    descriptor = None
    receipt = dict(receipt)
    if managed() and receipt.get("_lock_fd") is None:
        from ..repositories.custom_page_locks import acquire
        from .rebalance_cli import data_root

        local_id = "lineage_" + hashlib.sha256(receipt["id"].encode()).hexdigest()
        descriptor = acquire(data_root(), ".capacity-" + local_id + ".lock", shared=True)
        receipt.update(_lock_fd=descriptor, _local_id=local_id)
    token = _admitted.set(receipt)
    try:
        yield
    finally:
        _admitted.reset(token)
        if descriptor is not None:
            os.close(descriptor)


def current_admission():
    current = _admitted.get()
    if current is not None:
        return current
    local_id = os.getenv("XNOBRAIN_ADMISSION_LOCAL_ID", "")
    raw = os.getenv("XNOBRAIN_ADMISSION_FD", "")
    if not raw or not re.fullmatch(r"(?:native|lineage)_[0-9a-f]{64}", local_id):
        return None
    from .rebalance_cli import data_root

    try:
        descriptor = int(raw)
        actual = os.fstat(descriptor)
        expected = (data_root() / (".capacity-" + local_id + ".lock")).stat(follow_symlinks=False)
        if descriptor < 3 or (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            return None
        return {"_lock_fd": descriptor, "_local_id": local_id}
    except (OSError, ValueError):
        return None


def require_admitted():
    if managed() and current_admission() is None:
        raise ServiceError(
            "A new task must wait for resource admission",
            status=503,
            code="capacity_admission_required",
        )


@contextmanager
def immediate_submission():
    token = _immediate.set(True)
    try:
        yield
    finally:
        _immediate.reset(token)


def requires_immediate():
    return _immediate.get()


class AdmissionClient:
    def __init__(self, client=None):
        self.client = client or httpx.AsyncClient(timeout=5, follow_redirects=False)

    async def close(self):
        await self.client.aclose()

    async def call(self, method, path="", body=None):
        action = (
            "observe"
            if method == "GET"
            else (
                "claim"
                if path.endswith("/claim")
                else "receipt"
                if path.endswith("/receipt")
                else "register"
            )
        )
        with trace.get_tracer(__name__).start_as_current_span(
            "run_admission." + action, record_exception=False, set_status_on_exception=False
        ):
            try:
                return await self._call(method, path, body)
            except ServiceError as error:
                logging.getLogger(__name__).warning(
                    "Control run admission request failed",
                    extra={
                        "event": "run_admission_failed",
                        "action": action,
                        "error": "capacity_request_failed",
                        "error_type": type(error).__name__,
                        "http_status_code": error.status,
                    },
                )
                raise

    async def _call(self, method, path="", body=None):
        endpoint = os.getenv("RUNTIME_CONTROL_URL", "").rstrip("/")
        workspace = os.getenv("RUNTIME_WORKSPACE_ID", "").strip()
        secret = os.getenv("RUNTIME_INTERNAL_SERVICE_TOKEN", "").strip()
        if not endpoint or not workspace or not secret:
            raise ServiceError(
                "Resource admission temporarily unavailable",
                status=503,
                code="capacity_service_unavailable",
            )
        headers = {
            "Authorization": "Bearer " + secret,
            "X-XNOBrain-Workspace-ID": workspace,
            "X-XNOBrain-Admission-Policy": POLICY_VERSION,
        }
        propagate.inject(headers)
        try:
            response = await self.client.request(
                method,
                endpoint + "/xnobrain/api/control/internal/v1/run-admissions" + path,
                headers=headers,
                json=body,
            )
            result = response.json()
            if response.status_code >= 400:
                code = (result.get("data") or {}).get("code")
                if code == "capacity_queue_full":
                    raise ServiceError("Task queue is full", status=429, code=code)
                if code == "capacity_policy_mismatch":
                    raise ServiceError("Task admission is being updated", status=503, code=code)
                if response.status_code in {400, 401, 403, 404, 409}:
                    raise ServiceError(
                        "Task admission could not be authorized",
                        status=response.status_code,
                        code=code or "capacity_conflict",
                    )
                raise ServiceError(
                    "Resource admission temporarily unavailable",
                    status=503,
                    code="capacity_service_unavailable",
                )
            data = result["data"]
            if not isinstance(data, dict) or not data.get("id"):
                raise ValueError("invalid admission response")
            if (
                method == "POST"
                and not path.endswith("/receipt")
                and data.get("policy_version") != POLICY_VERSION
            ):
                raise ServiceError(
                    "Task admission is being updated", status=503, code="capacity_policy_mismatch"
                )
            return data
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            raise ServiceError(
                "Resource admission temporarily unavailable",
                status=503,
                code="capacity_service_unavailable",
            ) from error

    async def register(self, run_id, kind="conversation", *, immediate=False):
        if not enabled("AGENT_POOL_MEMORY_ADMISSION"):
            raise ServiceError(
                "New tasks are temporarily paused", status=503, code="capacity_feature_disabled"
            )
        return await self.call(
            "POST", body={"run_id": run_id, "kind": kind, "immediate": immediate}
        )

    async def observe(self, identifier):
        return await self.call("GET", "/" + quote(identifier, safe=""))

    async def claim(self, offer):
        if not enabled("AGENT_POOL_MEMORY_ADMISSION"):
            raise ServiceError(
                "New tasks are temporarily paused", status=503, code="capacity_feature_disabled"
            )
        return await self.call(
            "POST",
            "/" + quote(offer["id"], safe="") + "/claim",
            {"nonce": offer["nonce"], "runtime_boot": BOOT_ID},
        )

    async def receipt(self, admission, state):
        return await self.call(
            "POST",
            "/" + quote(admission["id"], safe="") + "/receipt",
            {
                "nonce": admission.get("nonce", ""),
                "runtime_boot": admission.get("runtime_boot") or BOOT_ID,
                "state": state,
            },
        )
