"""Immutable profile-local conversation ownership context persistence."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from .base import StoreError

_CONVERSATION_ID_RE = re.compile(r"^[^/\\\r\n\x00]{1,256}$")


class ConversationRepositoryMixin:
    """Persist one immutable owner/payer binding for every conversation."""

    def _conversation_context_root(self, profile_root: Path) -> Path:
        profile_root = Path(profile_root)
        if profile_root.is_symlink() or not profile_root.is_dir():
            raise StoreError("agent profile not found", status=404, code="not_found")
        resolved_profile = profile_root.resolve()
        metadata_root = profile_root / ".xnobrain"
        context_root = metadata_root / "conversation-contexts"
        for path in (metadata_root, context_root):
            if path.is_symlink():
                raise StoreError(
                    "conversation context storage must not be a symlink",
                    code="unsafe_conversation_context_store",
                )
        context_root.mkdir(parents=True, exist_ok=True, mode=0o750)
        resolved_context_root = context_root.resolve()
        if resolved_profile not in resolved_context_root.parents:
            raise StoreError(
                "conversation context storage escapes agent profile",
                code="unsafe_conversation_context_store",
            )
        return resolved_context_root

    @staticmethod
    def _conversation_context_key(conversation_id: Any) -> tuple[str, str]:
        value = str(conversation_id or "").strip()
        if not _CONVERSATION_ID_RE.fullmatch(value):
            raise StoreError("invalid conversation id")
        return value, hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _conversation_context_path(self, profile_root: Path, conversation_id: Any) -> Path:
        value, key = self._conversation_context_key(conversation_id)
        del value
        root = self._conversation_context_root(profile_root)
        path = root / f"{key}.json"
        if path.is_symlink() or path.resolve().parent != root:
            raise StoreError(
                "invalid conversation context path",
                code="unsafe_conversation_context_store",
            )
        return path

    def create_conversation_context(
        self,
        profile_root: Path,
        record: Mapping[str, Any],
    ) -> dict[str, Any]:
        context = dict(record)
        conversation_id, _ = self._conversation_context_key(context.get("conversation_id"))
        path = self._conversation_context_path(profile_root, conversation_id)
        with self._lock:
            if path.exists():
                raise StoreError(
                    "conversation ownership context already exists",
                    status=409,
                    code="conversation_context_exists",
                )
            self.atomic_write(
                path,
                (json.dumps(context, ensure_ascii=False, indent=2) + "\n").encode(),
                replace=False,
            )
        return context

    def get_conversation_context(
        self,
        profile_root: Path,
        conversation_id: Any,
    ) -> dict[str, Any] | None:
        expected_id, _ = self._conversation_context_key(conversation_id)
        path = self._conversation_context_path(profile_root, expected_id)
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StoreError(
                "conversation ownership context is unreadable",
                status=500,
                code="invalid_conversation_context_store",
            ) from exc
        if not isinstance(value, dict) or value.get("conversation_id") != expected_id:
            raise StoreError(
                "conversation ownership context is invalid",
                status=500,
                code="invalid_conversation_context_store",
            )
        return value

    def delete_conversation_context(self, profile_root: Path, conversation_id: Any) -> bool:
        path = self._conversation_context_path(profile_root, conversation_id)
        with self._lock:
            if not path.exists():
                return False
            if path.is_symlink() or not path.is_file():
                raise StoreError(
                    "conversation ownership context is unsafe",
                    code="unsafe_conversation_context_store",
                )
            path.unlink()
            self._sync_dir(path.parent)
            return True
