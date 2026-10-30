"""Outbound-only connector for delegated organization work.

The member's browser hands this Runtime a one-time enrollment token per
organization. Each organization then gets its own device credential and its
own loop for inventory, heartbeat and command polling, so one organization's
failure never stops another.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ..integrations import organization_funding
from ..repositories.base import StoreError
from ..repositories.organization_connector import (
    ORGANIZATION_ID,
    OrganizationConnectorRepository,
)
from ..repositories.runtime_update_gate import workspace_activity
from ..trusted_context import TrustedRequestContext
from .base import ServiceError

logger = logging.getLogger(__name__)

TERMINAL = {"completed", "failed", "timed_out", "cancelled"}
MAX_BACKOFF_SECONDS = 300
MAINTENANCE_CODES = frozenset(
    {
        "runtime_update_maintenance",
        "workspace_rebalance_maintenance",
        "workspace_memory_maintenance",
    }
)


class EnrollmentRejected(Exception):
    """Control refused the enrollment token; a new token is required."""


class DeviceRevoked(Exception):
    """Control no longer accepts this device's refresh token."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OrganizationConnector:
    def __init__(self, platform):
        self.platform = platform
        self.store = OrganizationConnectorRepository(platform.repository)
        self.endpoint = os.getenv("RUNTIME_CONTROL_URL", "").rstrip("/")
        self.env_organization = os.getenv("RUNTIME_ORGANIZATION_ID", "")
        self.env_proof = os.getenv("RUNTIME_RUNNER_ENROLLMENT_PROOF", "")
        self.interval = max(2, int(os.getenv("RUNTIME_CONNECTOR_INTERVAL_SECONDS", "10")))
        self.task = None
        self.stop_event = asyncio.Event()
        self.wake = asyncio.Event()
        self.dispatch_paused = False
        self.active_commands: dict[str, tuple[str, str, str]] = {}
        self.links: dict[str, asyncio.Task] = {}
        self.key_path = self.store.root / "device-ed25519.key"

    @property
    def enabled(self):
        return bool(self.endpoint)

    async def start(self):
        if self.enabled and self.task is None:
            self.seed_env_connection()
            self.task = asyncio.create_task(self.run(), name="xnobrain-organization-connector")

    def seed_env_connection(self) -> None:
        """Unattended installs pass one Control-issued token through the env."""
        organization, proof = self.env_organization, self.env_proof.strip()
        if not (organization and proof) or not ORGANIZATION_ID.match(organization):
            return
        store = self.store.for_organization(organization)
        if store.state():
            return
        token = proof if proof.startswith(f"{organization}.") else f"{organization}.{proof}"
        store.save_state({"status": "pending", "enrollment_token": token, "updated_at": _now()})

    async def pause_dispatch(self) -> None:
        self.dispatch_paused = True

    def resume_dispatch(self) -> None:
        self.dispatch_paused = False

    async def cancel_active_commands(self) -> None:
        for agent_id, conversation_id, run_id in list(self.active_commands.values()):
            try:
                await self.platform.stop_run(agent_id, conversation_id, run_id)
            except Exception:
                continue

    async def stop(self):
        self.stop_event.set()
        tasks = [task for task in (self.task, *self.links.values()) if task]
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.links.clear()

    def key(self):
        if self.key_path.exists():
            return Ed25519PrivateKey.from_private_bytes(self.key_path.read_bytes())
        key = Ed25519PrivateKey.generate()
        self.platform.repository.atomic_write(
            self.key_path,
            key.private_bytes(
                serialization.Encoding.Raw,
                serialization.PrivateFormat.Raw,
                serialization.NoEncryption(),
            ),
            mode=0o600,
            replace=False,
        )
        return key

    def inventory(self):
        activity = self.platform.agent_activity()["agents"]
        return [
            {
                "agent_public_id": a["id"],
                "display_name": a.get("display_name") or a.get("name") or a["id"],
                # Control refuses personal worker dispatch to older runtimes
                # that would interpret the command as organization-owned work.
                "capabilities": ["personal_execution_v1"],
                "availability": "busy" if activity.get(a["id"]) == "running" else "available",
                "revision": 1,
            }
            for a in self.platform.list_agents()
        ]

    @staticmethod
    def execution_body(cmd, instruction, deadline):
        allowed_tools = list(cmd.get("allowed_toolsets") or [])
        allowed_skills = list(cmd.get("allowed_skills") or [])
        limits = dict(cmd.get("limits") or {})
        seconds = int(limits.get("max_duration_seconds") or 3600)
        seconds = min(
            seconds, max(30, int((deadline - datetime.now(timezone.utc)).total_seconds()))
        )
        return {
            "input": instruction,
            "run_mode": "background",
            "timeout_seconds": seconds,
            "toolsets": allowed_tools,
            "skills": allowed_skills,
            "organization_command": True,
            "approval_mode": cmd.get("approval_mode") or "local",
        }

    def link(self, organization_id: str) -> OrganizationLink:
        return OrganizationLink(self, self.store.for_organization(organization_id))

    # Browser-facing connection management. The browser only ever supplies a
    # Control-issued enrollment token, never its own credential.

    def connections(self) -> dict[str, Any]:
        return {"items": [self.link(org).view() for org in self.store.organizations()]}

    def connect(self, body: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            raise StoreError(
                "organization connector is not configured",
                status=503,
                code="organization_connector_unavailable",
            )
        organization = str(body.get("organization_id") or "")
        token = str(body.get("enrollment_token") or "").strip()
        if not ORGANIZATION_ID.match(organization) or not token.startswith(f"{organization}."):
            raise StoreError(
                "enrollment token does not match the organization",
                status=400,
                code="invalid_enrollment_token",
            )
        self._stop_link(organization)
        link = self.link(organization)
        link.store.save_state(
            {"status": "pending", "enrollment_token": token, "updated_at": _now()}
        )
        self.wake.set()
        return link.view()

    def disconnect(self, organization_id: str) -> dict[str, Any]:
        if not ORGANIZATION_ID.match(organization_id):
            raise StoreError("invalid organization id", status=400, code="invalid_organization_id")
        self._stop_link(organization_id)
        self.store.for_organization(organization_id).remove()
        return {"organization_id": organization_id, "state": "removed"}

    def _stop_link(self, organization_id: str) -> None:
        task = self.links.pop(organization_id, None)
        if task is not None:
            task.cancel()

    def _sync_links(self, client) -> None:
        stored = set(self.store.organizations())
        for organization in list(self.links):
            if organization not in stored:
                self._stop_link(organization)
        for organization in stored:
            task = self.links.get(organization)
            if task is None or task.done():
                self.links[organization] = asyncio.create_task(
                    self.link(organization).run(client),
                    name=f"xnobrain-organization-connector-{organization}",
                )

    async def run(self):
        async with httpx.AsyncClient(timeout=20, verify=True) as client:
            while not self.stop_event.is_set():
                self._sync_links(client)
                self.wake.clear()
                try:
                    await asyncio.wait_for(self.wake.wait(), timeout=self.interval)
                except asyncio.TimeoutError:
                    pass


class OrganizationLink:
    """Connection to one organization, sharing the Runtime's device key."""

    def __init__(self, connector: OrganizationConnector, store: OrganizationConnectorRepository):
        self.connector = connector
        self.store = store
        self.organization = store.organization_id
        self.delay = connector.interval
        self.running: dict[str, asyncio.Task] = {}

    @property
    def platform(self):
        return self.connector.platform

    @property
    def endpoint(self):
        return self.connector.endpoint

    @property
    def dispatch_paused(self):
        return self.connector.dispatch_paused

    @property
    def active_commands(self):
        return self.connector.active_commands

    def key(self):
        return self.connector.key()

    def execution_body(self, cmd, instruction, deadline):
        return self.connector.execution_body(cmd, instruction, deadline)

    def view(self) -> dict[str, Any]:
        state = self.store.state()
        return {
            "organization_id": self.organization,
            "state": state.get("status") or "pending",
            "last_heartbeat_at": state.get("last_heartbeat_at"),
            "error_code": state.get("error_code"),
        }

    def _mark(self, status: str, error_code: str | None = None, **changes) -> dict[str, Any]:
        state = self.store.state()
        state.update(changes)
        state.update({"status": status, "error_code": error_code, "updated_at": _now()})
        self.store.save_state(state)
        return state

    @workspace_activity(lambda self: self.platform.repository.data_dir)
    def failed(self, error: Exception, delay: float) -> float | None:
        """Record a failed tick; return the next delay or None to stop."""
        if isinstance(error, EnrollmentRejected):
            self._mark("error", "enrollment_token_invalid", enrollment_token=None)
            return None
        if isinstance(error, DeviceRevoked):
            self._mark("revoked", "device_revoked", access_token=None, refresh_token=None)
            return None
        if isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 401:
            # Force a refresh on the next tick; a refused refresh revokes.
            self._mark("error", "device_unauthorized", expires_at="1970-01-01T00:00:00+00:00")
            return self.connector.interval
        code = (
            "control_unreachable" if isinstance(error, httpx.TransportError) else "connector_failed"
        )
        self._mark("error", code)
        return min(MAX_BACKOFF_SECONDS, max(self.connector.interval, delay * 2))

    async def run_once(self, client) -> float | None:
        """Run one tick; return the delay before the next or None to stop."""
        try:
            try:
                await self.tick(client)
            except asyncio.CancelledError:
                raise
            except StoreError as error:
                if error.code in MAINTENANCE_CODES:
                    raise
                self.delay = self.failed(error, self.delay)
            except Exception as error:
                self.delay = self.failed(error, self.delay)
            else:
                self.delay = self.connector.interval
        except StoreError as error:
            # Admission may close before tick OR between a failed tick and its
            # error write. Maintenance is a pause, never a connection failure.
            if error.code not in MAINTENANCE_CODES:
                raise
            self.delay = self.connector.interval
        return self.delay

    async def run(self, client):
        try:
            while not self.connector.stop_event.is_set():
                delay = await self.run_once(client)
                if delay is None:
                    return
                await asyncio.sleep(delay)
        finally:
            for task in list(self.running.values()):
                task.cancel()

    def dispatch(self, client, state, cmd) -> None:
        """Run a command beside the loop so heartbeats continue during it."""
        cid = str(cmd.get("command_id") or "")
        if not cid or cid in self.running:
            return
        task = asyncio.create_task(
            self.command(client, state, cmd), name=f"xnobrain-organization-command-{cid}"
        )
        self.running[cid] = task

        def finished(done: asyncio.Task) -> None:
            self.running.pop(cid, None)
            if not done.cancelled() and done.exception() is not None:
                # An unacknowledged command's lease expires and Control
                # re-queues it; the error itself stays local.
                logger.warning(
                    "organization command failed",
                    extra={
                        "command_id": cid,
                        "error": getattr(done.exception(), "code", "")
                        or type(done.exception()).__name__,
                    },
                )

        task.add_done_callback(finished)

    def headers(self, state):
        # Long commands outlive the 15-minute access token; always send the
        # token the loop refreshed most recently.
        token = self.store.state().get("access_token") or state["access_token"]
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    async def enroll(self, client, token):
        key = self.key()
        pub = key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        response = await client.post(
            f"{self.endpoint}/xnobrain/api/control/v1/runner/enrollment-challenges",
            json={"enrollment_token": token, "device_public_key": base64.b64encode(pub).decode()},
        )
        if 400 <= response.status_code < 500:
            raise EnrollmentRejected
        response.raise_for_status()
        challenge = response.json()["data"]
        response = await client.post(
            f"{self.endpoint}/xnobrain/api/control/v1/runner/enroll",
            json={
                "challenge_id": challenge["challenge_id"],
                "signature": base64.b64encode(key.sign(challenge["challenge"].encode())).decode(),
                "device_metadata": {"runtime_version": os.getenv("XNOBRAIN_VERSION", "dev")},
            },
        )
        if 400 <= response.status_code < 500:
            raise EnrollmentRejected
        response.raise_for_status()
        enrolled = response.json()["data"]
        state = {
            "status": "pending",
            "device_id": enrolled["device_id"],
            "access_token": enrolled["access_token"],
            "expires_at": enrolled["expires_at"],
            "refresh_token": enrolled["refresh_token"],
            "heartbeat_sequence": 0,
            "inventory_revision": 0,
            "updated_at": _now(),
        }
        self.store.save_state(state)
        return state

    async def refresh_if_needed(self, client, state):
        expires = datetime.fromisoformat(str(state["expires_at"]).replace("Z", "+00:00"))
        if (expires - datetime.now(timezone.utc)).total_seconds() > 60:
            return state
        nonce = str(time.time_ns())
        signature = base64.b64encode(self.key().sign(nonce.encode())).decode()
        response = await client.post(
            f"{self.endpoint}/xnobrain/api/control/v1/runner/token",
            json={"refresh_token": state["refresh_token"], "nonce": nonce, "signature": signature},
        )
        if response.status_code in {401, 403}:
            raise DeviceRevoked
        response.raise_for_status()
        value = response.json()["data"]
        state["access_token"], state["expires_at"] = value["access_token"], value["expires_at"]
        self.store.save_state(state)
        return state

    async def send_outbox(self, client, state):
        for item in self.store.outbox():
            response = await client.post(
                item["url"], headers=self.headers(state), json=item["body"]
            )
            if response.status_code < 300:
                self.store.complete(item["idempotency_key"])

    async def renew(self, client, state, cmd, stop):
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=10)
                continue
            except asyncio.TimeoutError:
                pass
            response = await client.post(
                f"{self.endpoint}/xnobrain/api/control/v1/organizations/{self.organization}/runner/commands/{cmd['command_id']}/lease",
                headers=self.headers(state),
                json={
                    "lease_id": cmd["lease_id"],
                    "extend_seconds": 30,
                    "progress_state": "running",
                },
            )
            if response.status_code in {401, 403, 404, 409, 410}:
                raise asyncio.CancelledError
            response.raise_for_status()

    async def import_inputs(self, client, state, cmd, agent_id):
        response = await client.get(
            f"{self.endpoint}/xnobrain/api/control/v1/organizations/{self.organization}/runner/commands/{cmd['command_id']}/artifacts",
            headers=self.headers(state),
        )
        response.raise_for_status()
        imported = []
        for item in response.json().get("data", {}).get("items", []):
            destination = f"organization-runs/{cmd['run_id']}/inputs/{item['name']}"
            result = self.platform.import_organization_artifact(
                agent_id,
                {
                    "file_id": item["artifact_id"],
                    "version_id": item["version_id"],
                    "name": item["name"],
                    "size_bytes": item["size_bytes"],
                    "sha256": item["digest"],
                    "media_type": item["media_type"],
                    "transfer": item["transfer"],
                    "destination": destination,
                    "collision": "cancel",
                    "run_id": cmd["run_id"],
                },
            )
            imported.append(
                {
                    "artifact_id": item["artifact_id"],
                    "version_id": item["version_id"],
                    "path": result["path"],
                }
            )
        return imported

    async def publish_outputs(self, client, state, cmd, agent_id):
        root = (
            self.platform.agents.workspace_dir(agent_id)
            / "organization-runs"
            / cmd["run_id"]
            / "outputs"
        ).resolve()
        workspace = self.platform.agents.workspace_dir(agent_id).resolve()
        artifacts = []
        if not root.is_dir():
            return artifacts
        for source in sorted(root.rglob("*")):
            if source.is_symlink() or not source.is_file():
                continue
            relative = source.relative_to(workspace).as_posix()
            info = self.platform.inspect_organization_artifact(agent_id, {"path": relative})
            key = f"{cmd['idempotency_key']}:{hashlib.sha256(relative.encode()).hexdigest()}"
            response = await client.post(
                f"{self.endpoint}/xnobrain/api/control/v1/organizations/{self.organization}/runner/commands/{cmd['command_id']}/output-uploads",
                headers={**self.headers(state), "Idempotency-Key": key},
                json={
                    # Control records the agent as the executor of the upload.
                    "agent_id": agent_id,
                    "name": source.name,
                    "size_bytes": info["size_bytes"],
                    "media_type": "application/octet-stream",
                    "sha256": info["sha256"],
                    "classification": "normal",
                },
            )
            response.raise_for_status()
            upload = response.json()["data"]
            parts = []
            if upload.get("multipart_parts"):
                part_size = int(upload.get("part_size_bytes") or 0)
                if part_size <= 0:
                    raise ValueError("invalid multipart upload authorization")
                with source.open("rb") as stream:
                    for authorization in upload["multipart_parts"]:
                        chunk = stream.read(part_size)
                        if not chunk:
                            raise ValueError("multipart upload authorization has too many parts")
                        transfer = {
                            "url": authorization["url"],
                            "method": "PUT",
                            "expires_at": authorization["expires_at"],
                            "headers": {"Content-Length": str(len(chunk))},
                        }
                        etag = self.platform.publish_organization_artifact_part(transfer, chunk)
                        parts.append(
                            {"part_number": int(authorization["part_number"]), "etag": etag}
                        )
                    if stream.read(1):
                        raise ValueError("multipart upload authorization has too few parts")
            else:
                self.platform.publish_organization_artifact(
                    agent_id,
                    {
                        "path": relative,
                        "file_id": upload["file"]["id"],
                        "version_id": upload["version"]["id"],
                        "transfer": upload["transfer"],
                        "approved": True,
                    },
                )
            response = await client.post(
                f"{self.endpoint}/xnobrain/api/control/v1/organizations/{self.organization}/runner/commands/{cmd['command_id']}/output-uploads/{upload['upload_id']}/complete",
                headers=self.headers(state),
                json={"size_bytes": info["size_bytes"], "sha256": info["sha256"], "parts": parts},
            )
            response.raise_for_status()
            artifacts.append(response.json()["data"])
        return artifacts

    @workspace_activity(lambda self: self.platform.repository.data_dir, independent=True)
    async def command(self, client, state, cmd):
        if self.dispatch_paused:
            return
        canonical = json.dumps(cmd, sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(canonical).hexdigest()
        cid = cmd["command_id"]
        if self.store.seen(cid):
            return
        deadline = datetime.fromisoformat(cmd["deadline"].replace("Z", "+00:00"))
        if deadline <= datetime.now(timezone.utc):
            raise ValueError("expired command")
        if (
            cmd.get("device_id") != state["device_id"]
            or cmd.get("organization_id") != self.organization
        ):
            raise ValueError("wrong command target")
        instruction = str(cmd.get("payload_reference") or "").strip()
        expected = base64.urlsafe_b64decode(str(cmd["payload_digest"]) + "==")
        if not instruction or hashlib.sha256(instruction.encode()).digest() != expected:
            raise ValueError("invalid task payload digest")
        registration = str(cmd["registration_id"])
        mapped = dict(self.store.state().get("registrations") or {})
        agent_id = mapped.get(registration)
        if not agent_id:
            raise ValueError("unknown agent registration")
        self.store.record(cid, digest, "acknowledged")
        response = await client.post(
            f"{self.endpoint}/xnobrain/api/control/v1/organizations/{self.organization}/runner/commands/{cid}/ack",
            headers=self.headers(state),
            json={
                "state": "acknowledged",
                "local_sequence": int(time.time_ns()),
                "occurred_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        response.raise_for_status()
        try:
            await self.execute_acknowledged(
                client, state, cmd, digest, agent_id, instruction, deadline
            )
        except Exception:
            # Control never re-leases an acknowledged command. Report it failed
            # so the run, and any team waiting on it, ends instead of waiting
            # for the deadline.
            self.refuse(cmd, digest, "runtime_execution_failed")
            raise

    async def execute_acknowledged(
        self, client, state, cmd, digest, agent_id, instruction, deadline
    ):
        """Import inputs, run the agent and enqueue the command result."""
        cid = cmd["command_id"]
        context_id = str(cmd.get("work_context_id") or "").strip()
        sponsor_grant_id = str(cmd.get("sponsor_grant_id") or "").strip()
        if not context_id:
            raise ValueError("organization command has no work context")
        funding = dict(cmd.get("funding") or {})
        mode = str(funding.get("mode") or "")
        model = str(funding.get("model") or "")
        if mode == organization_funding.ORGANIZATION:
            payer_kind, sponsor_grant_id = "organization", ""
            if not funding.get("workspace") and self.platform.agents.pinned_auxiliary_models(
                agent_id
            ):
                self.refuse(cmd, digest, "organization_auxiliary_model_configured")
                return
        elif mode == organization_funding.OWNER:
            if model and model not in (funding.get("allowed_models") or []):
                self.refuse(cmd, digest, "stage_model_not_allowed")
                return
            payer_kind = "organization_sponsor" if sponsor_grant_id else "personal"
        else:
            raise ValueError("organization command has no funding")
        personal = cmd.get("work_owner_kind") == "personal"
        owner_kind = "personal" if personal else "organization"
        ownership = {
            "schema_version": 1,
            "id": context_id,
            "owner_kind": owner_kind,
            "organization_id": None if personal else self.organization,
            "payer_kind": payer_kind,
            "sponsor_grant_id": sponsor_grant_id or None,
            "membership_revision_at_create": cmd.get("membership_revision"),
            "policy_revision_at_create": cmd.get("policy_revision"),
            "state": "active",
        }
        if personal and payer_kind == "organization":
            if cmd.get("work_payer_organization_id") != self.organization:
                raise ValueError("personal command has no bound organization payer")
            ownership["payer_organization_id"] = self.organization
        if not personal:
            inputs = await self.import_inputs(client, state, cmd, agent_id)
            instruction += (
                f"\n\nWrite organization deliverables only under organization-runs/{cmd['run_id']}/outputs/. "
                "Files there will be published after completion."
                + (
                    "\n\nApproved organization inputs:\n"
                    + "\n".join("- " + row["path"] for row in inputs)
                    if inputs
                    else ""
                )
            )
        conversation = self.platform.create_conversation(
            agent_id,
            {
                "title": f"{'Personal delegated' if personal else 'Organization'} run {str(cmd['run_id'])[-8:]}",
                "ownership_context": ownership,
            },
            TrustedRequestContext(
                subject=str(cmd.get("subject_user_id") or "organization-runner"),
                organization_id="" if personal else self.organization,
                ownership_context=ownership,
            ),
        )
        organization_funding.register(
            conversation["id"], mode, model, workspace=funding.get("workspace") is True
        )
        body = self.execution_body(cmd, instruction, deadline)
        if model:
            # Both funding modes honor an authorized stage pin.
            body["model"] = model
        self.platform.runtime_updates.register_organization_command(cid)
        try:
            run = await self.platform.start_conversation_run(agent_id, conversation["id"], body)
        except Exception:
            organization_funding.release(conversation["id"])
            await self.platform.runtime_updates.release_organization_command(cid)
            raise
        self.active_commands[cid] = (agent_id, conversation["id"], run["id"])
        done = asyncio.Event()
        renew = asyncio.create_task(self.renew(client, state, cmd, done))
        try:
            while True:
                current = self.platform.conversation_runs.get_run(
                    agent_id, conversation["id"], run["id"]
                )
                if current["status"] in TERMINAL:
                    break
                if renew.done():
                    await renew
                await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            current = await self.platform.stop_run(agent_id, conversation["id"], run["id"])
            raise
        finally:
            done.set()
            renew.cancel()
            organization_funding.release(conversation["id"])
            self.active_commands.pop(cid, None)
            await self.platform.runtime_updates.release_organization_command(cid)
            try:
                await renew
            except asyncio.CancelledError:
                pass
        completed = current["status"] == "completed"
        summary = {
            "conversation_id": conversation["id"],
            "output": str(current.get("output") or "")[:4000],
        }
        if personal:
            summary.pop("conversation_id", None)
        artifacts = []
        if completed and not personal:
            try:
                artifacts = await self.publish_outputs(client, state, cmd, agent_id)
            except (ServiceError, httpx.HTTPError, ValueError, KeyError) as error:
                # The agent's answer still reaches the team; only its files
                # stay in the member workspace.
                code = str(getattr(error, "code", "") or "output_publish_failed")
                summary["output_publish_error"] = code
                logger.warning(
                    "organization outputs not published",
                    extra={"command_id": cid, "error": code},
                )
        result = {
            "state": "completed" if completed else "failed",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "safe_summary": summary,
            "usage": dict(current.get("usage") or {}),
            "artifacts": artifacts,
            "error_code": "" if completed else "runtime_execution_failed",
            "result_digest": hashlib.sha256(
                (cid + digest + str(current.get("output") or "")).encode()
            ).hexdigest(),
        }
        self.store.enqueue(
            {
                "idempotency_key": cmd["idempotency_key"],
                "url": f"{self.endpoint}/xnobrain/api/control/v1/organizations/{self.organization}/runner/commands/{cid}/result",
                "body": result,
            }
        )
        self.store.record(cid, digest, "completed")

    def refuse(self, cmd, digest: str, error_code: str) -> None:
        """Report a command as failed before it starts."""
        cid = str(cmd["command_id"])
        self.store.enqueue(
            {
                "idempotency_key": cmd["idempotency_key"],
                "url": f"{self.endpoint}/xnobrain/api/control/v1/organizations/{self.organization}/runner/commands/{cid}/result",
                "body": {
                    "state": "failed",
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                    "safe_summary": {},
                    "usage": {},
                    "artifacts": [],
                    "error_code": error_code,
                    "result_digest": hashlib.sha256(
                        (cid + digest + error_code).encode()
                    ).hexdigest(),
                },
            }
        )
        self.store.record(cid, digest, "completed")

    @workspace_activity(lambda self: self.platform.repository.data_dir)
    async def tick(self, client):
        state = self.store.state()
        if state.get("status") == "revoked":
            raise DeviceRevoked
        if not state.get("access_token"):
            token = state.get("enrollment_token")
            if not token:
                raise EnrollmentRejected
            state = await self.enroll(client, token)
        state = await self.refresh_if_needed(client, state)
        await self.send_outbox(client, state)
        agents = self.connector.inventory()
        digest = hashlib.sha256(json.dumps(agents, sort_keys=True).encode()).hexdigest()
        if digest != state.get("inventory_digest"):
            state["inventory_revision"] = int(state.get("inventory_revision") or 0) + 1
            state["inventory_digest"] = digest
        response = await client.put(
            f"{self.endpoint}/xnobrain/api/control/v1/organizations/{self.organization}/runner/agent-registrations",
            headers=self.headers(state),
            json={"inventory_revision": state["inventory_revision"], "agents": agents},
        )
        response.raise_for_status()
        state["registrations"] = {
            r["registration_id"]: r["agent_public_id"]
            for r in response.json().get("data", {}).get("registrations", [])
        }
        state["heartbeat_sequence"] = int(state.get("heartbeat_sequence") or 0) + 1
        response = await client.post(
            f"{self.endpoint}/xnobrain/api/control/v1/organizations/{self.organization}/runner/heartbeat",
            headers=self.headers(state),
            json={
                "sequence": state["heartbeat_sequence"],
                "inventory_revision": state["inventory_revision"],
                "availability": "online",
                "runtime_version": os.getenv("XNOBRAIN_VERSION", "dev"),
            },
        )
        response.raise_for_status()
        state.update(
            {
                "status": "connected",
                "error_code": None,
                "last_heartbeat_at": _now(),
                "updated_at": _now(),
            }
        )
        self.store.save_state(state)
        response = await client.post(
            f"{self.endpoint}/xnobrain/api/control/v1/organizations/{self.organization}/runner/commands:poll",
            headers=self.headers(state),
            json={"max_commands": 20, "wait_seconds": 0},
        )
        response.raise_for_status()
        for cmd in response.json().get("data", {}).get("commands", []):
            self.dispatch(client, state, cmd)
