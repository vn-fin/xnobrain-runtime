"""Atomic staged assistance results; Control remains layout authority."""

from __future__ import annotations

import hashlib
import json
import time

from .base import StoreError
from .custom_page import CustomPageRepository


def fail(code="ui_assistance_invalid", status=409):
    raise StoreError(code.replace("_", " "), status=status, code=code)


class UICompositionRepository:
    def __init__(self, files):
        self.files = files
        self.storage = CustomPageRepository(files)

    def directory(self, owner, create=False):
        base = self.files.data_dir / "ui-assistance"
        directory = base / hashlib.sha256(owner.encode()).hexdigest()
        for path in (base, directory):
            if path.is_symlink():
                fail("ui_assistance_unsafe_storage")
            if create:
                path.mkdir(exist_ok=True, mode=0o700)
        return directory

    def path(self, owner, identifier):
        path = self.directory(owner) / (self.files._id(identifier) + ".json")
        if path.is_symlink():
            fail("ui_assistance_unsafe_storage")
        return path

    def get(self, owner, identifier):
        with self.storage.lock():
            path = self.path(owner, identifier)
            if not path.is_file():
                fail("ui_assistance_not_found", 404)
            if path.stat().st_size > 65536:
                fail()
            try:
                row = json.loads(path.read_text())
            except (ValueError, OSError):
                fail()
            if not isinstance(row, dict):
                fail()
            if row.get("expires_at", 0) < time.time():
                fail("ui_assistance_expired")
            return row

    def prepare(self, owner, body):
        with self.storage.lock(mutation=True):
            path = self.path(owner, body["assistance_id"])
            if path.exists():
                existing = self.get(owner, body["assistance_id"])
                if existing["cancelled"]:
                    fail("ui_assistance_cancelled")
                if existing["request"] != body:
                    fail("ui_assistance_conflict")
                return existing
            directory = self.directory(owner, create=True)
            records = list(directory.glob("uia_*.json"))
            for record in records[:100]:
                if not record.is_symlink() and time.time() - record.stat().st_mtime > 86400:
                    record.unlink()
            if len(list(directory.glob("uia_*.json"))) >= 100:
                fail("ui_assistance_limit", 413)
            row = {
                "request": body,
                "run_id": None,
                "result": None,
                "cancelled": False,
                "expires_at": time.time() + 86400,
            }
            self.files.atomic_json(path, row)
            return row

    def change(self, owner, identifier, **values):
        with self.storage.lock(mutation=True):
            row = self.get(owner, identifier)
            if row["cancelled"] and not values.get("cancelled"):
                fail("ui_assistance_cancelled")
            row.update(values)
            self.files.atomic_json(self.path(owner, identifier), row)
            return row

    def cancel(self, owner, identifier):
        with self.storage.lock(mutation=True):
            self.directory(owner, create=True)
            path = self.path(owner, identifier)
            if not path.exists():
                # A cancelled relay may arrive before its delayed start.
                if len(list(path.parent.glob("uia_*.json"))) >= 100:
                    fail("ui_assistance_limit", 413)
                row = {
                    "cancelled": True,
                    "run_id": None,
                    "request": None,
                    "result": None,
                    "expires_at": time.time() + 86400,
                }
                self.files.atomic_json(path, row)
                return row
            return self.change(owner, identifier, cancelled=True)
