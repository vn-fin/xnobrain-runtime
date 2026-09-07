"""Opt-in outbound-only connector for delegated organization work."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ..repositories.organization_connector import OrganizationConnectorRepository

TERMINAL = {"completed", "failed", "timed_out", "cancelled"}


class OrganizationConnector:
    def __init__(self, platform):
        self.platform = platform
        self.store = OrganizationConnectorRepository(platform.repository)
        self.endpoint = os.getenv("RUNTIME_CONTROL_URL", "").rstrip("/")
        self.organization = os.getenv("RUNTIME_ORGANIZATION_ID", "")
        self.proof = os.getenv("RUNTIME_RUNNER_ENROLLMENT_PROOF", "")
        self.interval = max(2, int(os.getenv("RUNTIME_CONNECTOR_INTERVAL_SECONDS", "10")))
        self.task = None
        self.stop_event = asyncio.Event()
        self.key_path = self.store.root / "device-ed25519.key"

    @property
    def enabled(self):
        return bool(self.endpoint and self.organization and self.proof)

    async def start(self):
        if self.enabled and self.task is None:
            self.task = asyncio.create_task(self.run(), name="xnobrain-organization-connector")

    async def stop(self):
        self.stop_event.set()
        if self.task:
            self.task.cancel()
        if self.task:
            try:
                await self.task
            except asyncio.CancelledError:
                pass

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

    def headers(self, state):
        return {
            "Authorization": f"Bearer {state['access_token']}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def require(response):
        response.raise_for_status()
        body = response.json()

    async def enroll(self, client):
        key = self.key()
        pub = key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        response = await client.post(
            f"{self.endpoint}/xnobrain/api/control/v1/runner/enrollment-challenges",
            json={
                "enrollment_token": f"{self.organization}.{self.proof}",
                "device_public_key": base64.b64encode(pub).decode(),
            },
        )
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
        response.raise_for_status()
        enrolled = response.json()["data"]
        state = {
            "device_id": enrolled["device_id"],
            "access_token": enrolled["access_token"],
            "expires_at": enrolled["expires_at"],
            "refresh_token": enrolled["refresh_token"],
            "heartbeat_sequence": 0,
            "inventory_revision": 1,
        }
        self.store.save_state(state)
        return state

    def inventory(self):
        activity = self.platform.agent_activity()["agents"]
        return [
            {
                "agent_public_id": a["id"],
                "display_name": a.get("display_name") or a.get("name") or a["id"],
                "capabilities": [],
                "availability": "busy" if activity.get(a["id"]) == "running" else "available",
                "revision": 1,
            }
            for a in self.platform.list_agents()
        ]

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

    def execution_body(self, cmd, instruction, deadline):
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

    async def command(self, client, state, cmd):
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
        inputs = await self.import_inputs(client, state, cmd, agent_id)
        instruction = (
            instruction
            + f"\n\nWrite organization deliverables only under organization-runs/{cmd['run_id']}/outputs/. Files there will be published after completion."
            + (
                "\n\nApproved organization inputs:\n"
                + "\n".join("- " + row["path"] for row in inputs)
                if inputs
                else ""
            )
        )
        conversation = self.platform.create_conversation(
            agent_id, {"title": f"Organization run {str(cmd['run_id'])[-8:]}"}
        )
        run = await self.platform.start_conversation_run(
            agent_id, conversation["id"], self.execution_body(cmd, instruction, deadline)
        )
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
            try:
                await renew
            except asyncio.CancelledError:
                pass
        completed = current["status"] == "completed"
        artifacts = await self.publish_outputs(client, state, cmd, agent_id) if completed else []
        summary = {
            "conversation_id": conversation["id"],
            "output": str(current.get("output") or "")[:4000],
        }
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

    async def tick(self, client):
        state = self.store.state()
        if not state:
            state = await self.enroll(client)
        state = await self.refresh_if_needed(client, state)
        await self.send_outbox(client, state)
        agents = self.inventory()
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
        state["heartbeat_sequence"] += 1
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
        self.store.save_state(state)
        response = await client.post(
            f"{self.endpoint}/xnobrain/api/control/v1/organizations/{self.organization}/runner/commands:poll",
            headers=self.headers(state),
            json={"max_commands": 20, "wait_seconds": 0},
        )
        response.raise_for_status()
        for cmd in response.json().get("data", {}).get("commands", []):
            await self.command(client, state, cmd)

    async def run(self):
        async with httpx.AsyncClient(timeout=20, verify=True) as client:
            while not self.stop_event.is_set():
                try:
                    await self.tick(client)
                except Exception:
                    pass
                await asyncio.sleep(self.interval)
