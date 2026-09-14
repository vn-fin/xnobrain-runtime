"""Bounded durable receipts for server-bound conversation creation only."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from contextlib import contextmanager
from pathlib import Path

from .base import StoreError
from .custom_page_locks import ExecutionLease, acquire, release


class ConversationCreationRepository:
    def __init__(self, files, profile: Path, agent: str, conversation: str):
        self.files = files
        self.profile = profile
        self.agent = agent
        self.parent = files.data_dir / "conversation-creations"
        self.root = self.parent / hashlib.sha256(agent.encode()).hexdigest()
        self.key = hashlib.sha256(conversation.encode()).hexdigest()
        self.path = self.root / f"{self.key}.json"
        self.lock_key = hashlib.sha256(agent.encode()).hexdigest()

    @contextmanager
    def locked(self):
        self.files.storage_mount.check()
        activity = ExecutionLease(self.files.data_dir, self.agent)
        try:
            descriptor = acquire(
                self.files.data_dir,
                f".conversation-creation-{self.lock_key}.lock",
                shared=False,
                timeout=2,
            )
            try:
                if self.profile.is_symlink() or not self.profile.is_dir():
                    raise StoreError("agent profile unavailable", status=404, code="not_found")
                for directory in (self.parent, self.root):
                    if directory.is_symlink():
                        raise StoreError(
                            "unsafe creation receipt storage", code="unsafe_creation_receipt"
                        )
                    directory.mkdir(mode=0o750, exist_ok=True)
                yield
            finally:
                release(descriptor)
        finally:
            activity.close()

    def read(self) -> dict | None:
        try:
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise StoreError("unsafe creation receipt", code="unsafe_creation_receipt") from error
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 16384:
                raise ValueError("unsafe receipt")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                value = json.loads(stream.read(16385))
            if not isinstance(value, dict) or value.get("state") not in {
                "pending",
                "complete",
                "deleted",
            }:
                raise ValueError("invalid receipt")
            return value
        except (OSError, ValueError) as error:
            raise StoreError(
                "unreadable creation receipt", status=409, code="invalid_creation_receipt"
            ) from error
        finally:
            os.close(descriptor)

    def save(self, receipt: dict, *, new: bool = False):
        if new:
            with os.scandir(self.root) as entries:
                for index, _entry in enumerate(entries):
                    if index >= 9999:
                        raise StoreError(
                            "creation receipt limit reached",
                            status=409,
                            code="creation_receipt_quota",
                        )
        self.files.atomic_json(self.path, receipt)
