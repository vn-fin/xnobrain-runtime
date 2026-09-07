"""Dedicated hosted Runtime bootstrap and bounded execution."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any, Mapping

from .base import ServiceError


class HostedRuntimeService:
    def __init__(self, platform):
        self.platform = platform

    def bootstrap(self, body: Mapping[str, Any]) -> dict[str, Any]:
        package = dict(body.get("package") or {})
        package["id"] = body["installation_id"]
        result = self.platform.marketplace.install(package)
        return {
            "installation_id": body["installation_id"],
            "local_profile_id": result["local_profile_id"],
            "digest": result["digest"],
            "state_owner": "customer",
            "operator_access": "denied",
        }

    async def execute(self, body: Mapping[str, Any]) -> dict[str, Any]:
        instruction = str(body.get("instruction") or "")
        timeout = int(body.get("timeout_seconds") or 0)
        profiles = [
            p
            for p in self.platform.repository.profiles_root.iterdir()
            if p.is_dir() and p.name.startswith("market-")
        ]
        if len(profiles) != 1:
            raise ServiceError(
                "hosted installation profile unavailable",
                status=409,
                code="hosted_profile_unavailable",
            )
        agent = profiles[0].name
        conversation = self.platform.create_conversation(agent, {"title": "Hosted execution"})
        run = await self.platform.start_conversation_run(
            agent,
            conversation["id"],
            {
                "input": instruction,
                "run_mode": "background",
                "timeout_seconds": timeout,
                "organization_command": True,
            },
        )
        while True:
            current = self.platform.conversation_runs.get_run(agent, conversation["id"], run["id"])
            if current["status"] in {"completed", "failed", "timed_out", "cancelled"}:
                break
            await asyncio.sleep(0.25)
        return {
            "status": current["status"],
            "output": str(current.get("output") or "")[:1048576],
            "usage": dict(current.get("usage") or {}),
            "completed_at": time.time(),
        }

    def _backup_key(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        path = self.platform.repository.data_dir / "hosted-backup.key"
        if not path.exists():
            self.platform.repository.atomic_write(path, os.urandom(32), mode=0o600, replace=False)
        return AESGCM(path.read_bytes())

    def backup(self) -> dict[str, Any]:
        import base64
        import hashlib
        import json
        import os

        profiles = [
            p
            for p in self.platform.repository.profiles_root.iterdir()
            if p.is_dir() and p.name.startswith("market-")
        ]
        if len(profiles) != 1:
            raise ServiceError(
                "hosted profile unavailable", status=409, code="hosted_profile_unavailable"
            )
        profile = profiles[0]
        files = {
            p.relative_to(profile).as_posix(): base64.b64encode(p.read_bytes()).decode()
            for p in profile.rglob("*")
            if p.is_file() and not p.is_symlink() and p.stat().st_size <= 10 * 1024 * 1024
        }
        raw = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
        nonce = os.urandom(12)
        cipher = self._backup_key().encrypt(nonce, raw, None)
        return {
            "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
            "ciphertext": base64.b64encode(cipher).decode(),
            "nonce": base64.b64encode(nonce).decode(),
        }

    def restore(self, body: Mapping[str, Any]) -> dict[str, Any]:
        import base64
        import hashlib
        import json

        nonce = base64.b64decode(body["nonce"])
        raw = self._backup_key().decrypt(nonce, base64.b64decode(body["ciphertext"]), None)
        if "sha256:" + hashlib.sha256(raw).hexdigest() != body["digest"]:
            raise ServiceError(
                "hosted backup digest mismatch", status=422, code="backup_digest_mismatch"
            )
        profiles = [
            p
            for p in self.platform.repository.profiles_root.iterdir()
            if p.is_dir() and p.name.startswith("market-")
        ]
        if len(profiles) != 1:
            raise ServiceError(
                "hosted profile unavailable", status=409, code="hosted_profile_unavailable"
            )
        for name, payload in json.loads(raw).items():
            parts = Path(name).parts
        for name, payload in json.loads(raw).items():
            parts = Path(name).parts
            if any(x in {"", ".."} for x in parts):
                raise ServiceError("unsafe backup path", status=422, code="backup_rejected")
            target = profiles[0].joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            self.platform.repository.atomic_write(target, base64.b64decode(payload))
        return {"restored": True, "digest": body["digest"]}
