"""Profile-local reasoning preferences, separate from immutable owner bindings."""

from __future__ import annotations

import json
from contextlib import contextmanager

from .base import StoreError
from .custom_page_locks import acquire, release


class ConversationReasoningRepository:
    def __init__(self, files, profile, conversation):
        self.files = files
        # Reuse validated, hashed session paths without modifying owner records.
        context = files._conversation_context_path(profile, conversation)
        self.path = context.with_name(context.stem + ".reasoning.json")
        self.root = context.parent
        self.key = context.stem

    @contextmanager
    def locked(self):
        descriptor = acquire(self.root, f".{self.key}.reasoning.lock", shared=False, timeout=2)
        try:
            if self.path.is_symlink():
                raise StoreError("unsafe reasoning storage", code="unsafe_reasoning_store")
            yield
        finally:
            release(descriptor)

    def read(self):
        if not self.path.exists():
            return {"reasoning_effort": None, "reasoning_revision": 0}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or not isinstance(value.get("reasoning_revision"), int):
                raise ValueError("invalid preference")
            return value
        except (OSError, ValueError) as error:
            raise StoreError(
                "reasoning storage unavailable", status=503, code="invalid_reasoning_store"
            ) from error

    def update(self, effort, revision):
        with self.locked():
            current = self.read()
            if current["reasoning_revision"] != revision:
                raise StoreError(
                    "reasoning preference changed; refresh and try again",
                    status=409,
                    code="reasoning_revision_conflict",
                )
            value = {"reasoning_effort": effort, "reasoning_revision": revision + 1}
            self.files.atomic_json(self.path, value)
            return value

    def delete(self):
        with self.locked():
            self.path.unlink(missing_ok=True)
            self.files._sync_dir(self.root)
