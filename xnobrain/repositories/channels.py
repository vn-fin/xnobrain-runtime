"""Atomic Zalo link state and read-only channel discovery for Runtime chats."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import StoreError

ZALO_DIR = "zalo"
LINKS_FILE = "links.json"
HISTORY_FILE = "history.jsonl"
DIRECTORY_FILE = "channel_directory.json"
MAX_STATE_BYTES = 2 * 1024 * 1024
MAX_HISTORY_LINES = 5000


class ChannelsRepository:
    """Read bridge history and persist ``links.json`` beneath one profile."""

    def __init__(self, base):
        self.base = base

    @staticmethod
    def _profile_root(profile: Any) -> Path:
        candidate = Path(profile)
        if candidate.is_symlink():
            raise StoreError("agent profile not found", status=404, code="not_found")
        root = candidate.resolve()
        if not root.is_dir():
            raise StoreError("agent profile not found", status=404, code="not_found")
        return root

    @classmethod
    def _zalo_dir(cls, profile: Any, *, create: bool = False) -> tuple:
        root = cls._profile_root(profile)
        directory = root / ZALO_DIR
        if directory.is_symlink():
            raise StoreError(
                "agent channel directory is unsafe", status=400, code="channel_path_unsafe"
            )
        if create:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not directory.is_dir():
            return root, None
        resolved = directory.resolve()
        if resolved.parent != root:
            raise StoreError(
                "agent channel directory escapes the profile",
                status=400,
                code="channel_path_unsafe",
            )
        return root, resolved

    @staticmethod
    def _read_text(path: Path) -> str:
        if path.is_symlink() or not path.is_file():
            raise StoreError(
                "agent channel state is unsafe", status=400, code="channel_path_unsafe"
            )
        try:
            payload = path.read_bytes()
        except OSError as error:
            raise StoreError(
                "agent channel state is unavailable", status=500, code="channel_state_unavailable"
            ) from error
        if len(payload) > MAX_STATE_BYTES:
            payload = payload[-MAX_STATE_BYTES:]
        return payload.decode("utf-8", errors="replace")

    def links(self, profile: Any) -> dict[str, dict[str, Any]]:
        _, directory = self._zalo_dir(profile)
        if directory is None:
            return {}
        path = directory / LINKS_FILE
        if not path.exists():
            return {}
        try:
            value = json.loads(self._read_text(path))
        except (ValueError, TypeError) as error:
            raise StoreError(
                "agent channel links are invalid", status=500, code="channel_links_invalid"
            ) from error
        if not isinstance(value, dict):
            raise StoreError(
                "agent channel links are invalid", status=500, code="channel_links_invalid"
            )
        return {
            str(thread_id): record
            for thread_id, record in value.items()
            if isinstance(record, dict)
        }

    def write_link(
        self,
        profile: Any,
        thread_id: str,
        *,
        agent_id: str,
        conversation_id: str,
        name: str,
        timestamp: float,
    ) -> dict[str, Any]:
        _, directory = self._zalo_dir(profile, create=True)
        if directory is None:
            raise StoreError(
                "agent channel directory is unavailable",
                status=500,
                code="channel_state_unavailable",
            )
        path = directory / LINKS_FILE
        if path.is_symlink():
            raise StoreError(
                "agent channel links are unsafe", status=400, code="channel_path_unsafe"
            )
        links = self.links(profile)
        record = {
            "agent_id": agent_id,
            "conversation_id": conversation_id,
            "name": name,
            "ts": timestamp,
        }
        links[str(thread_id)] = record
        self.base.atomic_write(
            path,
            (json.dumps(links, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"),
            mode=0o600,
        )
        return record

    def history(self, profile: Any) -> list[dict[str, Any]]:
        _, directory = self._zalo_dir(profile)
        if directory is None:
            return []
        path = directory / HISTORY_FILE
        if not path.exists():
            return []
        lines = self._read_text(path).splitlines()
        records: list[dict[str, Any]] = []
        for line in lines[-MAX_HISTORY_LINES:]:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(value, dict):
                records.append(value)
        return records

    def directory(self, profile: Any) -> list[dict[str, Any]]:
        root = self._profile_root(profile)
        path = root / DIRECTORY_FILE
        if not path.exists():
            return []
        try:
            value = json.loads(self._read_text(path))
        except (ValueError, TypeError):
            return []
        platforms = value.get("platforms") if isinstance(value, dict) else None
        entries = platforms.get("zalo") if isinstance(platforms, dict) else None
        if not isinstance(entries, list):
            return []
        result: list[dict[str, Any]] = []
        for entry in entries:
            if isinstance(entry, dict) and str(entry.get("id") or "").strip():
                result.append(entry)
        return result
