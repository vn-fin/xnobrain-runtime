"""Profile and snapshot persistence."""

from __future__ import annotations

import hashlib
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from .base import StoreError


class ProfileRepositoryMixin:
    """Profile lifecycle and snapshot operations."""

    def profile_path(self, agent_id: Any) -> Path:
        agent_id = self._id(agent_id, "agent id")
        candidate = (self.profiles_root / agent_id).resolve()
        if candidate.parent != self.profiles_root:
            raise StoreError("agent path escapes profiles root")
        return candidate

    def soft_delete_profile(self, agent_id: Any) -> Path:
        source = self.profile_path(agent_id)
        if not source.is_dir():
            raise StoreError("agent not found", status=404, code="not_found")
        target = self.trash_root / f"{source.name}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        with self._lock:
            os.replace(source, target)
            self._sync_dir(source.parent)
            self._sync_dir(target.parent)
        return target

    def hard_delete_profile(self, agent_id: Any) -> None:
        source = self.profile_path(agent_id)
        if not source.is_dir():
            raise StoreError("agent not found", status=404, code="not_found")
        with self._lock:
            shutil.rmtree(source)
            self._sync_dir(source.parent)

    def snapshot(self, agent_id: Any, kind: str, target: str, content: bytes) -> dict[str, Any]:
        agent_id = self._id(agent_id, "agent id")
        if kind not in {"memory", "skills", "config"}:
            raise StoreError("invalid snapshot kind")
        target = self._id(target, "snapshot target")
        digest = hashlib.sha256(content).hexdigest()
        snapshot_id = f"{time.time_ns()}-{digest[:12]}"
        suffix = ".yaml" if kind == "config" else ".md"
        relative = Path("snapshots") / kind / target / f"{snapshot_id}{suffix}"
        path = self.profile_path(agent_id) / relative
        with self._lock:
            self.atomic_write(path, content, mode=0o440, replace=False)
        return {
            "id": snapshot_id,
            "agent_id": agent_id,
            "kind": kind,
            "target": target,
            "hash": digest,
            "path": relative.as_posix(),
            "created_at": time.time(),
        }

    def list_snapshots(self, agent_id: Any, kind: str | None = None) -> list[dict[str, Any]]:
        agent_id = self._id(agent_id, "agent id")
        root = self.profile_path(agent_id) / "snapshots"
        if not root.is_dir():
            return []
        result: list[dict[str, Any]] = []
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(self.profile_path(agent_id))
            parts = relative.parts
            if len(parts) != 4 or parts[0] != "snapshots":
                continue
            current_kind, target = parts[1], parts[2]
            if kind and current_kind != kind:
                continue
            payload = path.read_bytes()
            result.append(
                {
                    "id": path.stem,
                    "agent_id": agent_id,
                    "kind": current_kind,
                    "target": target,
                    "hash": hashlib.sha256(payload).hexdigest(),
                    "path": relative.as_posix(),
                    "created_at": path.stat().st_mtime,
                }
            )
        return sorted(result, key=lambda item: item["created_at"], reverse=True)

    def restore_snapshot(self, agent_id: Any, snapshot_id: Any) -> dict[str, Any]:
        snapshot_id = self._id(snapshot_id, "snapshot id")
        matches = [item for item in self.list_snapshots(agent_id) if item["id"] == snapshot_id]
        if not matches:
            raise StoreError("snapshot not found", status=404, code="not_found")
        item = matches[0]
        profile = self.profile_path(agent_id)
        source = profile / item["path"]
        if item["kind"] == "skills":
            destination = profile / "skills" / "custom" / item["target"] / "SKILL.md"
            skills_root = profile / "skills"
            if skills_root.is_dir():
                for skill_file in skills_root.rglob("SKILL.md"):
                    if skill_file.parent.name == item["target"]:
                        destination = skill_file
                        break
        elif item["kind"] == "memory":
            filename = "USER.md" if item["target"] == "user" else "MEMORY.md"
            destination = profile / "memories" / filename
        else:
            destination = profile / "config.yaml"
        self.atomic_write(destination, source.read_bytes(), mode=0o640)
        return item
