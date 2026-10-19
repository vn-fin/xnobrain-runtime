"""Atomic bounded journal for the organization Runtime connector."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from .base import RepositoryBase, StoreError

ORGANIZATION_ID = re.compile(r"^org_[A-Za-z0-9]{8,64}$")


class OrganizationConnectorRepository:
    """Connector journal for one organization.

    Each organization keeps its own device credential, command inbox and result
    outbox under ``organization-connector/<organization_id>/``. The Ed25519
    device key at the connector root is shared by every organization.
    """

    def __init__(self, base: RepositoryBase, organization_id: str | None = None):
        self.base = base
        self.root = base.data_dir / "organization-connector"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.organization_id = organization_id
        self.directory = self.root
        if organization_id is not None:
            if not ORGANIZATION_ID.match(organization_id):
                raise StoreError(
                    "invalid organization id", status=400, code="invalid_organization_id"
                )
            self.directory = self.root / organization_id
            self.directory.mkdir(exist_ok=True, mode=0o700)
        self.state_path = self.directory / "state.json"
        self.inbox_path = self.directory / "inbox.json"
        self.outbox_path = self.directory / "outbox.json"

    def organizations(self) -> list[str]:
        """Organizations that have a stored connection, in stable order."""
        return sorted(
            path.name
            for path in self.root.iterdir()
            if path.is_dir() and ORGANIZATION_ID.match(path.name) and (path / "state.json").exists()
        )

    def for_organization(self, organization_id: str) -> OrganizationConnectorRepository:
        return OrganizationConnectorRepository(self.base, organization_id)

    def remove(self) -> None:
        if self.organization_id is None:
            raise StoreError("connection has no organization", status=400)
        shutil.rmtree(self.directory, ignore_errors=True)

    def _read(self, path: Path, default: Any):
        if not path.exists():
            return default
        value = (
            self.base._read_json(path)
            if hasattr(self.base, "_read_json")
            else json.loads(path.read_text())
        )
        return value

    def state(self) -> dict[str, Any]:
        return dict(self._read(self.state_path, {}))

    def save_state(self, value: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        self.base.atomic_write(self.state_path, payload.encode(), mode=0o600)

    def seen(self, command_id: str) -> bool:
        return command_id in self._read(self.inbox_path, {})

    def record(self, command_id: str, digest: str, state: str) -> None:
        items = dict(self._read(self.inbox_path, {}))
        existing = items.get(command_id)
        if existing and existing.get("digest") != digest:
            raise StoreError("command replay digest mismatch", status=409, code="command_replay")
        items[command_id] = {"digest": digest, "state": state}
        items = dict(list(items.items())[-1000:])
        self.base.atomic_json(self.inbox_path, items)

    def enqueue(self, item: dict[str, Any]) -> None:
        rows = list(self._read(self.outbox_path, []))
        key = item["idempotency_key"]
        rows = [r for r in rows if r.get("idempotency_key") != key]
        rows.append(item)
        if len(rows) > 500:
            raise StoreError("connector outbox is full", status=503, code="connector_outbox_full")
        self.base.atomic_json(self.outbox_path, rows)

    def outbox(self) -> list[dict[str, Any]]:
        return list(self._read(self.outbox_path, []))

    def complete(self, key: str) -> None:
        self.base.atomic_json(
            self.outbox_path, [r for r in self.outbox() if r.get("idempotency_key") != key]
        )
